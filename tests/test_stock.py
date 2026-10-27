import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from equitylens.live.providers import PublicSources
from equitylens.live.stock import build_stock_snapshot, performance, stock_evidence, technicals, valuation
from equitylens.live.types import CompanyMatch

NOW = datetime(2026, 10, 26, 15, tzinfo=UTC)


def company(symbol="TSLA", market="US"):
    return CompanyMatch(
        symbol=symbol, name="Example company", exchange="NASDAQ", market=market, retrieved_at=NOW
    )


def chart(symbol="TSLA", currency="USD"):
    days = [NOW - timedelta(days=day) for day in (3, 2, 0)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "currency": currency,
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketPrice": 110,
                        "regularMarketTime": int(NOW.timestamp()),
                        "chartPreviousClose": 20,
                        "regularMarketVolume": 1000,
                        "regularMarketDayLow": 105,
                        "regularMarketDayHigh": 112,
                        "fiftyTwoWeekLow": 60,
                        "fiftyTwoWeekHigh": 120,
                    },
                    "timestamp": [int(day.timestamp()) for day in days],
                    "indicators": {
                        "quote": [
                            {"close": [90, 100, 110], "open": [89, 99, 105], "volume": [100, 200, 1000]}
                        ],
                        "adjclose": [{"adjclose": [90, 100, 110]}],
                    },
                }
            ]
        }
    }


def facts(eps=5, currency="USD"):
    result = []
    for metric, value, period in [
        ("trailingDilutedEPS", eps, "TTM"),
        ("quarterlyOrdinarySharesNumber", 1000, "3M"),
        ("quarterlyStockholdersEquity", 20000, "3M"),
    ]:
        result.append(
            {
                metric: [
                    {
                        "asOfDate": "2026-06-30",
                        "reportedValue": {"raw": value},
                        "currencyCode": currency,
                        "periodType": period,
                    }
                ]
            }
        )
    return {"timeseries": {"result": result}}


def test_daily_change_uses_previous_trading_bar_not_chart_window_start():
    snapshot = build_stock_snapshot(company(), chart(), facts(), NOW)
    assert snapshot["quote"]["previous_close"] == "100"
    assert snapshot["quote"]["daily_change"] == "10"
    assert snapshot["quote"]["daily_change_pct"] == "10.00"
    metrics = {row["id"]: row for row in snapshot["valuation"]}
    assert metrics["pe_ttm"]["value"] == "22.00"
    assert metrics["indicative_equity_value"]["value"] == "110000.00"
    assert metrics["price_book_estimate"]["value"] == "5.50"
    assert metrics["pe_ttm"]["financial_period_end"] == "2026-06-30"


@pytest.mark.parametrize("eps", [0, -5])
def test_nonpositive_eps_has_no_pe(eps):
    snapshot = build_stock_snapshot(company(), chart(), facts(eps), NOW)
    assert "pe_ttm" not in {row["id"] for row in snapshot["valuation"]}
    assert any("non-positive EPS" in gap for gap in snapshot["gaps"])


def test_currency_mismatch_does_not_create_ratios():
    snapshot = build_stock_snapshot(company(), chart(), facts(currency="INR"), NOW)
    assert snapshot["valuation"] == []


def test_split_after_financial_period_withholds_unverified_ratios():
    payload = chart()
    payload["chart"]["result"][0]["events"] = {
        "splits": {"1": {"date": int(NOW.timestamp()), "splitRatio": "2:1"}}
    }
    snapshot = build_stock_snapshot(company(), payload, facts(), NOW)
    identifiers = {row["id"] for row in snapshot["valuation"]}
    assert identifiers == {"eps_ttm"}
    assert snapshot["actions"][0]["ratio"] == "2:1"


def test_price_book_requires_matching_periods():
    observations = [
        {
            "metric": "quarterlyOrdinarySharesNumber",
            "value": "1000",
            "currency": "USD",
            "period_type": "3M",
            "period_end": "2026-06-30",
        },
        {
            "metric": "quarterlyStockholdersEquity",
            "value": "20000",
            "currency": "USD",
            "period_type": "3M",
            "period_end": "2026-03-31",
        },
    ]
    metrics, _ = valuation(Decimal("110"), "USD", observations, [])
    assert "price_book_estimate" not in {row["id"] for row in metrics}


def test_missing_current_bar_does_not_fabricate_daily_change():
    payload = chart()
    payload["chart"]["result"][0]["timestamp"][-1] = int((NOW - timedelta(days=1)).timestamp())
    snapshot = build_stock_snapshot(company(), payload, {}, NOW)
    assert snapshot["quote"]["daily_change"] is None


