"""Stock snapshots with explicit quote times, currencies and calculation inputs."""

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np


def number(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or (positive and result <= 0):
            return None
        return result
    except (InvalidOperation, ValueError, TypeError):
        return None


def text(value):
    return str(value) if value is not None else None


def timestamp(value, now):
    parsed = number(value, positive=True)
    if parsed is None:
        return None
    try:
        result = datetime.fromtimestamp(int(parsed), UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return result if result <= now + timedelta(minutes=5) else None


def at(values, i):
    return values[i] if isinstance(values, list) and i < len(values) else None


def parse_history(result, zone, now):
    indicators = result.get("indicators") or {}
    quotes = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
    by_date = {}
    for i, raw_time in enumerate(result.get("timestamp") or []):
        instant = timestamp(raw_time, now)
        close = number(at(quotes.get("close"), i), positive=True)
        if instant is None or close is None:
            continue
        day = instant.astimezone(zone).date().isoformat()
        volume = number(at(quotes.get("volume"), i))
        by_date[day] = {
            "date": day,
            "timestamp": instant.isoformat(),
            "open": text(number(at(quotes.get("open"), i), positive=True)),
            "high": text(number(at(quotes.get("high"), i), positive=True)),
            "low": text(number(at(quotes.get("low"), i), positive=True)),
            "close": str(close),
            "adjusted_close": text(number(at(adjusted, i), positive=True)),
            "volume": str(volume) if volume is not None and volume >= 0 else None,
        }
    return sorted(by_date.values(), key=lambda row: row["date"])[-600:]


def parse_actions(result, zone, now, currency):
    actions = []
    for kind in ("dividends", "splits"):
        for item in (result.get("events", {}).get(kind) or {}).values():
            instant = timestamp(item.get("date"), now)
            if not instant:
                continue
            day = instant.astimezone(zone).date().isoformat()
            if kind == "dividends":
                amount = number(item.get("amount"))
                if amount is None or amount < 0:
                    continue
                actions.append(
                    {
                        "type": "dividend",
                        "date": day,
                        "amount": str(amount),
                        "currency": currency,
                        "note": "Provider event date; not independently verified as payment or ex-dividend date.",
                    }
                )
            else:
                actions.append(
                    {
                        "type": "split",
                        "date": day,
                        "ratio": str(item.get("splitRatio", "unknown")),
                        "note": "Provider-reported split event.",
                    }
                )
    return sorted(actions, key=lambda row: row["date"], reverse=True)


def performance(history):
    adjusted = [row for row in history if row["adjusted_close"] is not None]
    returns = []
    if len(adjusted) < 2:
        return returns
    latest = adjusted[-1]
    end = date.fromisoformat(latest["date"])
    for label, days in [("30D", 30), ("90D", 90), ("180D", 180), ("1Y", 365)]:
        target = end - timedelta(days=days)
        eligible = [row for row in adjusted if date.fromisoformat(row["date"]) <= target]
        if not eligible:
            continue
        base = eligible[-1]
        if (target - date.fromisoformat(base["date"])).days > 7:
            continue
        change = (Decimal(latest["adjusted_close"]) / Decimal(base["adjusted_close"]) - 1) * 100
        returns.append(
            {
                "period": label,
                "value_pct": str(change.quantize(Decimal(".01"))),
                "start_date": base["date"],
                "end_date": latest["date"],
                "basis": "Provider adjusted close; not an independently reconstructed total return.",
            }
        )
    return returns


def technicals(history):
    metrics = []
    if not history:
        return metrics
    for days in (50, 200):
        if len(history) >= days:
            average = sum(Decimal(row["close"]) for row in history[-days:]) / days
            metrics.append(
                {
                    "id": f"sma_{days}",
                    "label": f"{days}-session moving average",
                    "value": str(average.quantize(Decimal(".01"))),
                    "unit": "quote_currency",
                    "period_end": history[-1]["date"],
                    "basis": f"Mean of the latest {days} available provider daily closes.",
                }
            )
    values = np.array(
        [float(row["adjusted_close"]) for row in history[-252:] if row["adjusted_close"] is not None]
    )
    # Dropping missing adjusted prices would turn multi-session moves into daily returns.
    if len(values) >= 31 and len(values) == len(history[-252:]):
        log_returns = np.diff(np.log(values))
        volatility = float(np.std(log_returns, ddof=1) * math.sqrt(252) * 100)
        drawdown = float(np.min(values / np.maximum.accumulate(values) - 1) * 100)
        metrics.extend(
            [
                {
                    "id": "volatility",
                    "label": "Annualized observed volatility",
                    "value": f"{volatility:.2f}",
                    "unit": "%",
                    "period_end": history[-1]["date"],
                    "basis": f"Daily adjusted-close log returns; sample standard deviation; 252 sessions/year; {len(values)} prices.",
                },
                {
                    "id": "drawdown",
                    "label": "Maximum observed drawdown",
                    "value": f"{drawdown:.2f}",
                    "unit": "%",
                    "period_end": history[-1]["date"],
                    "basis": f"Peak-to-trough decline within the latest {len(values)} available adjusted daily closes (at most 252).",
                },
            ]
        )
    return metrics


def valuation_observations(payload, now):
    observations = []
    for result in (payload or {}).get("timeseries", {}).get("result") or []:
        for key in ("trailingDilutedEPS", "quarterlyOrdinarySharesNumber", "quarterlyStockholdersEquity"):
            for item in result.get(key, []):
                try:
                    period_end = date.fromisoformat(item["asOfDate"])
                    value = number(item["reportedValue"]["raw"])
                    if value is None or period_end > now.date():
                        continue
                    observations.append(
                        {
                            "metric": key,
                            "value": str(value),
                            "currency": item["currencyCode"],
                            "period_end": str(period_end),
                            "period_type": item.get("periodType"),
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    continue
    return observations


def valuation(price, currency, observations, actions):
    values, gaps = [], []

    def latest(metric, period_type):
        return next(
            iter(
                sorted(
                    (
                        row
                        for row in observations
                        if row["metric"] == metric
                        and row["period_type"] == period_type
                        and row["currency"] == currency
                    ),
                    key=lambda row: row["period_end"],
                    reverse=True,
                )
            ),
            None,
        )

    eps = latest("trailingDilutedEPS", "TTM")
    if eps:
        values.append(
            {
                "id": "eps_ttm",
                "label": "Diluted EPS (TTM)",
                "value": eps["value"],
                "unit": currency + "/share",
                "financial_period_end": eps["period_end"],
                "method": "Provider-reported trailing diluted earnings per share.",
            }
        )
        split_after_eps = any(
            event["type"] == "split" and event["date"] > eps["period_end"] for event in actions
        )
        if price is not None and Decimal(eps["value"]) > 0 and not split_after_eps:
            values.append(
                {
                    "id": "pe_ttm",
                    "label": "Price / trailing diluted EPS",
                    "value": str((price / Decimal(eps["value"])).quantize(Decimal(".01"))),
                    "unit": "×",
                    "financial_period_end": eps["period_end"],
                    "method": "Regular-session quote / provider TTM diluted EPS, matched by currency. Not a buy/sell signal.",
                }
            )
        else:
            gaps.append(
                "P/E withheld: non-positive EPS, a missing quote, or a subsequent split requiring EPS adjustment verification."
            )
    else:
        gaps.append("Trailing diluted EPS in the quote currency is unavailable; P/E was not fabricated.")
    shares = latest("quarterlyOrdinarySharesNumber", "3M")
    if shares and Decimal(shares["value"]) > 0:
        split_after = any(
            event["type"] == "split" and event["date"] > shares["period_end"] for event in actions
        )
        if split_after:
            gaps.append(
                "A split follows the reported share-count date; share-based valuation estimates were withheld."
            )
        else:
            values.append(
                {
                    "id": "ordinary_shares",
                    "label": "Reported ordinary shares",
                    "value": shares["value"],
                    "unit": "shares",
                    "financial_period_end": shares["period_end"],
                    "method": "Latest available quarterly provider observation; not a live shares-outstanding feed.",
                }
            )
            if price is not None:
                values.append(
                    {
                        "id": "indicative_equity_value",
                        "label": "Indicative equity value",
                        "value": str((price * Decimal(shares["value"])).quantize(Decimal(".01"))),
                        "unit": currency,
                        "financial_period_end": shares["period_end"],
                        "method": "Quote × latest reported ordinary shares. An estimate, not published market cap; share-class/ADR treatment is not independently verified.",
                    }
                )
            equity = latest("quarterlyStockholdersEquity", "3M")
            if (
                equity
                and equity["period_end"] == shares["period_end"]
                and Decimal(equity["value"]) > 0
                and price is not None
            ):
                book_per_share = Decimal(equity["value"]) / Decimal(shares["value"])
                values.append(
                    {
                        "id": "price_book_estimate",
                        "label": "Price / book estimate",
                        "value": str((price / book_per_share).quantize(Decimal(".01"))),
                        "unit": "×",
                        "financial_period_end": shares["period_end"],
                        "method": "Quote / (reported stockholders equity / ordinary shares), matching period and currency; share-class/ADR treatment not independently verified.",
                    }
                )
    gaps.append(
        "Published market cap, bid/ask, analyst targets and forward P/E were not available from the unauthenticated quote source."
    )
    return values, gaps


def build_stock_snapshot(company, chart_payload, facts_payload, now):
    result = next(iter(chart_payload.get("chart", {}).get("result") or []), None)
    if not result:
        raise ValueError("No stock-price data returned.")
    meta = result.get("meta") or {}
    if meta.get("symbol", "").upper() != company.symbol.upper():
        raise ValueError("Stock source returned a different security.")
    currency = meta.get("currency")
    if not currency:
        raise ValueError("Quote currency is missing.")
    try:
        zone = ZoneInfo(meta.get("exchangeTimezoneName", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    quote_time = timestamp(meta.get("regularMarketTime"), now)
    price = number(meta.get("regularMarketPrice"), positive=True)
    history = parse_history(result, zone, now)
    actions = parse_actions(result, zone, now, currency)
    quote_date = quote_time.astimezone(zone).date().isoformat() if quote_time else None
    current_bar = next((row for row in history if row["date"] == quote_date), None)
    previous = next(iter([row for row in reversed(history) if quote_date and row["date"] < quote_date]), None)
    previous_close = number(meta.get("regularMarketPreviousClose"), positive=True)
    if previous_close is None and current_bar and previous:
        previous_close = Decimal(previous["close"])
    # chartPreviousClose belongs to the start of the requested chart window, not yesterday.
    change = price - previous_close if price is not None and previous_close is not None else None
    change_pct = change / previous_close * 100 if change is not None else None
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = (
        timestamp(period.get("start"), now + timedelta(days=1)),
        timestamp(period.get("end"), now + timedelta(days=1)),
    )
    market_status = (
        "within_scheduled_regular_session"
        if start and end and start <= now < end
        else "outside_scheduled_regular_session"
        if start and end
        else "unknown"
    )
    metrics, gaps = valuation(price, currency, valuation_observations(facts_payload, now), actions)
    if quote_time is None:
        gaps.append("Quote timestamp is unavailable; freshness cannot be verified.")
    if price is None:
        gaps.append("Latest regular-session price is unavailable.")
    if not history:
        gaps.append("Daily price history is unavailable.")
    volume = number(meta.get("regularMarketVolume"))
    snapshot = {
        "symbol": company.symbol,
        "currency": currency,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "exchange_timezone": str(zone),
        "provider": "Yahoo Finance public chart and financial time series (unofficial)",
        "source_url": f"https://finance.yahoo.com/quote/{quote(company.symbol, safe='')}/",
        "retrieved_at": now.isoformat(),
        "quote": {
            "price": text(price),
            "timestamp": quote_time.isoformat() if quote_time else None,
            "exchange_timestamp": quote_time.astimezone(zone).isoformat() if quote_time else None,
            "basis": "Latest available regular-session quote; not extended-hours or overnight trading.",
            "delay": "Provider delay not supplied; real-time accuracy is not guaranteed.",
            "market_status": market_status,
            "previous_close": text(previous_close),
            "previous_close_date": previous["date"] if previous_close is not None and previous else None,
            "daily_change": text(change),
            "daily_change_pct": str(change_pct.quantize(Decimal(".01"))) if change_pct is not None else None,
            "open": current_bar["open"] if current_bar else None,
            "day_high": text(number(meta.get("regularMarketDayHigh"), positive=True)),
            "day_low": text(number(meta.get("regularMarketDayLow"), positive=True)),
            "volume": text(volume) if volume is not None and volume >= 0 else None,
            "fifty_two_week_high": text(number(meta.get("fiftyTwoWeekHigh"), positive=True)),
            "fifty_two_week_low": text(number(meta.get("fiftyTwoWeekLow"), positive=True)),
        },
        "history": history,
        "performance": performance(history),
        "technicals": technicals(history),
        "valuation": metrics,
        "actions": actions,
        "gaps": gaps,
        "notes": [
            "Data is fetched on demand, but every quote retains its own trading timestamp. A closed market can have an older last quote.",
            "Daily history may include a still-forming current-session bar. No intraday chart is claimed.",
            "Adjusted-close comparisons use provider adjustments; they are not an independent total-return reconstruction.",
            "Valuation estimates combine a market quote with older financial observations; both dates are shown.",
        ],
    }
    return snapshot


def stock_evidence(company, stock):
    """Index bounded dated stock facts, not hundreds of raw daily bars."""
    from .types import Evidence

    sources = []

    def add(identifier, title, content):
        sources.append(
            Evidence(
                id=identifier,
                title=f"{company.name} — {title}",
                url=stock["source_url"],
                publisher=stock["provider"],
                retrieved_at=stock["retrieved_at"],
                category="stock",
                categories=["stock"],
                evidence_level="structured_fact",
                content=content,
            )
        )

    quote_data = stock["quote"]
    as_of = (
        f"Price observation dated {quote_data['as_of_date']}; exact quote time is not supplied; currency {stock['currency']}."
        if quote_data.get("as_of_date")
        else f"Quote as of {quote_data['exchange_timestamp']} ({stock['exchange_timezone']}); currency {stock['currency']}."
    )
    add(
        "M1",
        "stock price and trading volume",
        f"{company.symbol} stock price {quote_data['price']} {stock['currency']}; daily change {quote_data['daily_change']} "
        f"({quote_data['daily_change_pct']}%); previous close {quote_data['previous_close']} on {quote_data['previous_close_date']}; "
        f"trading volume {quote_data['volume']}. {as_of} {quote_data['basis']} {quote_data['delay']}",
    )
    add(
        "M2",
        "stock trading ranges",
        f"Daily low/high {quote_data['day_low']} / {quote_data['day_high']}; 52-week low/high "
        f"{quote_data['fifty_two_week_low']} / {quote_data['fifty_two_week_high']}. {as_of}",
    )
    for i, metric in enumerate(stock["valuation"], 1):
        add(
            f"MV{i}",
            metric["label"],
            f"{metric['label']}: {metric['value']} {metric['unit']}. Financial period ended {metric['financial_period_end']}. "
            f"{metric['method']} {as_of}",
        )
    for i, metric in enumerate(stock["technicals"], 1):
        unit = stock["currency"] if metric["unit"] == "quote_currency" else metric["unit"]
        add(
            f"MT{i}",
            metric["label"],
            f"{metric['label']}: {metric['value']} {unit}; through {metric['period_end']}. {metric['basis']}",
        )
    for i, metric in enumerate(stock["performance"], 1):
        add(
            f"MR{i}",
            metric["period"] + " stock performance",
            f"{metric['period']} price performance: {metric['value_pct']}%, from {metric['start_date']} "
            f"to {metric['end_date']}. {metric['basis']}",
        )
    for i, event in enumerate(stock["actions"][:12], 1):
        detail = (
            f"{event['amount']} {event['currency']}/share" if event["type"] == "dividend" else event["ratio"]
        )
        add(
            f"MA{i}",
            event["type"] + " event",
            f"Reported {event['type']}: {detail}, provider event date {event['date']}. {event['note']}",
        )
    add("MG1", "stock coverage limitations", " ".join(stock["gaps"] + stock["notes"]))
    return sources
