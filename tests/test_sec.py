import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from equitylens.config import Settings
from equitylens.live.sec import fetch_sec_filings, reconcile
from equitylens.live.service import ResearchService
from equitylens.live.types import CompanyMatch


def company(market="US"):
    return CompanyMatch(
        symbol="TSLA", name="Tesla, Inc.", exchange="NASDAQ", market=market, retrieved_at=datetime.now(UTC)
    )


def test_sec_is_disabled_without_contact_and_for_india():
    def unexpected(request):
        raise AssertionError(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(unexpected)
    assert asyncio.run(fetch_sec_filings(company(), "EquityLens", transport)) is None
    assert asyncio.run(fetch_sec_filings(company("IN"), "EquityLens contact@example.com", transport)) is None


def test_sec_annual_filing_identity_provenance_and_amount_comparison():
    requests = []
    facts = {
        "cik": 1318605,
        "entityName": "Tesla, Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01", "form": "10-K", "val": 100, "accn": "0001318605-25-000001"},
                            {"start": "2025-01-01", "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "val": 120, "accn": "0001318605-26-000001"},
                            {"start": "2026-01-01", "end": "2026-03-31", "filed": "2026-04-01", "form": "10-Q", "val": 1000, "accn": "0001318605-26-000002"},
                        ]
                    }
                }
            }
        },
    }

    def respond(request):
        requests.append(request)
        assert request.headers["User-Agent"] == "EquityLens contact@example.com"
        if request.url.path == "/files/company_tickers.json":
            return httpx.Response(200, json={"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."}})
        if request.url.path == "/api/xbrl/companyfacts/CIK0001318605.json":
            return httpx.Response(200, json=facts)
        raise AssertionError(str(request.url))

    result = asyncio.run(fetch_sec_filings(company(), "EquityLens contact@example.com", httpx.MockTransport(respond)))
    assert len(requests) == 2
    assert result["facts"][0]["value"] == "120"
    assert result["evidence"][0].id == "SEC1"
    assert result["evidence"][0].url.endswith("0001318605-26-000001-index.html")
    assert "us-gaap:Revenues" in result["evidence"][0].content
    comparison = reconcile(
        [{"id": "F1", "metric": "TotalRevenue", "value": "120", "currency": "USD", "period_type": "12M", "period_end": "2025-12-31"}],
        result["facts"],
    )
    assert comparison[0]["status"] == "amount_matches"
    assert comparison[0]["provider_id"] == "F1"


def test_sec_rejects_companyfacts_with_wrong_cik():
    def respond(request):
        if request.url.path == "/files/company_tickers.json":
            return httpx.Response(200, json={"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla"}})
        return httpx.Response(200, json={"cik": 999, "facts": {}})

    with pytest.raises(ValueError, match="identity"):
        asyncio.run(fetch_sec_filings(company(), "EquityLens contact@example.com", httpx.MockTransport(respond)))


def test_us_research_collects_official_facts_and_reconciles():
    class Sources:
        async def stock(self, selected):
            return None

        async def financials(self, selected):
            return [{"id": "", "metric": "TotalRevenue", "label": "Revenue", "value": "120", "currency": "USD", "period_end": "2025-12-31", "period_type": "12M"}]

        async def news(self, selected, days, category, limit):
            return []

        async def profile(self, selected, key):
            return None

        async def sec_filings(self, selected):
            fact = {"metric": "TotalRevenue", "label": "Revenue", "value": "120", "currency": "USD", "period_end": "2025-12-31", "period_type": "12M", "concept": "Revenues", "url": "https://www.sec.gov/example"}
            return {"facts": [fact], "evidence": []}

    settings = Settings(sec_user_agent="EquityLens contact@example.com", _env_file=None)
    service = ResearchService(settings, Sources())
    state = {"company": company(), "days": 7, "depth": "quick", "eodhd": "", "tavily": ""}
    collected = asyncio.run(service.collect(state))["collected"]
    assert collected["filings"]["facts"][0]["value"] == "120"
    validated = service.validate({"collected": collected})
    assert reconcile(validated["rows"], collected["filings"]["facts"])[0]["status"] == "amount_matches"
