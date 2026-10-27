import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from equitylens.config import Settings
from equitylens.embedded_client import EmbeddedClient
from equitylens.live.providers import PublicSources, safe_link
from equitylens.live.service import (
    CompanyTokens,
    ResearchService,
    SessionStore,
    calculate,
    deduplicate,
    direct_financial_observation,
    generation_failure,
    reporting_basis_gap,
    validate_analysis,
)
from equitylens.live.types import CompanyMatch, Evidence
from equitylens.live_api import create_app


def company(symbol="TSLA", name="Tesla, Inc."):
    return CompanyMatch(
        symbol=symbol,
        name=name,
        exchange="NASDAQ",
        market="US",
        industry="Auto Manufacturers",
        retrieved_at=datetime.now(UTC),
    )


def evidence(title="Tesla factory expansion", category="news", url="https://example.com/story"):
    return Evidence(
        title=title,
        url=url,
        publisher="Example",
        published_at=datetime.now(UTC),
        retrieved_at=datetime.now(UTC),
        category=category,
    )


def financial(metric, value, end="2025-12-31", currency="USD", identifier="F1"):
    return {
        "id": identifier,
        "metric": metric,
        "label": metric,
        "value": value,
        "currency": currency,
        "period_end": end,
        "period_type": "12M",
        "source_url": "https://example.com/financials",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "basis": "provider aggregate",
    }


