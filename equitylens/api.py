from datetime import UTC, date, datetime, time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, or_, select, text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .adapters import CATALOG, fetch_public, fetch_sec
from .config import Settings
from .database import make_engine, session_factory
from .ingestion import blob_path, ingest, media_type
from .models import (
    Company,
    Document,
    DocumentVersion,
    EvidenceChunk,
    FinancialFact,
    IngestionRun,
    Listing,
    Source,
)


def create_app(settings=None, factory=None):
    settings = settings or Settings()
    factory = factory or session_factory(make_engine(settings.database_url))
    app = FastAPI(
        title="EquityLens — Evidence Foundation",
        version="0.1.0",
        description="Milestone 1. Local single-user research ingestion. No investment recommendations or AI synthesis.",
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in {
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "http://localhost:8001",
            "http://127.0.0.1:8001",
        }:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Cross-origin requests are not enabled."}, status_code=403)
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > settings.max_upload_bytes + 1024 * 1024):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Upload exceeds the allowed size."}, status_code=413)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_request(_request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(httpx.HTTPError)
    async def source_error(_request, _exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"detail": "The public source could not be retrieved. Retry later or upload the document."},
            status_code=502,
        )

    def company_or_404(session, company_id):
        company = session.get(Company, company_id)
        if not company:
            raise HTTPException(404, "Company not found.")
        return company

    @app.get("/health")
    def health():
        with factory() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "milestone": "evidence-foundation", "database": "connected"}

    @app.get("/companies")
    def companies(q: str = Query("", max_length=100)):
        with factory() as session:
            statement = select(Company).outerjoin(Listing).distinct()
            if q:
                escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                statement = statement.where(
                    or_(
                        Company.name.ilike(f"%{escaped}%", escape="\\"),
                        Listing.ticker.ilike(f"%{escaped}%", escape="\\"),
                        Company.cik == q.zfill(10),
                    )
                )
            rows = session.scalars(statement.order_by(Company.name)).all()
            return [
                {
                    "id": c.id,
                    "name": c.name,
                    "country": c.country,
                    "reporting_currency": c.reporting_currency,
                    "cik": c.cik,
                    "listings": [
                        {
                            "exchange": x.exchange,
                            "ticker": x.ticker,
                            "currency": x.currency,
                            "security_type": x.security_type,
                        }
                        for x in session.scalars(select(Listing).where(Listing.company_id == c.id))
                    ],
                }
                for c in rows
            ]

    @app.get("/sources")
    def sources():
        with factory() as session:
            return [
                {"id": s.id, "name": s.name, "kind": s.kind, "policy": s.policy}
                for s in session.scalars(select(Source))
            ]

    @app.get("/companies/{company_id}/catalog")
    def catalog(company_id: str):
        with factory() as session:
            company = company_or_404(session, company_id)
            return [
                {"key": key, "title": entry.title, "url": entry.url, "period_end": entry.period_end}
                for key, entry in CATALOG.items()
                if entry.cik == company.cik
            ]

    @app.get("/companies/{company_id}/coverage")
    def coverage(company_id: str):
        with factory() as session:
            company_or_404(session, company_id)
            versions = session.scalars(
                select(DocumentVersion).join(Document).where(Document.company_id == company_id)
            ).all()
            fact_count = session.scalar(
                select(func.count()).select_from(FinancialFact).where(FinancialFact.company_id == company_id)
            )
            return {
                "document_versions": len(versions),
                "searchable_versions": sum(v.status == "indexed" for v in versions),
                "financial_observations": fact_count,
                "unknown_publication_dates": sum(v.published_on is None for v in versions),
                "latest_retrieval": max((v.retrieved_at for v in versions), default=None),
                "limitations": [
                    "No automatic financial extraction from PDF/HTML yet.",
                    "SEC observations preserve reported concepts and units; cross-company normalization is pending.",
                    "No news feed, AI analysis, price feed, OCR, or automated monitoring in milestone 1.",
                    "Curated sources and uploads do not imply complete company coverage.",
                ],
            }

    @app.post("/companies/{company_id}/imports/catalog/{key}")
    def import_catalog(company_id: str, key: str):
        with factory() as session:
            company = company_or_404(session, company_id)
            if key not in CATALOG or CATALOG[key].cik != company.cik:
                raise HTTPException(422, "This document does not belong to the selected company.")
        item, content, _ = fetch_public(key, settings)
        kind = media_type(content, urlsplit(item.url).path)
        return ingest(
            factory,
            settings,
            company_id=company_id,
            source_id="company-ir",
            source_key=item.url,
            title=item.title,
            content=content,
            kind=kind,
            source_url=item.url,
            period_end=item.period_end,
        )

    @app.post("/companies/{company_id}/imports/sec")
    def import_sec(company_id: str):
        with factory() as session:
            company = company_or_404(session, company_id)
            cik, name = company.cik, company.name
        if not cik:
            raise ValueError("This company has no SEC identifier.")
        url, content = fetch_sec(cik, settings)
        return ingest(
            factory,
            settings,
            company_id=company_id,
            source_id="sec-edgar",
            source_key=url,
            title=f"{name} — SEC company facts",
            content=content,
            kind="application/json",
            source_url=url,
        )

    @app.post("/companies/{company_id}/imports/upload")
    def upload(
        company_id: str,
        file: UploadFile = File(...),
        title: str = Form(..., max_length=300),
        document_key: str = Form(..., max_length=300),
        source_url: str | None = Form(None),
        published_on: date | None = Form(None),
        period_end: date | None = Form(None),
    ):
        if source_url:
            parsed = urlsplit(source_url)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or len(source_url) > 1200
            ):
                raise ValueError("Source link must be a valid HTTP or HTTPS URL without credentials.")
        with factory() as session:
            company_or_404(session, company_id)
        content = file.file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "Maximum upload size is 25 MB.")
        kind = media_type(content, file.filename or "")
        return ingest(
            factory,
            settings,
            company_id=company_id,
            source_id="user-upload",
            source_key=document_key,
            title=title,
            content=content,
            kind=kind,
            source_url=source_url,
            published_on=published_on,
            period_end=period_end,
            publication_basis="user_provided" if published_on else "unknown",
        )

    @app.get("/companies/{company_id}/documents")
    def documents(company_id: str):
        with factory() as session:
            company_or_404(session, company_id)
            records = session.execute(
                select(DocumentVersion, Document.source_id)
                .join(Document)
                .where(Document.company_id == company_id)
                .order_by(DocumentVersion.retrieved_at.desc())
            ).all()
            return [
                {
                    "id": v.id,
                    "document_id": v.document_id,
                    "title": v.title,
                    "source_id": source_id,
                    "status": v.status,
                    "sha256": v.sha256,
                    "published_on": v.published_on,
                    "publication_basis": v.publication_basis,
                    "period_end": v.period_end,
                    "retrieved_at": v.retrieved_at,
                    "source_url": v.source_url,
                    "warning": v.warning,
                    "parser_version": v.parser_version,
                }
                for v, source_id in records
            ]

    @app.get("/documents/{version_id}/chunks")
    def chunks(version_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        with factory() as session:
            if not session.get(DocumentVersion, version_id):
                raise HTTPException(404, "Document version not found.")
            rows = session.scalars(
                select(EvidenceChunk)
                .where(EvidenceChunk.version_id == version_id)
                .order_by(EvidenceChunk.ordinal)
                .offset(offset)
                .limit(limit)
            )
            return [{"id": c.id, "locator": c.locator, "text": c.text} for c in rows]

    @app.get("/documents/{version_id}/original")
    def original(version_id: str):
        with factory() as session:
            v = session.get(DocumentVersion, version_id)
            if not v:
                raise HTTPException(404, "Document version not found.")
            path = blob_path(settings, v.sha256)
            suffix = {
                "application/pdf": ".pdf",
                "text/html": ".html",
                "application/json": ".json",
                "text/plain": ".txt",
            }[v.media_type]
        if not path.exists():
            raise HTTPException(503, "Original source object is missing.")
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=f"equitylens-{version_id}{suffix}",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/companies/{company_id}/evidence")
    def search_evidence(
        company_id: str,
        q: str = Query(..., min_length=2, max_length=300),
        limit: int = Query(15, ge=1, le=50),
        as_of: date | None = None,
    ):
        with factory() as session:
            company_or_404(session, company_id)
            statement = (
                select(EvidenceChunk, DocumentVersion)
                .join(DocumentVersion)
                .join(Document)
                .where(Document.company_id == company_id)
            )
            if session.bind.dialect.name == "postgresql":
                vector = func.to_tsvector("english", EvidenceChunk.text)
                query = func.plainto_tsquery("english", q)
                statement = statement.where(vector.op("@@")(query)).order_by(
                    func.ts_rank(vector, query).desc()
                )
            else:
                escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                statement = statement.where(EvidenceChunk.text.ilike(f"%{escaped}%", escape="\\"))
            if as_of:
                # Strict observed-as-of mode: do not backdate material first ingested later.
                cutoff = datetime.combine(as_of, time.max, tzinfo=UTC)
                statement = statement.where(
                    and_(DocumentVersion.published_on <= as_of, DocumentVersion.retrieved_at <= cutoff)
                )
            rows = session.execute(statement.limit(limit)).all()
            return [
                {
                    "chunk_id": c.id,
                    "version_id": v.id,
                    "title": v.title,
                    "locator": c.locator,
                    "text": c.text,
                    "source_url": v.source_url,
                    "published_on": v.published_on,
                    "retrieved_at": v.retrieved_at,
                    "publication_basis": v.publication_basis,
                }
                for c, v in rows
            ]

    @app.get("/companies/{company_id}/facts")
    def facts(company_id: str, concept: str | None = None, limit: int = Query(100, ge=1, le=500)):
        with factory() as session:
            company_or_404(session, company_id)
            statement = select(FinancialFact).where(FinancialFact.company_id == company_id)
            if concept:
                statement = statement.where(FinancialFact.concept == concept)
            rows = session.scalars(
                statement.order_by(FinancialFact.period_end.desc(), FinancialFact.filed_on.desc()).limit(
                    limit
                )
            )
            return [
                {
                    "id": r.id,
                    "version_id": r.version_id,
                    "concept": r.concept,
                    "taxonomy": r.taxonomy,
                    "value": str(r.value),
                    "unit": r.unit,
                    "period_start": r.period_start,
                    "period_end": r.period_end,
                    "filed_on": r.filed_on,
                    "accession": r.accession,
                    "form": r.form,
                    "locator": r.locator,
                    "metadata": r.metadata_json,
                }
                for r in rows
            ]

    @app.get("/companies/{company_id}/runs")
    def runs(company_id: str):
        with factory() as session:
            company_or_404(session, company_id)
            return [
                {
                    "id": r.id,
                    "status": r.status,
                    "version_id": r.version_id,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "error": r.error,
                }
                for r in session.scalars(
                    select(IngestionRun)
                    .where(IngestionRun.company_id == company_id)
                    .order_by(IngestionRun.started_at.desc())
                    .limit(30)
                )
            ]

    return app
