from copy import deepcopy

from bs4 import BeautifulSoup

from equitylens.ui_cards import formatted_excerpt, news_cards_html, passage_cards_html, published_time


def news(**changes):
    return {
        "id": "S1",
        "title": "A reported factory expansion - Example News",
        "publisher": "Example News",
        "published_at": "2026-10-26T04:03:00+00:00",
        "evidence_level": "headline",
        "content": "",
        "url": "https://example.com/article",
        **changes,
    }


def passage():
    return {
        "id": "F12:C001",
        "source_id": "F12",
        "title": "Example — Operating cash flow",
        "text": "Operating cash flow: 5.15927E+8 INR. Reporting period 12M, ended 2026-03-31.",
        "category": "financial",
        "evidence_level": "structured_fact",
        "publisher": "Financial provider",
        "url": "https://example.com/financials",
        "locator": "Reported observation F12",
        "semantic_score": 0.637,
        "keyword_score": 2.786,
    }


def test_financial_evidence_is_formatted_but_original_and_retrieval_details_remain_available():
    original = passage()
    before = deepcopy(original)
    report = {
        "financials": [
            {
                "id": "F12",
                "label": "Operating cash flow",
                "value": "5.15927E+8",
                "currency": "INR",
                "period_type": "12M",
                "period_end": "2026-03-31",
            }
        ]
    }
    html = BeautifulSoup(passage_cards_html([original], report), "html.parser")
    assert html.select_one(".ey-fact-value").text == "₹51.59 Cr"
    assert "Period ended 31 Mar 2026" in html.select_one(".ey-card-copy").text
    assert html.select_one("details p").text == original["text"]
    assert "not confidence probabilities" in html.select_one("details").text
    assert html.select_one(".ey-source-id").text == "F12:C001"
    assert original == before


def test_scientific_currency_formatting_does_not_rewrite_dates_ids_or_other_values():
    text = "Revenue: 7.8637E+10 INR. Period 2026-03-31, source F12; 3M; 1.2E+3 EUR."
    assert formatted_excerpt(text) == "Revenue: ₹7,863.70 Cr. Period 2026-03-31, source F12; 3M; 1.2E+3 EUR."


def test_headline_card_does_not_invent_a_summary_or_duplicate_its_publisher_in_the_title():
    html = BeautifulSoup(news_cards_html([news()]), "html.parser")
    assert html.h3.text == "A reported factory expansion"
    assert html.select_one(".ey-card-copy") is None
    assert html.select_one(".ey-pill").text == "Headline only"
    assert html.select_one(".ey-publisher").text.startswith("Example News")
    assert "26 Oct 2026 · 09:33 IST" in html.text
    assert html.a["rel"] == ["noopener", "noreferrer"]


def test_untrusted_titles_excerpts_and_link_schemes_cannot_inject_markup():
    source = news(
        title="<img src=x onerror=alert(1)>",
        publisher="<script>bad()</script>",
        content="<svg onload=alert(1)>",
        url="javascript:alert(1)",
    )
    html = BeautifulSoup(news_cards_html([source]), "html.parser")
    assert html.find("script") is None and html.find("img") is None and html.find("svg") is None
    assert html.find("a") is None
    assert "<img src=x onerror=alert(1)>" in html.h3.text


def test_untrusted_original_passages_are_escaped_inside_the_audit_disclosure():
    source = passage()
    source["text"] = "<script>bad()</script>"
    html = BeautifulSoup(passage_cards_html([source]), "html.parser")
    assert html.find("script") is None
    assert html.select_one("details p").text == "<script>bad()</script>"


def test_missing_publication_time_is_explicit():
    assert published_time(None) == "Publication date unavailable"
    assert published_time("not-a-date") == "Publication date unavailable"


def test_headline_evidence_keeps_original_but_does_not_repeat_title_as_body():
    source = passage()
    source.update(
        evidence_level="headline", category="news", title="Reported expansion", text="Reported expansion"
    )
    html = BeautifulSoup(passage_cards_html([source]), "html.parser")
    assert html.h3.text == "Reported expansion"
    assert html.select_one(".ey-card-copy") is None
    assert "article body has not been reviewed" in html.select_one(".ey-fact-note").text
    assert html.select_one("details p").text == "Reported expansion"
