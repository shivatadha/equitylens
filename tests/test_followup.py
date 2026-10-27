from equitylens.live.followup import select_followup_matches


def observation(identifier, year, *, title="Reliance — Operating cash flow", months="12"):
    return {
        "id": identifier,
        "title": title,
        "text": f"Operating cash flow: 1.0E+12 INR. Reporting period {months}M, ended {year}-03-31.",
        "category": "financial",
        "evidence_level": "structured_fact",
    }


def test_specific_fiscal_year_shows_only_that_annual_observation():
    hits = [observation("F2024", "2024"), observation("F2026", "2026"), observation("F2025", "2025")]
    assert [row["id"] for row in select_followup_matches("Operating cash flow in FY2026?", hits)] == [
        "F2026"
    ]


def test_undated_question_shows_latest_observation_per_metric():
    hits = [
        observation("F2024", "2024"),
        observation("F2026", "2026"),
        observation("F2025", "2025"),
        observation("D2026", "2026", title="Reliance — Debt"),
    ]
    assert [row["id"] for row in select_followup_matches("How is cash generated?", hits)] == [
        "F2026",
        "D2026",
    ]


def test_comparison_keeps_historical_observations():
    hits = [observation("F2024", "2024"), observation("F2026", "2026")]
    assert select_followup_matches("Compare operating cash flow from 2024 to 2026", hits) == hits
