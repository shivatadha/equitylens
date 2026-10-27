"""Display-only formatting. Original API values and currencies remain unchanged."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import altair as alt
import pandas as pd

MISSING = "Not available"
PERIODS = {"30D": "30 days", "90D": "90 days", "180D": "6 months", "1Y": "1 year"}


def decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def number(value, *, indian=False, places=2):
    parsed = decimal(value)
    if parsed is None:
        return MISSING
    sign = "−" if parsed < 0 else ""
    whole, _, fraction = f"{abs(parsed):.{places}f}".partition(".")
    if indian and len(whole) > 3:
        prefix, tail = whole[:-3], whole[-3:]
        groups = []
        while prefix:
            groups.insert(0, prefix[-2:])
            prefix = prefix[:-2]
        whole = ",".join([*groups, tail])
    elif not indian:
        whole = f"{int(whole):,}"
    return sign + whole + ("." + fraction if places else "")


def money(value, currency="USD", *, compact=False):
    parsed = decimal(value)
    if parsed is None:
        return MISSING
    symbol = {"INR": "₹", "USD": "$"}.get(currency, currency + " ")
    suffix = ""
    if compact:
        scales = (
            [("1e7", " Cr"), ("1e5", " L")]
            if currency == "INR"
            else [("1e12", " T"), ("1e9", " B"), ("1e6", " M")]
        )
        for scale, label in scales:
            if abs(parsed) >= Decimal(scale):
                parsed /= Decimal(scale)
                suffix = label
                break
    sign = "−" if parsed < 0 else ""
    return sign + symbol + number(abs(parsed), indian=currency == "INR") + suffix


def percent(value, *, signed=False):
    parsed = decimal(value)
    if parsed is None:
        return MISSING
    return ("+" if signed and parsed > 0 else "") + number(parsed) + "%"


def day(value):
    if value is None:
        return MISSING
    if isinstance(value, (date, datetime)):
        return value.strftime("%d %b %Y")
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
    except ValueError:
        return MISSING


def valuation_value(row, currency):
    unit = row["unit"]
    if unit == "×":
        return number(row["value"]) + "×"
    if unit == "shares":
        return number(row["value"], indian=currency == "INR", places=0) + " shares"
    if unit.endswith("/share"):
        return money(row["value"], unit.removesuffix("/share")) + " / share"
    return money(row["value"], unit, compact=True)


def performance_table(rows):
    return pd.DataFrame(
        [
            {
                "Holding period": PERIODS.get(row["period"], row["period"]),
                "Price change": percent(row["value_pct"], signed=True),
                "From": day(row["start_date"]),
                "To": day(row["end_date"]),
                "Calculation": "Change in provider-adjusted close; not independently verified total return",
            }
            for row in rows
        ]
    )


def valuation_table(rows, currency):
    labels = {
        "eps_ttm": "Diluted earnings per share · trailing 12 months",
        "pe_ttm": "Trailing price / earnings (P/E)",
        "ordinary_shares": "Reported ordinary shares",
        "indicative_equity_value": "Equity value · estimate",
        "price_book_estimate": "Price / book (P/B) · estimate",
    }
    return pd.DataFrame(
        [
            {
                "Stock measure": labels.get(row["id"], row["label"]),
                "Value": valuation_value(row, currency),
                "Financial period ended": day(row["financial_period_end"]),
                "How it is calculated": row["method"],
            }
            for row in rows
        ]
    )


def technical_table(rows, currency):
    return pd.DataFrame(
        [
            {
                "Price measure": row["label"],
                "Value": percent(row["value"]) if row["unit"] == "%" else money(row["value"], currency),
                "Data through": day(row["period_end"]),
                "What it measures": row["basis"],
            }
            for row in rows
        ]
    )


def actions_table(rows):
    return pd.DataFrame(
        [
            {
                "Event": row["type"].capitalize(),
                "Provider event date": day(row["date"]),
                "Dividend per share": money(row.get("amount"), row.get("currency", "USD"))
                if row["type"] == "dividend"
                else "—",
                "Split ratio": row.get("ratio", "—"),
                "Date / source note": row["note"],
            }
            for row in rows
        ]
    )


def financial_table(rows, *, compact=True):
    return pd.DataFrame(
        [
            {
                "Financial measure": row["label"],
                "Period ended": day(row["period_end"]),
                "Reporting period": {"12M": "Annual · 12 months", "3M": "Quarter · 3 months"}.get(
                    row["period_type"], row["period_type"]
                ),
                "Amount": money(row["value"], row["currency"], compact=compact),
                "Currency": row["currency"],
                "Evidence reference": row["id"],
            }
            for row in rows
        ]
    )


def history_window(history, window):
    frame = pd.DataFrame(history).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    if frame.empty:
        return frame
    end = frame.date.max()
    offset = {
        "30D": pd.DateOffset(days=30),
        "90D": pd.DateOffset(days=90),
        "1Y": pd.DateOffset(years=1),
        "2Y": pd.DateOffset(years=2),
    }[window]
    return frame[frame.date.between(end - offset, end)].copy()


def price_chart(history, window, basis, currency):
    """Plot every available daily bar with a nearest-trading-day hover."""
    frame = history_window(history, window)
    if frame.empty:
        return None, frame
    field = "close" if basis == "Close" else "adjusted_close"
    frame["price"] = pd.to_numeric(frame[field], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame["display_price"] = frame[field].map(lambda value: money(value, currency))
    for source in ("open", "high", "low", "close", "adjusted_close"):
        if source in frame:
            frame[f"display_{source}"] = frame[source].map(lambda value: money(value, currency))
        else:
            frame[f"display_{source}"] = MISSING
    frame["display_volume"] = frame["volume"].map(
        lambda value: number(value, indian=currency == "INR", places=0)
    )
    start, end = frame.date.min(), frame.date.max()
    if start == end:
        start, end = start - pd.Timedelta(hours=12), end + pd.Timedelta(hours=12)
    daily_labels = window == "30D"
    x = alt.X(
        "date:T",
        title=None,
        scale=alt.Scale(domain=[start, end], nice=False, clamp=True),
        axis=alt.Axis(
            format="%d %b" if daily_labels else "%b-%Y" if window in {"1Y", "2Y"} else "%d %b-%y",
            values=frame.date.tolist() if daily_labels else alt.Undefined,
            tickCount=alt.Undefined if daily_labels else 6,
            labelAngle=-50 if daily_labels else 0,
            labelOverlap=False if daily_labels else "greedy",
            grid=False,
        ),
    )
    base = alt.Chart(frame).encode(x=x)
    y = alt.Y(
        "price:Q",
        title=f"Price ({currency})",
        scale=alt.Scale(zero=False, nice=False, padding=18),
        axis=alt.Axis(format=",.2f"),
    )
    tooltip = [
        alt.Tooltip("date:T", title="Trading date", format="%d %b %Y"),
        alt.Tooltip("display_price:N", title="Selected price"),
        alt.Tooltip("display_open:N", title="Open"),
        alt.Tooltip("display_high:N", title="High"),
        alt.Tooltip("display_low:N", title="Low"),
        alt.Tooltip("display_close:N", title="Close"),
    ]
    if basis != "Close":
        tooltip.append(alt.Tooltip("display_adjusted_close:N", title="Adjusted close"))
    tooltip.append(alt.Tooltip("display_volume:N", title="Shares traded"))
    hover = alt.selection_point(
        fields=["date"], nearest=True, on="pointermove", clear="pointerout", empty=False
    )
    line = base.mark_line(
        color="#8d80ff", strokeWidth=2.5, clip=True, point=daily_labels or len(frame) == 1
    ).encode(y=y)
    selectors = base.mark_point(opacity=0, size=100, clip=True).encode(y=y, tooltip=tooltip).add_params(hover)
    guide = (
        base.mark_rule(color="#b3aaff", opacity=0.55, strokeDash=[4, 4], clip=True)
        .encode(opacity=alt.condition(hover, alt.value(0.55), alt.value(0)))
        .transform_filter(hover)
    )
    selected_point = (
        base.mark_circle(color="#c0b8ff", stroke="#372b7b", strokeWidth=1.5, size=85, clip=True)
        .encode(y=y, opacity=alt.condition(hover, alt.value(1), alt.value(0)))
        .transform_filter(hover)
    )
    price = alt.layer(line, guide, selected_point, selectors).properties(height=300)
    volume = (
        base.mark_bar(color="#9b92e8", opacity=0.65, clip=True)
        .encode(
            y=alt.Y(
                "volume:Q",
                title="Shares traded",
                scale=alt.Scale(zero=True),
                axis=alt.Axis(format="~s", tickCount=3),
            ),
            tooltip=tooltip,
        )
        .properties(height=100)
    )
    chart = alt.vconcat(price, volume, spacing=16).resolve_scale(x="shared").configure_view(stroke=None)
    return chart, frame


def annual_revenue_chart(rows):
    annual = [row for row in rows if row["metric"] == "TotalRevenue" and row["period_type"] == "12M"]
    if len(annual) < 2 or len({row["currency"] for row in annual}) != 1:
        return None
    frame = pd.DataFrame(annual).sort_values("period_end").drop_duplicates("period_end")
    if len(frame) < 2:
        return None
    currency = frame.iloc[0]["currency"]
    frame["period_end"] = pd.to_datetime(frame["period_end"])
    frame["display_amount"] = frame.value.map(lambda value: money(value, currency, compact=True))
    divisor, unit = (1e7, "₹ crore") if currency == "INR" else (1e6, currency + " million")
    frame["revenue"] = frame.value.map(float) / divisor
    # Use actual financial period ends instead of dozens of repeated year labels.
    ticks = frame.period_end.iloc[:: max(1, (len(frame) + 6) // 8)].tolist()
    if ticks[-1] != frame.period_end.max():
        ticks.append(frame.period_end.max())
    return (
        alt.Chart(frame)
        .mark_line(point=True, color="#8b78ef", strokeWidth=2.5, clip=True)
        .encode(
            x=alt.X(
                "period_end:T",
                title="Financial year ended",
                scale=alt.Scale(
                    domain=[frame.period_end.min(), frame.period_end.max()], nice=False, clamp=True
                ),
                axis=alt.Axis(format="%b-%Y", values=ticks, labelAngle=0, labelOverlap="greedy", grid=False),
            ),
            y=alt.Y(
                "revenue:Q",
                title="Revenue (" + unit + ")",
                scale=alt.Scale(zero=False),
                axis=alt.Axis(format=",.0f"),
            ),
            tooltip=[
                alt.Tooltip("period_end:T", title="Year ended", format="%d %b %Y"),
                alt.Tooltip("display_amount:N", title="Revenue"),
            ],
        )
        .properties(height=260)
        .configure_view(stroke=None)
    )
