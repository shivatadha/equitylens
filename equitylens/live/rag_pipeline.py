import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .providers import utcnow
from .rag import LocalEmbedder, SessionIndex, chunk_blocks, tokens
from .reader import PublicReader
from .stock import stock_evidence
from .types import Evidence


def scaled_financial_amount(row):
    """Offer an exact, labelled display conversion alongside the provider's base amount."""
    scale, label = (Decimal("10000000"), "crore") if row["currency"] == "INR" else (
        Decimal("1000000"), "million"
    ) if row["currency"] == "USD" else (None, None)
    if scale is None:
        return ""
    try:
        value = Decimal(str(row["value"])) / scale
    except (InvalidOperation, ValueError):
        return ""
    display = format(value, "f")
    whole, dot, fractional = display.partition(".")
    grouped = f"{int(whole):,}" + (dot + fractional if dot else "")
    return f" Exact display conversion: {grouped} {row['currency']} {label} (from provider amount)."


def as_context(passages):
    return [
        Evidence(
            id=p["id"],
            title=p["title"],
            url=p["url"],
            publisher=p["publisher"],
            published_at=p["published_at"],
            retrieved_at=p["retrieved_at"],
            category=p["category"],
            categories=[p["category"]],
            content=p["text"],
            evidence_level=p["evidence_level"],
            locator=p["locator"],
        )
        for p in passages
    ]


class RagPipeline:
    def __init__(self, settings, embedder=None, reader=None):
        self.settings = settings
        self.embedder = embedder or LocalEmbedder(settings)
        self.reader = reader or PublicReader()

    async def build(self, company, evidence, rows, profile, depth, *, stock=None):
        sources = list(evidence)
        if stock:
            sources.extend(stock_evidence(company, stock))
        for row in rows:
            sources.append(
                Evidence(
                    id=row["id"],
                    title=f"{company.name} — {row['label']}",
                    url=row["source_url"],
                    publisher=row["provider"] if "provider" in row else "Financial data provider",
                    retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
                    category="financial",
                    categories=["financial"],
                    evidence_level="structured_fact",
                    content=f"{company.name}. {row['label']}: {row['value']} {row['currency']}. Reporting period {row['period_type']}, ended {row['period_end']}.{scaled_financial_amount(row)} {row['basis']}. Observation {row['id']}.",
                )
            )
        if profile and profile.get("description"):
            sources.append(
                Evidence(
                    id="P1",
                    title=company.name + " — business profile",
                    url=profile["source_url"],
                    publisher=profile["provider"],
                    retrieved_at=profile["retrieved_at"],
                    category="profile",
                    categories=["profile"],
                    evidence_level="provider_profile",
                    content=profile["description"],
                )
            )
        candidates = [
            e for e in evidence if e.evidence_level in {"search_excerpt", "official_feed_summary"}
        ]
        candidates.sort(key=lambda item: item.evidence_level != "official_feed_summary")
        if profile and profile.get("website"):
            website = Evidence(
                id="W1",
                title=company.name + " — company website",
                url=profile["website"],
                publisher=company.name,
                retrieved_at=utcnow(),
                category="official_website",
                categories=["company_website"],
                evidence_level="page_excerpt",
            )
            sources.append(website)
            candidates.insert(0, website)
        max_sources = min(self.settings.rag_read_max_sources, 2 if depth == "quick" else 4)
        tasks = {asyncio.create_task(self.reader.read(source)): source for source in candidates[:max_sources]}
        reads = []
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=28)
            for task in done:
                try:
                    reads.append(task.result())
                except (ValueError, TypeError, OSError):
                    reads.append(
                        {
                            "source_id": tasks[task].id,
                            "status": "unavailable",
                            "blocks": [],
                            "notice": "Source extraction unavailable.",
                        }
                    )
            for task in pending:
                task.cancel()
                reads.append(
                    {
                        "source_id": tasks[task].id,
                        "status": "budget_exhausted",
                        "blocks": [],
                        "notice": "Source reading budget exhausted.",
                    }
                )
            await asyncio.gather(*pending, return_exceptions=True)
        readings = {r["source_id"]: r for r in reads}
        passages = []
        for source in sources:
            reading = readings.get(source.id)
            if reading and reading["blocks"]:
                level = "pdf_excerpt" if reading["blocks"][0]["locator"].startswith("PDF") else "page_excerpt"
                passages.extend(chunk_blocks(source, reading["blocks"], level=level))
            elif source.evidence_level == "headline":
                passages.extend(chunk_blocks(source, [{"text": source.title, "locator": "Headline only"}]))
            elif source.content:
                location = (
                    "Dated provider observation or calculation"
                    if source.evidence_level == "structured_fact"
                    else "Provider profile"
                    if source.evidence_level == "provider_profile"
                    else "Official exchange RSS summary"
                    if source.evidence_level == "official_feed_summary"
                    else "Search excerpt"
                )
                passages.extend(
                    chunk_blocks(source, [{"text": source.content, "locator": source.locator or location}])
                )
        # Prefer a balanced corpus rather than letting the first large page consume the cap.
        balanced = []
        by_source = {}
        for passage in passages:
            by_source.setdefault(passage.source_id, []).append(passage)
        for position in range(35):
            for group in by_source.values():
                if position < len(group) and len(balanced) < 250:
                    balanced.append(group[position])
        index = await asyncio.to_thread(
            SessionIndex, balanced, self.embedder, tokens(company.name + " " + company.symbol)
        )
        context = await asyncio.to_thread(index.report_context)
        summary = {
            **index.summary(),
            "selected_passages": context,
            "source_reads": [{k: v for k, v in reading.items() if k != "blocks"} for reading in reads],
            "body_sources_read": sum(r["status"] == "read" for r in reads),
            "retrieval": "Semantic cosine + BM25, reciprocal-rank fusion, MMR diversity; 2 passages per source, bounded context.",
            "source_limits": "Headlines, official feed summaries, excerpts, provider profiles and reported financial observations retain their original evidence level.",
        }
        return {"index": index, "summary": summary, "context": as_context(context), "sources": sources}
