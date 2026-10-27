"""Select financial evidence for a question without repeating the same metric across years."""

import re


def select_followup_matches(question, matches, *, top_k=6):
    year_match = re.search(r"\b(?:FY|fiscal(?:\s+year)?)\s*[-/]?\s*(20\d{2})\b", question, re.I)
    if year_match is None:
        year_match = re.search(r"\b(20\d{2})\b", question)
    year = year_match.group(1) if year_match else None
    compares = bool(
        re.search(
            r"\b(?:compare|comparison|versus|vs\.?|trend|history|historical|change|growth|difference|between|over time|year.over.year)\b",
            question,
            re.I,
        )
        or len(set(re.findall(r"\b20\d{2}\b", question))) > 1
    )
    if compares:
        return matches[:top_k]

    latest_by_metric = {}
    for match in matches:
        if match.get("category") != "financial" or match.get("evidence_level") != "structured_fact":
            continue
        period = re.search(r"Reporting period\s+(\d+)M,\s+ended\s+(20\d{2}-\d{2}-\d{2})", match["text"])
        if period is None:
            continue
        months, end_date = period.groups()
        if year and (not end_date.startswith(year) or ("FY" in question.upper() and months != "12")):
            continue
        key = (match["title"].casefold(), months)
        if key not in latest_by_metric or end_date > latest_by_metric[key][0]:
            latest_by_metric[key] = (end_date, match["id"])

    chosen_financial = {item[1] for item in latest_by_metric.values()}
    selected = []
    for match in matches:
        if match.get("category") != "financial" or match.get("evidence_level") != "structured_fact":
            selected.append(match)
        elif not re.search(r"Reporting period\s+\d+M,\s+ended\s+20\d{2}-\d{2}-\d{2}", match["text"]):
            selected.append(match)
        elif match["id"] in chosen_financial:
            selected.append(match)
        if len(selected) >= top_k:
            break
    return selected