def test_future_invalid_and_duplicate_bars_are_filtered():
    payload = chart()
    result = payload["chart"]["result"][0]
    result["timestamp"] += [
        int((NOW + timedelta(days=1)).timestamp()),
        result["timestamp"][0],
        int((NOW - timedelta(days=1)).timestamp()),
    ]
    result["indicators"]["quote"][0]["close"] += [120, 91, float("nan")]
    snapshot = build_stock_snapshot(company(), payload, {}, NOW)
    assert len(snapshot["history"]) == 3
    assert snapshot["history"][0]["close"] == "91"
    assert snapshot["technicals"] == []


def test_wrong_security_is_rejected():
    with pytest.raises(ValueError, match="different security"):
        build_stock_snapshot(company(), chart(symbol="OTHER"), {}, NOW)


def test_closed_market_keeps_original_quote_timestamp():
    snapshot = build_stock_snapshot(company(), chart(), {}, NOW + timedelta(days=2))
    assert snapshot["quote"]["timestamp"] == NOW.isoformat()
    assert snapshot["retrieved_at"] != snapshot["quote"]["timestamp"]


def test_technical_calculations_have_observation_counts_and_dates():
    rows = [
        {
            "date": (NOW - timedelta(days=day)).date().isoformat(),
            "close": str(100 + day),
            "adjusted_close": str(100 + day),
        }
        for day in reversed(range(200))
    ]
    metrics = {row["id"]: row for row in technicals(rows)}
    assert metrics["sma_50"]["value"] == "124.50"
    assert metrics["sma_200"]["value"] == "199.50"
    assert Decimal(metrics["drawdown"]["value"]) < 0
    assert "200 prices" in metrics["volatility"]["basis"]


@pytest.mark.parametrize(
    ("symbol", "currency", "market"), [("TSLA", "USD", "US"), ("RELIANCE.NS", "INR", "IN")]
)
def test_provider_keeps_quote_when_valuation_endpoint_fails(symbol, currency, market):
    def respond(request):
        if "/chart/" in request.url.path:
            return httpx.Response(200, json=chart(symbol, currency))
        return httpx.Response(503)

    provider = PublicSources(httpx.MockTransport(respond))
    snapshot = asyncio.run(provider.stock(company(symbol, market)))
    assert snapshot["currency"] == currency
    assert snapshot["quote"]["price"] == "110"
    assert snapshot["valuation"] == []


def test_stock_facts_are_dated_and_citable_for_rag():
    snapshot = build_stock_snapshot(company(), chart(), facts(), NOW)
    sources = stock_evidence(company(), snapshot)
    assert len({source.id for source in sources}) == len(sources)
    quote_source = next(source for source in sources if source.id == "M1")
    assert "2026-10-26" in quote_source.content
    assert "110 USD" in quote_source.content
    assert quote_source.evidence_level == "structured_fact"
    pe_source = next(source for source in sources if source.title.endswith("Price / trailing diluted EPS"))
    assert "2026-06-30" in pe_source.content
    assert "22.00" in pe_source.content


def test_future_financial_inputs_do_not_enter_valuation():
    payload = deepcopy(facts())
    for row in payload["timeseries"]["result"]:
        next(iter(row.values()))[0]["asOfDate"] = "2027-01-01"
    assert build_stock_snapshot(company(), chart(), payload, NOW)["valuation"] == []


def test_invalid_valuation_json_does_not_discard_good_prices():
    def respond(request):
        if "/chart/" in request.url.path:
            return httpx.Response(200, json=chart())
        return httpx.Response(200, text="Temporarily unavailable")

    snapshot = asyncio.run(PublicSources(httpx.MockTransport(respond)).stock(company()))
    assert snapshot["quote"]["price"] == "110"
    assert snapshot["valuation"] == []


def test_performance_uses_adjusted_close_and_requires_enough_history():
    rows = [
        {
            "date": (NOW - timedelta(days=day)).date().isoformat(),
            "close": "500",
            "adjusted_close": str(100 - day),
        }
        for day in reversed(range(31))
    ]
    returns = performance(rows)
    assert len(returns) == 1 and returns[0]["period"] == "30D"
    assert returns[0]["value_pct"] == "42.86"
    assert returns[0]["end_date"] == NOW.date().isoformat()


def test_missing_adjusted_bars_do_not_turn_multiday_moves_into_daily_volatility():
    rows = [
        {
            "date": (NOW - timedelta(days=day)).date().isoformat(),
            "close": "100",
            "adjusted_close": "100" if day != 15 else None,
        }
        for day in reversed(range(50))
    ]
    identifiers = {row["id"] for row in technicals(rows)}
    assert "sma_50" in identifiers
    assert "volatility" not in identifiers
