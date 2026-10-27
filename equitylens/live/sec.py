"""Request-time, entity-wide SEC XBRL facts with filing provenance."""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from .providers import request, utcnow
from .types import Evidence

CONCEPTS = {
    "TotalRevenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    "OperatingIncome": ("OperatingIncomeLoss",),
    "NetIncome": ("NetIncomeLoss",),
    "OperatingCashFlow": ("NetCashProvidedByUsedInOperatingActivities",),
}
LABELS = {
    "TotalRevenue": "Revenue",
    "OperatingIncome": "Operating income",
    "NetIncome": "Net income",
    "OperatingCashFlow": "Operating cash flow",
}


def configured(user_agent):
    """SEC requests require an identifiable contact, not the generic app UA."""
    return bool(re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", user_agent or ""))


def annual_facts(payload, cik):
    if int(payload.get("cik", -1)) != cik:
        raise ValueError("SEC companyfacts identity did not match the ticker directory.")
    results = []
    facts = payload.get("facts", {}).get("us-gaap", {})
    for metric, concepts in CONCEPTS.items():
        candidates = []
        for priority, concept in enumerate(concepts):
            for item in facts.get(concept, {}).get("units", {}).get("USD", []):
                try:
                    start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
                    filed = date.fromisoformat(item["filed"])
                    accession = item["accn"]
                    value = Decimal(str(item["val"]))
                    if (
                        item.get("form") != "10-K"
                        or not 300 <= (end - start).days <= 380
                        or end > utcnow().date()
                        or not value.is_finite()
                        or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)
                    ):
                        continue
                    candidates.append((end, filed, -priority, concept, value, accession, start))
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    continue
        if candidates:
            best = max(candidates, key=lambda item: item[:3])
            same_context = [item for item in candidates if item[:3] == best[:3]]
            if len({item[4] for item in same_context}) != 1:
                continue  # Ambiguous values for the same selected period and filing date.
            end, filed, _, concept, value, accession, start = best
            results.append(
                {
                    "metric": metric,
                    "label": LABELS[metric],
                    "value": str(value),
                    "currency": "USD",
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "period_type": "12M",
                    "filed": filed.isoformat(),
                    "accession": accession,
                    "concept": concept,
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.html",
                }
            )
    return results


async def fetch_sec_filings(company, user_agent, transport=None):
    if company.market != "US" or not configured(user_agent):
        return None
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(22, connect=8),
        follow_redirects=False,
        headers={"User-Agent": user_agent.strip(), "Accept-Encoding": "gzip, deflate"},
        transport=transport,
    ) as client:
        directory = json.loads(await request(client, "GET", "https://www.sec.gov/files/company_tickers.json"))
        symbol = company.symbol.upper().replace(".", "-")
        match = next(
            (
                item for item in directory.values()
                if isinstance(item, dict) and str(item.get("ticker", "")).upper() == symbol
            ),
            None,
        )
        if not match:
            return {"facts": [], "evidence": []}
        cik = int(match["cik_str"])
        payload = json.loads(
            await request(client, "GET", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010}.json")
        )
    facts = annual_facts(payload, cik)
    evidence = [
        Evidence(
            id=f"SEC{i}",
            title=f"{match['title']} — {fact['label']} (10-K)",
            url=fact["url"],
            publisher="U.S. Securities and Exchange Commission",
            published_at=fact["filed"] + "T00:00:00Z",
            retrieved_at=utcnow(),
            category="filing",
            categories=["filing", "financial"],
            evidence_level="structured_fact",
            locator=f"10-K {fact['accession']}; us-gaap:{fact['concept']}",
            content=(
                f"{match['title']}. SEC entity-wide XBRL {fact['label']}: {fact['value']} USD. "
                f"Reporting period 12M, {fact['period_start']} to {fact['period_end']}. "
                f"Filed {fact['filed']}; 10-K accession {fact['accession']}; "
                f"us-gaap:{fact['concept']}. Reporting basis and concept equivalence require review."
            ),
        )
        for i, fact in enumerate(facts, 1)
    ]
    return {"facts": facts, "evidence": evidence}


def reconcile(provider_rows, sec_facts):
    results = []
    for i, fact in enumerate(sec_facts, 1):
        row = next(
            (
                item for item in provider_rows
                if item["metric"] == fact["metric"]
                and item["period_end"] == fact["period_end"]
                and item["period_type"] == "12M"
                and item["currency"] == "USD"
            ),
            None,
        )
        status = "no_matching_provider_period"
        difference = None
        if row:
            try:
                difference = str(Decimal(row["value"]) - Decimal(fact["value"]))
                status = "amount_matches" if Decimal(difference) == 0 else "amount_differs"
            except (InvalidOperation, ValueError):
                status = "provider_value_invalid"
        results.append(
            {
                "metric": fact["metric"],
                "period_end": fact["period_end"],
                "status": status,
                "provider_id": row["id"] if row else None,
                "provider_value": row["value"] if row else None,
                "sec_value": fact["value"],
                "currency": "USD",
                "difference_provider_minus_sec": difference,
                "sec_source_id": f"SEC{i}",
                "concept": fact["concept"],
                "filing_url": fact["url"],
                "note": "Amount comparison only; accounting concept and reporting basis need manual review.",
            }
        )
    return results
