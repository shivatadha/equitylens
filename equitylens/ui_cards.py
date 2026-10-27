"""Escaped, display-only cards. The original evidence remains available for audit."""

import re
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .presentation import day, money, number

EVIDENCE_LABELS = {
    "headline": "Headline only",
    "structured_fact": "Financial observation",
    "search_excerpt": "Search excerpt",
    "page_excerpt": "Page excerpt",
    "pdf_excerpt": "PDF excerpt",
    "provider_profile": "Business profile",
    "official_feed_summary": "Official exchange feed summary",
    "demo_scenario": "Sample scenario",
}


def source_link(url):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            return escape(url, quote=True)
    except (ValueError, TypeError):
        pass
    return ""


def published_time(value):
    if not value:
        return "Publication date unavailable"
    try:
        parsed = datetime.fromisoformat(value)
        return (
            parsed.replace(tzinfo=parsed.tzinfo or UTC)
            .astimezone(ZoneInfo("Asia/Kolkata"))
            .strftime("%d %b %Y · %H:%M IST")
        )
    except (ValueError, TypeError):
        return "Publication date unavailable"


def formatted_excerpt(text):
    # Only a scientific amount followed by an explicit currency is reformatted.
    # The original passage is preserved in the audit disclosure and in the RAG index.
    return re.sub(
        r"(?<![\w.])([+\-]?\d+(?:\.\d+)?[eE][+\-]?\d+)\s+(INR|USD)\b",
        lambda match: money(match[1], match[2], compact=True),
        text,
    )


def pill(text, variant=""):
    return '<span class="ey-pill ' + variant + '">' + escape(text) + "</span>"


def news_cards_html(items):
    cards = []
    for source in items:
        title, publisher = source["title"], source["publisher"]
        suffix = " - " + publisher
        if publisher and title.endswith(suffix):
            title = title[: -len(suffix)]
        level = EVIDENCE_LABELS.get(source["evidence_level"], "Source evidence")
        excerpt = source.get("content") or ""
        url = source_link(source["url"])
        action = (
            '<a class="ey-card-link" href="'
            + url
            + '" target="_blank" rel="noopener noreferrer">Read source <span aria-hidden="true">↗</span></a>'
            if url
            else "<span>Source link unavailable</span>"
        )
        cards.append(
            '<article class="ey-news-card"><div class="ey-card-top">'
            + pill(level)
            + '<span class="ey-source-id">'
            + escape(source["id"])
            + "</span></div>"
            + "<h3>"
            + escape(title)
            + "</h3>"
            + ('<p class="ey-card-copy">' + escape(excerpt) + "</p>" if excerpt else "")
            + '<footer><div class="ey-publisher">'
            + escape(publisher)
            + "<small>"
            + escape(published_time(source.get("published_at")))
            + "</small></div>"
            + action
            + "</footer></article>"
        )
    return '<div class="ey-news-grid">' + "".join(cards) + "</div>"


def passage_cards_html(passages, report=None):
    rows = {row["id"]: row for row in (report or {}).get("financials", [])}
    cards = []
    for passage in passages:
        row = rows.get(passage["source_id"])
        title = passage["title"].split(" — ", 1)[-1]
        level = EVIDENCE_LABELS.get(passage["evidence_level"], "Source evidence")
        if passage["category"] == "stock" and passage["evidence_level"] == "structured_fact":
            level = "Stock observation"
        if row:
            title = row["label"]
            body = (
                '<div class="ey-fact-value">'
                + escape(money(row["value"], row["currency"], compact=True))
                + "</div>"
            )
            body += '<p class="ey-card-copy">' + escape(
                {"12M": "Annual · 12 months", "3M": "Quarter · 3 months"}.get(
                    row["period_type"], row["period_type"]
                )
            )
            body += " · Period ended " + escape(day(row["period_end"])) + "</p>"
            body += '<p class="ey-fact-note">Provider aggregate · Filing reconciliation pending</p>'
        elif passage["evidence_level"] == "headline":
            body = '<p class="ey-fact-note">Discovery headline · The article body has not been reviewed.</p>'
        else:
            excerpt = (
                formatted_excerpt(passage["text"])
                if passage["evidence_level"] == "structured_fact"
                else passage["text"]
            )
            body = '<p class="ey-card-copy">' + escape(excerpt) + "</p>"
        url = source_link(passage["url"])
        action = (
            '<a class="ey-card-link" href="'
            + url
            + '" target="_blank" rel="noopener noreferrer">Inspect source ↗</a>'
            if url
            else "<span>Source link unavailable</span>"
        )
        audit = (
            '<details class="ey-card-audit"><summary>Original passage & retrieval details</summary><p>'
            + escape(passage["text"])
            + "</p>"
        )
        audit += (
            "<p>"
            + escape(passage["locator"])
            + "</p><p>Meaning match: "
            + escape(number(passage["semantic_score"], places=3))
        )
        audit += (
            " · Keyword match: "
            + escape(number(passage["keyword_score"], places=3))
            + "</p><p>Ranking signals; not confidence probabilities.</p></details>"
        )
        cards.append(
            '<article class="ey-evidence-card"><div class="ey-card-top">'
            + pill(level, "ey-pill-evidence")
            + '<span class="ey-source-id">'
            + escape(passage["id"])
            + "</span></div><h3>"
            + escape(title)
            + "</h3>"
            + body
            + '<footer><span class="ey-publisher">'
            + escape(passage["publisher"])
            + "</span>"
            + action
            + "</footer>"
            + audit
            + "</article>"
        )
    return '<div class="ey-evidence-grid">' + "".join(cards) + "</div>"
