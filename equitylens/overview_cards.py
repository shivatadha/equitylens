"""Escaped, responsive company and trading cards for the Streamlit workspace."""

from html import escape
from textwrap import shorten

from .presentation import day, decimal, money, number, percent, valuation_value
from .ui_cards import published_time, source_link


def _text(value):
    return escape(str(value)) if value is not None else ""


def _fact(label, value, note):
    return (
        '<div class="ey-overview-fact"><span class="ey-overview-label">'
        + _text(label)
        + '</span><strong class="ey-overview-value">'
        + _text(value)
        + '</strong><span class="ey-overview-note">'
        + _text(note)
        + "</span></div>"
    )


def hero_html(report):
    company, stock = report["company"], report.get("stock")
    profile = report.get("profile") or {}
    currency = stock["currency"] if stock else ("INR" if company["market"] == "IN" else "USD")
    values = {row["id"]: row for row in stock["valuation"]} if stock else {}
    revenue = next(
        (
            row
            for row in report["financials"]
            if row["metric"] == "TotalRevenue" and row["period_type"] == "12M"
        ),
        None,
    )
    facts = [
        _fact(
            "Annual revenue",
            money(revenue["value"], revenue["currency"], compact=True) if revenue else "Not available",
            "Year ended " + day(revenue["period_end"]) if revenue else "No annual observation",
        ),
        _fact(
            "Indicative equity value",
            valuation_value(values["indicative_equity_value"], currency)
            if "indicative_equity_value" in values
            else "Not available",
            "Quote × dated shares · estimate",
        ),
        _fact(
            "Trailing P/E",
            valuation_value(values["pe_ttm"], currency) if "pe_ttm" in values else "Not available",
            "Financial period " + day(values["pe_ttm"]["financial_period_end"])
            if "pe_ttm" in values
            else "No supported ratio",
        ),
        _fact(
            "Diluted EPS · TTM",
            valuation_value(values["eps_ttm"], currency) if "eps_ttm" in values else "Not available",
            "Financial period " + day(values["eps_ttm"]["financial_period_end"])
            if "eps_ttm" in values
            else "No supported observation",
        ),
    ]
    quote = stock["quote"] if stock else {}
    price = money(quote.get("price"), currency) if stock else "Not available"
    movement = decimal(quote.get("daily_change"))
    movement_class = (
        "is-up"
        if movement is not None and movement > 0
        else "is-down"
        if movement is not None and movement < 0
        else "is-flat"
    )
    change = ""
    if movement is not None:
        change = (
            '<span class="ey-price-change '
            + movement_class
            + '">'
            + _text(("+" if movement > 0 else "") + money(movement, currency))
            + (
                " · " + _text(percent(quote["daily_change_pct"], signed=True))
                if quote.get("daily_change_pct") is not None
                else ""
            )
            + "</span>"
        )
    quote_time = (
        published_time(quote["timestamp"])
        if quote.get("timestamp")
        else day(quote["as_of_date"]) + " · trading date only"
        if quote.get("as_of_date")
        else "Quote time unavailable"
    )
    initials = "".join(word[:1] for word in company["name"].split()[:2]).upper() or "?"
    market = "India" if company["market"] == "IN" else "United States"
    description = profile.get("description")
    business = (
        shorten(" ".join(description.split()), width=190, placeholder="…")
        if description
        else (company.get("industry") or "Business description unavailable from current sources.")
    )
    sector = company.get("sector") or "Not provided"
    news_count = sum("news" in item["categories"] for item in report["evidence"])
    headline_label = "headline" if news_count == 1 else "headlines"
    website = source_link(profile.get("website"))
    website_html = (
        '<a class="ey-profile-link" href="'
        + website
        + '" target="_blank" rel="noopener noreferrer">Company website ↗</a>'
        if website
        else ""
    )
    return (
        '<section class="ey-hero" aria-label="Company overview">'
        '<div class="ey-hero-top"><div class="ey-identity">'
        '<span class="ey-company-avatar" aria-hidden="true">'
        + _text(initials)
        + '</span><div class="ey-identity-copy"><span class="ey-overline">COMPANY RESEARCH</span>'
        '<h2 class="ey-company-name">'
        + _text(company["name"])
        + '</h2><p class="ey-listing">'
        + _text(company["symbol"])
        + " <span>·</span> "
        + _text(company["exchange"])
        + " <span>·</span> "
        + _text(market)
        + '</p></div></div><div class="ey-quote-panel"><span class="ey-overline">'
        + _text(quote.get("label", "LAST REGULAR-SESSION PRICE"))
        + "</span>"
        '<strong class="ey-quote-value">'
        + _text(price)
        + "</strong>"
        + change
        + '<span class="ey-quote-time">'
        + _text(quote_time)
        + '</span></div></div><div class="ey-hero-content"><div class="ey-overview-grid">'
        + "".join(facts)
        + '</div><aside class="ey-business-panel"><span class="ey-overline">AT A GLANCE</span>'
        '<p class="ey-business-description">'
        + _text(business)
        + '</p><div class="ey-business-meta"><span>Sector</span><strong>'
        + _text(sector)
        + '</strong></div><div class="ey-business-meta"><span>Recent coverage</span><strong>'
        + _text(f"{news_count} {headline_label} · {report['news_days']} days")
        + "</strong></div>"
        + website_html
        + '</aside></div><div class="ey-hero-footer">'
        "<span>Financial figures are dated provider observations. Equity value is an estimate.</span>"
        "</div></section>"
    )


