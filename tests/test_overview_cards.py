from bs4 import BeautifulSoup

from equitylens.overview_cards import hero_html, trading_html


def sample_report():
    return {
        "company": {
            "name": "Reliance <Industries>",
            "symbol": "RELIANCE.NS",
            "exchange": "NSE",
            "market": "IN",
            "sector": "Energy",
            "industry": "Oil & gas",
        },
        "profile": {"description": "Oil & gas", "website": "javascript:alert(1)"},
        "financials": [
            {
                "metric": "TotalRevenue",
                "period_type": "12M",
                "period_end": "2026-03-31",
                "value": "10572190000000",
                "currency": "INR",
            }
        ],
        "evidence": [{"categories": ["news"]}],
        "news_days": 7,
        "stock": {
            "currency": "INR",
            "exchange": "NSE",
            "exchange_timezone": "Asia/Kolkata",
            "valuation": [],
            "quote": {
                "price": "1191.70",
                "daily_change": "-10.10",
                "daily_change_pct": "-0.84",
                "timestamp": "2026-10-27T08:41:00+00:00",
                "market_status": "within_scheduled_regular_session",
                "previous_close": "1201.80",
                "previous_close_date": "2026-10-26",
                "open": "1195.00",
                "day_low": "1184.00",
                "day_high": "1202.40",
                "volume": "7752238",
                "fifty_two_week_low": "1152.20",
                "fifty_two_week_high": "1611.80",
            },
        },
    }


def test_hero_has_one_quote_and_escapes_untrusted_profile_data():
    html = BeautifulSoup(hero_html(sample_report()), "html.parser")
    assert html.select_one(".ey-company-name").text == "Reliance <Industries>"
    assert html.find("industries") is None
    assert html.select_one(".ey-quote-value").text == "₹1,191.70"
    assert len(html.select(".ey-overview-fact")) == 4
    assert html.select_one(".ey-profile-link") is None


def test_trading_snapshot_separates_session_and_long_term_ranges():
    html = BeautifulSoup(trading_html(sample_report()["stock"]), "html.parser")
    assert [x.text for x in html.select(".ey-trading-grid .ey-overview-label")] == [
        "Previous close",
        "Open",
        "Day range",
        "Shares traded",
    ]
    assert "₹1,184.00 – ₹1,202.40" in html.select_one(".ey-trading-grid").text
    assert "₹1,152.20 – ₹1,611.80" in html.select_one(".ey-trading-range").text
