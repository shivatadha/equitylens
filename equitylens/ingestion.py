import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import ROOT
from .facts import normalize_companyfacts
from .models import (
    Company,
    Document,
    DocumentVersion,
    EvidenceChunk,
    FinancialFact,
    IngestionRun,
    Source,
    now,
)


def media_type(content, filename):
    suffix = Path(filename).suffix.lower()
    if content.startswith(b"%PDF-") and suffix == ".pdf":
        return "application/pdf"
    if suffix in {".html", ".htm"} and b"<" in content[:2000]:
        return "text/html"
    if suffix == ".txt":
        content.decode("utf-8-sig")
        return "text/plain"
    raise ValueError("Upload a PDF, HTML, or UTF-8 text document with a matching extension.")


def blob_path(settings, digest):
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid content identifier.")
    return settings.storage / "objects" / digest[:2] / digest


def store_blob(settings, content, digest):
    path = blob_path(settings, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Stored source failed its integrity check.")
        return path
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
    return path


def ingest(
    factory,
    settings,
    *,
    company_id,
    source_id,
    source_key,
    title,
    content,
    kind,
    source_url=None,
    published_on=None,
    period_end=None,
    publication_basis="unknown",
    extractor=None,
):
    if not content or len(content) > settings.max_upload_bytes:
        raise ValueError("Document is empty or exceeds the 25 MB import limit.")
    if not source_key or len(source_key) > 1200 or not title or len(title) > 300:
        raise ValueError("Invalid document title or stable source key.")
    if isinstance(published_on, str):
        published_on = date.fromisoformat(published_on)
    with factory() as session:
        company = session.get(Company, company_id)
        if not company or not session.get(Source, source_id):
            raise ValueError("Unknown company or source.")
        cik = company.cik
        run = IngestionRun(company_id=company_id, status="processing")
        session.add(run)
        session.commit()
        run_id = run.id
    digest = hashlib.sha256(content).hexdigest()
    try:
        path = store_blob(settings, content, digest)
        with factory() as session:
            existing = session.scalar(
                select(DocumentVersion)
                .join(Document)
                .where(
                    Document.company_id == company_id,
                    Document.source_id == source_id,
                    Document.source_key == source_key,
                    DocumentVersion.sha256 == digest,
                )
            )
            if existing:
                run = session.get(IngestionRun, run_id)
                run.status, run.version_id, run.finished_at = "duplicate", existing.id, now()
                session.commit()
                return {"run_id": run_id, "version_id": existing.id, "status": "duplicate"}
        facts = []
        if kind == "application/json" and source_id == "sec-edgar":
            facts, rejected = normalize_companyfacts(content, cik)
            extracted = {
                "chunks": [],
                "parser": "sec-companyfacts-v1",
                "warning": f"{rejected} malformed observations skipped." if rejected else None,
            }
            status = "structured" if facts else "needs_review"
            if not facts:
                extracted["warning"] = "No supported financial concepts found. Source retained for review."
        else:
            if extractor:
                extracted = extractor(content, kind)
            else:
                result = subprocess.run(
                    [sys.executable, "-m", "equitylens.parser", str(path), kind],
                    cwd=ROOT,
                    capture_output=True,
                    timeout=settings.extraction_timeout_seconds,
                )
                if result.returncode:
                    raise ValueError(result.stderr.decode("utf-8", errors="replace")[-1200:])
                extracted = json.loads(result.stdout)
            status = "indexed" if extracted["chunks"] else "needs_review"
        with factory() as session:
            document = session.scalar(
                select(Document).where(
                    Document.company_id == company_id,
                    Document.source_id == source_id,
                    Document.source_key == source_key,
                )
            )
            if not document:
                document = Document(company_id=company_id, source_id=source_id, source_key=source_key)
                session.add(document)
                session.flush()
            version = DocumentVersion(
                document_id=document.id,
                title=title,
                sha256=digest,
                size_bytes=len(content),
                media_type=kind,
                published_on=published_on,
                publication_basis=publication_basis,
                period_end=period_end,
                source_url=source_url,
                parser_version=extracted["parser"],
                status=status,
                warning=extracted["warning"],
            )
            session.add(version)
            session.flush()
            for i, chunk in enumerate(extracted["chunks"]):
                session.add(EvidenceChunk(version_id=version.id, ordinal=i, **chunk))
            keys = (
                set(
                    session.scalars(
                        select(FinancialFact.identity_hash).where(FinancialFact.company_id == company_id)
                    )
                )
                if facts
                else set()
            )
            added = 0
            for fact in facts:
                if fact["identity_hash"] not in keys:
                    session.add(FinancialFact(company_id=company_id, version_id=version.id, **fact))
                    keys.add(fact["identity_hash"])
                    added += 1
            run = session.get(IngestionRun, run_id)
            run.status, run.version_id, run.finished_at = status, version.id, now()
            session.commit()
            return {
                "run_id": run_id,
                "version_id": version.id,
                "status": status,
                "chunks": len(extracted["chunks"]),
                "facts_added": added,
                "warning": extracted["warning"],
            }
    except IntegrityError:
        # Another request may have imported identical content while this one parsed it.
        with factory() as session:
            existing = session.scalar(
                select(DocumentVersion)
                .join(Document)
                .where(
                    Document.company_id == company_id,
                    Document.source_id == source_id,
                    Document.source_key == source_key,
                    DocumentVersion.sha256 == digest,
                )
            )
            run = session.get(IngestionRun, run_id)
            run.finished_at = now()
            if existing:
                run.status, run.version_id = "duplicate", existing.id
                session.commit()
                return {"run_id": run_id, "version_id": existing.id, "status": "duplicate"}
            run.status, run.error = "failed", "Concurrent import conflicted. Retry this document."
            session.commit()
        raise ValueError("Concurrent import conflicted. Retry this document.") from None
    except Exception as exc:
        with factory() as session:
            run = session.get(IngestionRun, run_id)
            run.status, run.finished_at = "failed", now()
            run.error = str(exc)[:1200]
            session.commit()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise ValueError("Document extraction exceeded the time limit.") from exc
        raise
