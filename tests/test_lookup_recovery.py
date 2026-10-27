import asyncio
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from equitylens.config import Settings
from equitylens.live.providers import PublicSources
from equitylens.live.service import ResearchService
from equitylens.live_api import create_app


def chart_metadata(**changes):
    return {
        "symbol": "RELIANCE.NS",
        "longName": "Reliance Industries Limited",
        "instrumentType": "EQUITY",
        "exchangeName": "NSI",
        "fullExchangeName": "NSE",
        **changes,
    }


def provider(meta, calls):
    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(429)
        return httpx.Response(200, json={"chart": {"result": [{"meta": meta}]}})

    return PublicSources(httpx.MockTransport(respond), settings=Settings(_env_file=None))


def test_exact_ticker_is_verified_live_when_name_search_is_rate_limited():
    calls = []
    matches = asyncio.run(provider(chart_metadata(), calls).lookup("reliance.ns", "IN"))
    assert len(matches) == 1
    assert matches[0].symbol == "RELIANCE.NS"
    assert matches[0].name == "Reliance Industries Limited"
    assert "exact ticker verification" in matches[0].provider
    assert calls == ["/v1/finance/search", "/v8/finance/chart/RELIANCE.NS"]


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "RELIANCEPOWER.NS"},
        {"instrumentType": "ETF"},
        {"exchangeName": "LSE"},
        {"longName": "", "shortName": ""},
    ],
)
def test_ticker_recovery_never_returns_a_wrong_or_unverified_company(changes):
    with pytest.raises(httpx.HTTPStatusError) as error:
        asyncio.run(provider(chart_metadata(**changes), []).lookup("RELIANCE.NS", "IN"))
    assert error.value.response.status_code == 429


def test_ticker_recovery_keeps_market_filtering():
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider(chart_metadata(), []).lookup("RELIANCE.NS", "US"))


def test_free_text_does_not_get_converted_into_an_invented_ticker():
    calls = []
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider(chart_metadata(), calls).lookup("Reliance Industries", "AUTO"))
    assert calls == ["/v1/finance/search"]


def test_hosted_error_reports_status_without_leaking_request_secrets(caplog):
    def unavailable(request):
        raise httpx.HTTPStatusError(
            "sensitive-upstream-detail",
            request=httpx.Request("GET", "https://example.com/search?key=private-key"),
            response=httpx.Response(429),
        )

    settings = Settings(_env_file=None)
    sources = PublicSources(httpx.MockTransport(unavailable), settings=settings)
    with caplog.at_level(logging.WARNING, logger="equitylens.live_api"):
        with TestClient(create_app(settings, ResearchService(settings, sources))) as client:
            response = client.get("/live/companies", params={"q": "Reliance Industries"})
    assert response.status_code == 502
    assert "HTTP 429" in response.json()["detail"]
    assert "RELIANCE.NS" in response.json()["detail"]
    assert "status=429" in caplog.text
    for secret in ("private-key", "sensitive-upstream-detail", "example.com/search"):
        assert secret not in response.text and secret not in caplog.text