def trading_html(stock):
    quote, currency = stock["quote"], stock["currency"]
    low, high = money(quote.get("day_low"), currency), money(quote.get("day_high"), currency)
    day_range = f"{low} – {high}" if low != "Not available" and high != "Not available" else "Not available"
    week_low, week_high = (
        money(quote.get("fifty_two_week_low"), currency),
        money(quote.get("fifty_two_week_high"), currency),
    )
    week_range = (
        f"{week_low} – {week_high}"
        if week_low != "Not available" and week_high != "Not available"
        else "Not available"
    )
    status = {
        "within_scheduled_regular_session": "Within scheduled session",
        "outside_scheduled_regular_session": "Outside scheduled session",
        "unknown": "Trading schedule unavailable",
    }.get(quote["market_status"], "Trading schedule unavailable")
    if stock.get("data_mode") == "demo":
        status = "Synthetic sample data"
    quote_time = (
        published_time(quote["timestamp"])
        if quote.get("timestamp")
        else day(quote["as_of_date"]) + " · trading date only"
        if quote.get("as_of_date")
        else "Quote time unavailable"
    )
    metrics = "".join(
        [
            _fact(
                "Previous close",
                money(quote.get("previous_close"), currency),
                "Trading date " + day(quote.get("previous_close_date")),
            ),
            _fact("Open", money(quote.get("open"), currency), "Regular-session opening price"),
            _fact("Day range", day_range, "Reported regular-session low and high"),
            _fact(
                "Shares traded",
                number(quote.get("volume"), indian=currency == "INR", places=0),
                "Reported volume",
            ),
        ]
    )
    return (
        '<section class="ey-trading" aria-label="Trading snapshot">'
        '<div class="ey-trading-header"><div><span class="ey-overline">MARKET ACTIVITY</span>'
        "<h2>Trading snapshot</h2><p>"
        + _text(stock["exchange"])
        + " · "
        + _text(currency)
        + " · "
        + _text(stock["exchange_timezone"])
        + '</p></div><span class="ey-market-status">'
        + _text(status)
        + '</span></div><div class="ey-trading-grid">'
        + metrics
        + '</div><div class="ey-trading-range"><span>52-week range</span><strong>'
        + _text(week_range)
        + '</strong></div><div class="ey-trading-footer"><span>Quote: '
        + _text(quote_time)
        + "</span><span>"
        + (
            "Synthetic sample prices · Not market data"
            if stock.get("data_mode") == "demo"
            else "End-of-day data · No intraday quote"
            if quote.get("as_of_date")
            else "Provider delay not supplied · Regular session only"
        )
        + "</span></div>"
        "</section>"
    )
