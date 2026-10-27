from datetime import date, timedelta

import pandas as pd
import pytest

from equitylens.presentation import (
    MISSING,
    annual_revenue_chart,
    financial_table,
    history_window,
    money,
    number,
    performance_table,
    price_chart,
    valuation_table,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7.8637E+10", "₹78,63,70,00,000.00"),
        ("0", "₹0.00"),
        ("-12345678.9", "−₹1,23,45,678.90"),
        ("1779", "₹1,779.00"),
    ],
)
def test_rupee_values_use_indian_groups_without_scientific_notation(value, expected):
    assert money(value, "INR") == expected


def test_summary_units_do_not_convert_dollars_to_rupees():
    assert money("7.8637E+10", "INR", compact=True) == "₹7,863.70 Cr"
    assert money("7.8637E+10", "USD", compact=True) == "$78.64 B"
    assert money("123456", "INR", compact=True) == "₹1.23 L"
    assert number("12345678", indian=True, places=0) == "1,23,45,678"


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "not-a-number", True])
def test_unavailable_numbers_never_become_zero(value):
    assert money(value, "INR") == MISSING


def history():
    end = date(2026, 10, 26)
    return [
        {
            "date": (end - timedelta(days=offset)).isoformat(),
            "close": str(100 + offset),
            "adjusted_close": str(99 + offset),
            "volume": "1234567",
        }
        for offset in reversed(range(1000))
    ]


@pytest.mark.parametrize(
    ("window", "offset"),
    [
        ("30D", pd.DateOffset(days=30)),
        ("90D", pd.DateOffset(days=90)),
        ("1Y", pd.DateOffset(years=1)),
        ("2Y", pd.DateOffset(years=2)),
    ],
)
def test_chart_dates_and_axes_are_bounded_and_cannot_pan_beyond_range(window, offset):
    chart, frame = price_chart(history(), window, "Close", "INR")
    end = pd.Timestamp("2026-10-26")
    assert frame.date.min() == end - offset
    assert frame.date.max() == end
    spec = chart.to_dict()
    price_layers = spec["vconcat"][0]["layer"]
    if window == "30D":
        axis = price_layers[0]["encoding"]["x"]["axis"]
        assert [pd.Timestamp(value) for value in axis["values"]] == frame.date.tolist()
        assert axis["labelOverlap"] is False
        assert price_layers[0]["mark"]["point"] is True
    for layer in [*price_layers, spec["vconcat"][1]]:
        scale = layer["encoding"]["x"]["scale"]
        assert pd.Timestamp(scale["domain"][0]) == frame.date.min()
        assert pd.Timestamp(scale["domain"][1]) == frame.date.max()
        assert scale["nice"] is False and scale["clamp"] is True
        assert layer["mark"]["clip"] is True
    assert price_layers[0]["encoding"]["y"]["scale"]["zero"] is False
    hover = spec["params"][0]["select"]
    assert hover["type"] == "point" and hover["nearest"] is True
    tooltip_fields = {item["field"] for item in price_layers[-1]["encoding"]["tooltip"]}
    assert {
        "date",
        "display_open",
        "display_high",
        "display_low",
        "display_close",
        "display_volume",
    } <= tooltip_fields
    assert all(param["select"]["type"] != "interval" for param in spec["params"])
    assert frame.display_price.iloc[-1] == "₹100.00"


def test_history_range_uses_calendar_years_across_leap_day():
    rows = [{"date": "2024-02-28"}, {"date": "2024-02-29"}, {"date": "2025-02-28"}]
    assert history_window(rows, "1Y").date.min() == pd.Timestamp("2024-02-28")


def test_adjusted_basis_is_used_without_mutating_original_history():
    rows = history()
    _, frame = price_chart(rows, "30D", "Provider adjusted close", "USD")
    assert frame.price.iloc[-1] == 99
    assert rows[-1]["adjusted_close"] == "99"
    assert "price" not in rows[-1]