def test_dynamic_lookup_filters_wrong_markets_and_instruments():
    payload = {
        "quotes": [
            {"symbol": "TSLA", "quoteType": "EQUITY", "shortname": "Tesla, Inc.", "exchange": "NMS"},
            {
                "symbol": "RELIANCE.NS",
                "quoteType": "EQUITY",
                "shortname": "Reliance Industries",
                "exchange": "NSI",
            },
            {"symbol": "INDEX", "quoteType": "INDEX", "exchange": "NMS"},
            {"symbol": "OTHER", "quoteType": "EQUITY", "exchange": "LSE"},
        ]
    }
    provider = PublicSources(httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    assert [c.symbol for c in asyncio.run(provider.lookup("arbitrary", "IN"))] == ["RELIANCE.NS"]
    assert [c.symbol for c in asyncio.run(provider.lookup("arbitrary", "US"))] == ["TSLA"]


def test_tokens_are_signed_and_expire():
    clock = [0]
    tokens = CompanyTokens(clock=lambda: clock[0])
    token = tokens.issue(company())
    assert tokens.verify(token).symbol == "TSLA"
    with pytest.raises(ValueError):
        tokens.verify(token + "0")
    clock[0] = 901
    with pytest.raises(ValueError, match="expired"):
        tokens.verify(token)


def test_sessions_expire_and_require_correct_secret():
    clock = [0]
    store = SessionStore(ttl=10, clock=lambda: clock[0])
    sid, token = store.create({"company": "temporary"})
    assert store.get(sid, token)["company"] == "temporary"
    with pytest.raises(KeyError):
        store.get(sid, "another-user")
    clock[0] = 11
    with pytest.raises(KeyError):
        store.get(sid, token)
    assert not store._items


def test_clear_session_is_effective():
    store = SessionStore()
    sid, token = store.create({})
    store.delete(sid, token)
    with pytest.raises(KeyError):
        store.get(sid, token)


def test_news_dates_are_strictly_filtered():
    now = datetime.now(UTC)
    dates = [now - timedelta(hours=2), now - timedelta(days=40), now + timedelta(days=2)]
    items = "".join(
        f"<item><title>Story {i}</title><link>https://example.com/{i}</link><pubDate>{format_datetime(d)}</pubDate><source>Publisher</source></item>"
        for i, d in enumerate(dates)
    )
    xml = ("<rss><channel>" + items + "<item><title>Undated</title></item></channel></rss>").encode()
    provider = PublicSources(httpx.MockTransport(lambda r: httpx.Response(200, content=xml)))
    results = asyncio.run(provider.news(company(), 7))
    assert len(results) == 1
    assert results[0].title == "Story 0"
    assert results[0].evidence_level == "headline"
    assert results[0].content == ""


def test_xml_entities_rejected():
    xml = b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss/>'
    provider = PublicSources(httpx.MockTransport(lambda r: httpx.Response(200, content=xml)))
    with pytest.raises(ValueError, match="XML"):
        asyncio.run(provider.news(company(), 7))


def test_financial_context_and_precision_preserved():
    payload = {
        "timeseries": {
            "result": [
                {
                    "annualTotalRevenue": [
                        {
                            "asOfDate": "2025-12-31",
                            "periodType": "12M",
                            "currencyCode": "USD",
                            "reportedValue": {"raw": "123456789.12345678"},
                        },
                        {
                            "asOfDate": "2025-12-31",
                            "periodType": "3M",
                            "currencyCode": "USD",
                            "reportedValue": {"raw": "10"},
                        },
                    ]
                }
            ]
        }
    }
    provider = PublicSources(httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    rows = asyncio.run(provider.financials(company()))
    assert len(rows) == 1
    assert Decimal(rows[0]["value"]) == Decimal("123456789.12345678")
    assert rows[0]["period_type"] == "12M"
    assert rows[0]["currency"] == "USD"


def test_calculations_reconcile_and_do_not_mix_currencies():
    rows = [
        financial("TotalRevenue", "150"),
        financial("TotalRevenue", "100", "2024-12-31", identifier="F2"),
        financial("OperatingIncome", "30", identifier="F3"),
    ]
    values = calculate(rows)
    assert values[0]["value"] == "50.00"
    assert values[1]["value"] == "20.00"
    rows[1]["currency"] = "INR"
    assert len(calculate(rows)) == 1
    rows[2]["currency"] = "INR"
    assert calculate(rows) == []


def test_missing_year_is_not_reported_as_yoy_growth():
    assert calculate([financial("TotalRevenue", "150"), financial("TotalRevenue", "100", "2023-12-31")]) == []


def test_dedup_retains_categories_without_counting_story_twice():
    items = deduplicate([evidence(), evidence(category="projects")])
    assert len(items) == 1
    assert items[0].categories == ["news", "projects"]


def test_invalid_ai_citations_are_removed():
    item = evidence()
    item.id = "S1"
    sections, dropped = validate_analysis(
        {
            "sections": [
                {
                    "title": "Outlook",
                    "claims": [
                        {"text": "Supported reference", "source_ids": ["S1"]},
                        {"text": "Invented reference", "source_ids": ["S99"]},
                        {"text": "No reference", "source_ids": []},
                    ],
                }
            ]
        },
        [item],
    )
    assert len(sections[0]["claims"]) == 1
    assert dropped == 2


def test_generated_figures_must_appear_in_cited_evidence():
    item = evidence()
    item.id = "S1"
    item.title = "Annual filing"
    item.content = "Revenue for 2025 was INR 1,200 crore."
    sections, dropped = validate_analysis(
        {
            "sections": [
                {
                    "title": "Answer",
                    "claims": [
                        {"text": "Revenue in 2025 was INR 1,200 crore.", "source_ids": ["S1"]},
                        {"text": "Revenue in 2025 was INR 1,500 crore.", "source_ids": ["S1"]},
                        {"text": "Revenue in 2024 was INR 1,200 crore.", "source_ids": ["S1"]},
                        {"text": "Revenue in 2025 was USD 1,200 crore.", "source_ids": ["S1"]},
                        {"text": "Revenue in 2025 was INR 1,200 million.", "source_ids": ["S1"]},
                    ],
                }
            ]
        },
        [item],
    )
    assert [claim["text"] for claim in sections[0]["claims"]] == ["Revenue in 2025 was INR 1,200 crore."]
    assert dropped == 4


def test_unverified_provider_basis_cannot_be_claimed_as_consolidated():
    item = evidence()
    item.id = "F30:C001"
    item.content = (
        "Operating cash flow: 1.78703E+12 INR. Reporting period 12M, ended 2025-03-31. "
        "Provider aggregate; consolidated/standalone basis not independently verified."
    )
    sections, dropped = validate_analysis(
        {
            "sections": [
                {
                    "title": "Answer",
                    "claims": [
                        {
                            "text": "Consolidated operating cash flow was 1.78703E+12 INR in 2025.",
                            "source_ids": ["F30:C001"],
                        },
                        {
                            "text": "Provider operating cash flow was 1.78703E+12 INR in 2025; the basis is unverified.",
                            "source_ids": ["F30:C001"],
                        },
                    ],
                }
            ]
        },
        [item],
    )
    assert dropped == 1
    assert sections[0]["claims"][0]["text"].startswith("Provider operating cash flow")


def test_reporting_basis_gap_has_cited_abstention():
    matches = [
        {
            "id": "F30:C001",
            "text": "Operating cash flow, provider aggregate; consolidated/standalone basis not independently verified.",
        }
    ]
    answer = reporting_basis_gap("What was consolidated operating cash flow?", matches)
    assert answer[0]["claims"][0]["source_ids"] == ["F30:C001"]
    assert reporting_basis_gap("What was operating cash flow?", matches) == []


def test_direct_financial_observation_uses_exact_conversion_only():
    matches = [
        {
            "id": "F43:C001",
            "category": "financial",
            "title": "Microsoft — Revenue",
            "text": "Revenue: 2.81724E+11 USD. Reporting period 12M, ended 2025-06-30. "
            "Exact display conversion: 281,724 USD million (from provider amount). "
            "Provider aggregate; consolidated/standalone basis not independently verified.",
        }
    ]
    answer = direct_financial_observation("What was total revenue in fiscal 2025?", matches)
    assert "281,724 USD million" in answer[0]["claims"][0]["text"]
    assert answer[0]["claims"][0]["source_ids"] == ["F43:C001"]
    assert direct_financial_observation("What was consolidated revenue in 2025?", matches) == []
    assert direct_financial_observation("What was revenue after GST in 2025?", matches) == []
    assert direct_financial_observation("What was total revenue in INR crore in 2025?", matches) == []


def test_generation_failures_have_specific_safe_messages():
    request = httpx.Request("POST", "https://provider.example/chat/completions")
    limited = httpx.HTTPStatusError(
        "secret-query-token", request=request, response=httpx.Response(429, request=request)
    )
    code, message = generation_failure(limited)
    assert code == "rate_limit"
    assert "rate limit" in message
    assert "secret-query-token" not in message


def test_equivalent_period_and_date_punctuation_do_not_drop_supported_cash_flow():
    item = evidence()
    item.id = "F12:C001"
    item.title = "Operating cash flow"
    item.content = (
        "Operating cash flow: 1.92113E+12 INR. Reporting period 12M, ended 2026-03-31."
    )
    sections, dropped = validate_analysis(
        {
            "sections": [
                {
                    "title": "Answer",
                    "claims": [
                        {
                            "text": "Cash flow was 1.92113E+12 INR for the 12‑month period ended 2026‑03‑31.",
                            "source_ids": ["F12:C001"],
                        },
                        {
                            "text": "Cash flow was 1.92113E+12 INR for the 3‑month period ended 2026‑03‑31.",
                            "source_ids": ["F12:C001"],
                        },
                    ],
                }
            ]
        },
        [item],
    )
    assert len(sections[0]["claims"]) == 1
    assert dropped == 1


def test_outlook_reports_when_all_generated_claims_fail_evidence_checks():
    class UnsupportedAnswer:
        async def synthesize(self, *args, **kwargs):
            return {
                "sections": [
                    {"title": "Outlook", "claims": [{"text": "Revenue was USD 1,500.", "source_ids": ["S1"]}]}
                ]
            }

    item = evidence()
    item.id = "S1"
    item.content = "Revenue was USD 1,200."
    config = Settings(llm_api_key="test-key", llm_model="test-model", _env_file=None)
    service = ResearchService(config, UnsupportedAnswer())
    state = {
        "company": company(),
        "collected": {"stock": None},
        "rows": [],
        "evidence": [item],
        "tavily": "",
        "days": 7,
        "calculations": [],
        "model": "test-model",
        "rag_bundle": {
            "context": [item],
            "summary": {"notice": "", "body_sources_read": 0, "selected_passages": []},
            "sources": [item],
        },
    }
    report = asyncio.run(service.compose(state))["report"]
    assert report["analysis"] == []
    assert report["generation"]["failure_code"] == "claims_rejected"
    assert "evidence checks" in report["generation"]["message"]


def test_outlook_reports_rate_limit_without_exposing_provider_error():
    class LimitedModel:
        async def synthesize(self, *args, **kwargs):
            request = httpx.Request("POST", "https://provider.example/chat/completions")
            raise httpx.HTTPStatusError(
                "secret-query-token", request=request, response=httpx.Response(429, request=request)
            )

    item = evidence()
    item.id = "S1"
    config = Settings(llm_api_key="test-key", llm_model="test-model", _env_file=None)
    service = ResearchService(config, LimitedModel())
    state = {
        "company": company(),
        "collected": {"stock": None},
        "rows": [],
        "evidence": [item],
        "tavily": "",
        "days": 7,
        "calculations": [],
        "model": "test-model",
        "rag_bundle": {
            "context": [item],
            "summary": {"notice": "", "body_sources_read": 0, "selected_passages": []},
            "sources": [item],
        },
    }
    report = asyncio.run(service.compose(state))["report"]
    assert report["generation"]["failure_code"] == "rate_limit"
    assert "rate limit" in report["generation"]["message"]
    assert "secret-query-token" not in str(report)


@pytest.mark.parametrize(
    "value", ["javascript:alert(1)", "file:///secret", "https://user:password@example.com"]
)
def test_unsafe_source_links_filtered(value):
    assert safe_link(value) == ""


class FakeSources:
    calls = 0

    async def stock(self, selected):
        return None

    async def lookup(self, query, market):
        return [company(symbol="ACME", name="A newly discovered company")]

    async def financials(self, selected):
        self.calls += 1
        return [financial("TotalRevenue", "150"), financial("TotalRevenue", "100", "2024-12-31")]

    async def news(self, selected, days, category, limit):
        return [evidence(title="ACME new factory", category=category)]

    async def profile(self, selected, key):
        return None


def test_request_time_flow_no_company_database_or_file_writes(tmp_path):
    settings = Settings(database_url="", data_dir=tmp_path, _env_file=None)
    provider = FakeSources()
    service = ResearchService(settings, provider)
    with TestClient(create_app(settings, service)) as client:
        assert client.get("/health").json()["company_database_required"] is False
        match = client.get("/live/companies", params={"q": "new company"}).json()[0]
        assert match["symbol"] == "ACME"
        result = client.post("/live/research", json={"company_token": match["company_token"]})
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["report"]["company"]["symbol"] == "ACME"
        assert payload["report"]["analysis"] == []
        assert payload["report"]["status"] == "partial"
        assert provider.calls == 1
        client.post("/live/research", json={"company_token": match["company_token"]})
        assert provider.calls == 2  # Explicit refresh makes fresh source calls.
        sid, secret = payload["session_id"], payload["session_token"]
        assert client.get(f"/live/sessions/{sid}").status_code == 401
        assert (
            client.get(f"/live/sessions/{sid}", headers={"Authorization": "Bearer wrong"}).status_code == 404
        )
        headers = {"Authorization": "Bearer " + secret}
        assert client.get(f"/live/sessions/{sid}", headers=headers).headers["cache-control"] == "no-store"
        answer = client.post(
            f"/live/sessions/{sid}/follow-up", headers=headers, json={"question": "What about the factory?"}
        ).json()
        assert answer["matches"]
        assert not answer["analysis"]
        assert client.delete(f"/live/sessions/{sid}", headers=headers).status_code == 200
        assert client.get(f"/live/sessions/{sid}", headers=headers).status_code == 404
        assert not list(tmp_path.iterdir())


def test_embedded_streamlit_backend_preserves_research_session(tmp_path):
    settings = Settings(database_url="", data_dir=tmp_path, _env_file=None)
    service = ResearchService(settings, FakeSources())
    client = EmbeddedClient(create_app(settings, service))
    try:
        match = client.request("GET", "/live/companies", params={"q": "new company"}).json()[0]
        result = client.request("POST", "/live/research", json={"company_token": match["company_token"]})
        assert result.status_code == 200, result.text
        payload = result.json()
        session_path = "/live/sessions/" + payload["session_id"]
        assert client.request("GET", session_path).status_code == 401
        headers = {"Authorization": "Bearer " + payload["session_token"]}
        assert client.request("GET", session_path, headers=headers).json()["company"]["symbol"] == "ACME"
        assert client.request("DELETE", session_path, headers=headers).status_code == 200
        assert client.request("GET", session_path, headers=headers).status_code == 404
    finally:
        client.close()


def test_live_provider_errors_do_not_expose_keys(tmp_path):
    class Failed(FakeSources):
        async def financials(self, selected):
            raise httpx.ConnectError("https://provider.invalid?api_key=SECRET")

    settings = Settings(database_url="", data_dir=tmp_path, _env_file=None)
    service = ResearchService(settings, Failed())
    with TestClient(create_app(settings, service)) as client:
        result = client.post("/live/research", json={"company_token": service.tokens.issue(company())})
        assert result.status_code == 200
        assert result.json()["report"]["financials"] == []
        assert "SECRET" not in result.text


def test_live_rejects_invalid_window_and_cross_origin(tmp_path):
    settings = Settings(database_url="", data_dir=tmp_path, _env_file=None)
    with TestClient(create_app(settings, ResearchService(settings, FakeSources()))) as client:
        assert (
            client.post("/live/research", json={"company_token": "anything", "news_days": 0}).status_code
            == 422
        )
        assert (
            client.post("/live/research", headers={"Origin": "https://evil.example"}, json={}).status_code
            == 403
        )