def test_daily_chart_retains_each_available_bar_and_its_hover_details():
    rows = [
        {
            "date": "2026-10-23",
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "adjusted_close": "10.5",
            "volume": "1200",
        },
        {
            "date": "2026-10-26",
            "open": "11",
            "high": "14",
            "low": "10",
            "close": "13",
            "adjusted_close": "12.5",
            "volume": "1500",
        },
    ]
    chart, frame = price_chart(rows, "30D", "Close", "USD")
    assert frame.date.dt.strftime("%Y-%m-%d").tolist() == ["2026-10-23", "2026-10-26"]
    assert frame.display_open.tolist() == ["$10.00", "$11.00"]
    assert frame.display_high.tolist() == ["$12.00", "$14.00"]
    assert frame.display_low.tolist() == ["$9.00", "$10.00"]
    assert frame.display_close.tolist() == ["$11.00", "$13.00"]
    assert [item["field"] for item in chart.to_dict()["vconcat"][1]["encoding"]["tooltip"]][
        -1
    ] == "display_volume"


def test_display_tables_have_human_labels_dates_and_formatted_values():
    frame = financial_table(
        [
            {
                "label": "Revenue",
                "period_end": "2025-12-31",
                "period_type": "12M",
                "value": "7.8637E+10",
                "currency": "INR",
                "id": "F1",
            }
        ]
    )
    assert frame.iloc[0]["Amount"] == "₹7,863.70 Cr"
    assert frame.iloc[0]["Reporting period"] == "Annual · 12 months"
    assert frame.iloc[0]["Period ended"] == "31 Dec 2025"
    performance = performance_table(
        [
            {
                "period": "1Y",
                "value_pct": "-12.987",
                "start_date": "2025-10-24",
                "end_date": "2026-10-26",
            }
        ]
    )
    assert list(performance.columns) == ["Holding period", "Price change", "From", "To", "Calculation"]
    assert performance.iloc[0]["Price change"] == "−12.99%"
    assert performance.iloc[0]["Holding period"] == "1 year"


def test_ratio_share_count_and_currency_are_distinct_units():
    rows = [
        {
            "id": "pe_ttm",
            "label": "P/E",
            "value": "30.5",
            "unit": "×",
            "financial_period_end": "2026-06-30",
            "method": "Quote / EPS",
        },
        {
            "id": "ordinary_shares",
            "label": "Shares",
            "value": "1.1E+9",
            "unit": "shares",
            "financial_period_end": "2026-06-30",
            "method": "Reported",
        },
    ]
    frame = valuation_table(rows, "INR")
    assert frame.iloc[0]["Value"] == "30.50×"
    assert frame.iloc[1]["Value"] == "1,10,00,00,000 shares"


def test_revenue_axis_uses_unique_actual_period_ends_with_month_and_year():
    dates = ["2023-09-30", "2024-09-30", "2025-09-30", "2026-09-30"]
    rows = [
        {
            "metric": "TotalRevenue",
            "value": "35000000000",
            "currency": "INR",
            "period_type": "12M",
            "period_end": value,
        }
        for value in dates
    ]
    chart = annual_revenue_chart(rows)
    spec = chart.to_dict()
    axis = spec["encoding"]["x"]["axis"]
    assert axis["format"] == "%b-%Y"
    assert [pd.Timestamp(value).date().isoformat() for value in axis["values"]] == dates
    assert len(axis["values"]) == len(set(axis["values"])) == 4
    assert spec["encoding"]["y"]["title"] == "Revenue (₹ crore)"
    data = next(iter(spec["datasets"].values()))
    assert data[0]["revenue"] == 3500


def test_revenue_chart_does_not_mix_currencies_or_annual_and_quarterly_rows():
    rows = [
        {
            "metric": "TotalRevenue",
            "value": "100",
            "currency": "INR",
            "period_type": "12M",
            "period_end": "2025-03-31",
        },
        {
            "metric": "TotalRevenue",
            "value": "200",
            "currency": "USD",
            "period_type": "12M",
            "period_end": "2026-03-31",
        },
    ]
    assert annual_revenue_chart(rows) is None
    rows[1]["period_type"] = "3M"
    assert annual_revenue_chart(rows) is None
