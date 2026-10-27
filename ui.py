"""On-demand research UI. No company database is accessed by this flow."""

import hmac
import json
import os
import time
from pathlib import Path

import httpx
import streamlit as st

from equitylens.embedded_client import EmbeddedClient
from equitylens.overview_cards import hero_html, trading_html
from equitylens.presentation import (
    actions_table,
    annual_revenue_chart,
    day,
    financial_table,
    number,
    performance_table,
    price_chart,
    technical_table,
    valuation_table,
)
from equitylens.ui_cards import formatted_excerpt, news_cards_html, passage_cards_html, published_time

API = os.getenv("API_BASE_URL", "http://127.0.0.1:8001")
st.set_page_config(page_title="EquityLens | Live company research", page_icon="◈", layout="wide")
st.markdown(
    "<style>" + (Path(__file__).parent / "assets" / "ui.css").read_text(encoding="utf-8") + "</style>",
    unsafe_allow_html=True,
)


def require_demo_access():
    """Gate an internet-facing demo before any research API calls."""
    if os.getenv("EQUITYLENS_REQUIRE_DEMO_PASSWORD", "") != "1":
        return
    expected = os.getenv("EQUITYLENS_DEMO_PASSWORD", "")
    if not expected:
        st.error("Demo access is not configured. Set EQUITYLENS_DEMO_PASSWORD on the UI service.")
        st.stop()
    if st.session_state.get("demo_authenticated"):
        return
    st.title("EquityLens demo")
    st.caption("Enter the invitation password to use live company research.")
    with st.form("demo_access"):
        supplied = st.text_input("Demo password", type="password")
        submitted = st.form_submit_button("Enter demo")
    if submitted:
        if hmac.compare_digest(supplied.encode(), expected.encode()):
            st.session_state["demo_authenticated"] = True
            st.rerun()
        st.error("Incorrect demo password.")
    st.stop()


require_demo_access()


def call(method, path, **kwargs):
    response = (
        embedded_client().request(method, path, **kwargs)
        if os.getenv("EQUITYLENS_BACKEND") == "embedded"
        else httpx.request(method, API + path, timeout=195, **kwargs)
    )
    if response.is_error:
        try:
            message = response.json().get("detail", "Request failed.")
        except ValueError:
            message = "The backend returned an unexpected response."
        raise ValueError(str(message))
    return response.json()


@st.cache_resource(show_spinner=False)
def embedded_client():
    return EmbeddedClient()


def auth():
    return {"Authorization": "Bearer " + st.session_state["research_token"]}


def clear_research():
    if st.session_state.get("research_id"):
        try:
            call("DELETE", "/live/sessions/" + st.session_state["research_id"], headers=auth())
        except (httpx.HTTPError, ValueError):
            pass
    for key in ("research_id", "research_token", "expires_at", "followup_result", "retrieval_result"):
        st.session_state.pop(key, None)


def company_summary(report):
    st.markdown(hero_html(report), unsafe_allow_html=True)


def show_stock(stock):
    if not stock:
        st.info("Stock-price data could not be retrieved. Run fresh research to try again.")
        return
    currency = stock["currency"]
    st.markdown(trading_html(stock), unsafe_allow_html=True)
    if stock["history"]:
        st.subheader("Daily price history")
        left, right = st.columns(2)
        window = left.selectbox("Chart period", ["30D", "90D", "1Y", "2Y"], index=0)
        basis = right.selectbox("Price basis", ["Close", "Provider adjusted close"])
        chart, frame = price_chart(stock["history"], window, basis, currency)
        if chart is not None and frame["price"].notna().any():
            st.altair_chart(chart, width="stretch")
            st.caption(
                f"Showing {day(frame.date.min())} to {day(frame.date.max())} · {len(frame)} trading sessions · "
                + (
                    "Each trading date is labeled; hover for price, OHLC and volume"
                    if window == "30D"
                    else "Hover for each trading day's price, OHLC and volume; choose 30D for daily date labels"
                )
            )
        else:
            st.info("Prices are unavailable for this period and price basis.")
        st.caption("Daily bars; the latest bar may still be forming. No intraday prices are implied.")
    if stock["performance"]:
        st.subheader("Price performance")
        st.caption(
            "Percentages compare provider-adjusted daily closing prices. They are historical observations, not forecasts."
        )
        st.dataframe(performance_table(stock["performance"]), hide_index=True, width="stretch")
    st.subheader("Valuation and reported share data")
    st.caption(
        "Calculated ratios combine the quote above with older financial inputs. Indicative equity value is an estimate of quote × reported shares."
    )
    if stock["valuation"]:
        st.dataframe(
            valuation_table(stock["valuation"], currency),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No supported valuation inputs were available.")
    if stock["technicals"]:
        st.subheader("Observed price risk and trend")
        st.dataframe(
            technical_table(stock["technicals"], currency),
            hide_index=True,
            width="stretch",
        )
    with st.expander("Reported dividends and splits"):
        if stock["actions"]:
            st.dataframe(actions_table(stock["actions"]), hide_index=True, width="stretch")
        else:
            st.write(
                "No events were returned in the requested two-year window; this does not establish that the company never paid dividends."
            )
    with st.expander("Stock sources and limitations"):
        for note in stock["gaps"] + stock["notes"]:
            st.write("• " + note)
        st.caption(stock["provider"] + " · Retrieved " + stock["retrieved_at"])
        st.link_button("Inspect stock source", stock["source_url"])


def evidence_cards(items, prefix):
    st.markdown(news_cards_html(items), unsafe_allow_html=True)


def passage_cards(passages, prefix, report=None):
    st.markdown(passage_cards_html(passages, report), unsafe_allow_html=True)


TAB_NAMES = [
    "Stock & price",
    "Financial history",
    "Current news",
    "Projects & industry",
    "Outlook & questions",
    "Sources & coverage",
    "Retrieved evidence",
]


try:
    health = call("GET", "/health")
    if health.get("mode") != "on-demand":
        st.error("Restart the backend with python -m equitylens.cli serve to activate on-demand research.")
        st.stop()
    capabilities = call("GET", "/live/capabilities")
except (httpx.HTTPError, ValueError):
    st.error("Start the API: .venv\\Scripts\\python.exe -m equitylens.cli serve")
    st.stop()

st.logo(
    str(Path(__file__).parent / "assets/equitylens-logo.svg"),
    size="large",
    icon_image=str(Path(__file__).parent / "assets/equitylens-icon.svg"),
)
with st.sidebar:
    st.markdown(
        '<div class="ey-workspace-note"><strong>Built for a broader view.</strong><br>India & United States<br>Company research, with sources.</div>',
        unsafe_allow_html=True,
    )

with st.container(key="workspace_toolbar"):
    heading, settings_button = st.columns([6, 2], vertical_alignment="center")
    with heading:
        if st.session_state.get("research_id"):
            st.markdown('<div class="ey-compact-heading">Company workspace</div>', unsafe_allow_html=True)
        else:
            st.markdown('<p class="ey-kicker">MARKET RESEARCH / EQUITYLENS</p>', unsafe_allow_html=True)
            st.title("Company research")
    with settings_button:
        with st.popover("Research settings", icon=":material/tune:", width="stretch"):
            st.subheader("Research preferences")
            days_label = st.selectbox(
                "News window",
                ["Last 7 days", "Last 24 hours", "Last 30 days", "Last 90 days"],
                key="news_window",
            )
            depth = st.radio(
                "Research depth", ["Quick", "Detailed"], horizontal=True, key="research_depth"
            ).lower()
            days = {"Last 7 days": 7, "Last 24 hours": 1, "Last 30 days": 30, "Last 90 days": 90}[days_label]
            st.caption(
                "Preferences apply to your next research run. Project and industry discovery uses at least 30 days."
            )
    st.caption(
        "Live prices, financials and reported developments · "
        + days_label
        + " · "
        + depth.capitalize()
        + " research"
    )

with st.expander("Find or change company", expanded=not bool(st.session_state.get("research_id"))):
    with st.form("find_company"):
        col1, col2, col3 = st.columns([5, 2, 1.5], vertical_alignment="bottom")
        query = col1.text_input("Company name or ticker", placeholder="Tesla, Reliance, Infosys, Microsoft…")
        market_label = col2.selectbox("Market", ["Auto-detect", "India", "United States"])
        submitted = col3.form_submit_button("Find company", width="stretch", type="primary")

    if submitted:
        if len(query.strip()) < 2:
            st.warning("Enter at least two characters.")
        else:
            try:
                with st.spinner("Looking up the company now…"):
                    matches = call(
                        "GET",
                        "/live/companies",
                        params={
                            "q": query.strip(),
                            "market": {"Auto-detect": "AUTO", "India": "IN", "United States": "US"}[
                                market_label
                            ],
                        },
                    )
                st.session_state["matches"] = matches
                if not matches:
                    st.info(
                        "No supported equity matches found. Try the full name or ticker and select its market."
                    )
            except (httpx.HTTPError, ValueError) as exc:
                st.error(str(exc))

    if st.session_state.get("matches"):
        selected = st.selectbox(
            "Confirm the company",
            st.session_state["matches"],
            format_func=lambda c: f"{c['name']} · {c['symbol']} · {c['exchange']} · {c['market']}",
        )
        st.caption("Confirm the listing: similar names can refer to different companies.")
        if st.button("Research this company now", type="primary"):
            body = {
                "company_token": selected["company_token"],
                "news_days": days,
                "depth": depth,
            }
            try:
                with st.status(
                    "Fetching stock prices, financials, news, and reported developments…", expanded=True
                ) as status:
                    st.write(
                        "Sources are contacted for this request. Unavailable sections will be identified."
                    )
                    result = call("POST", "/live/research", json=body)
                    for step in result["report"]["workflow"]:
                        st.write("✓ " + step)
                    status.update(label="Live research assembled", state="complete", expanded=False)
                clear_research()
                st.session_state["research_id"] = result["session_id"]
                st.session_state["research_token"] = result["session_token"]
                st.session_state["expires_at"] = time.time() + result["expires_in_seconds"]
                st.rerun()
            except (httpx.HTTPError, ValueError) as exc:
                st.error(str(exc))


@st.fragment(run_every=30)
def show_report():
    if not st.session_state.get("research_id"):
        st.info("Find a company to start. Information is retrieved when you request research.")
        return
    if time.time() >= st.session_state.get("expires_at", 0):
        clear_research()
        st.info("Your temporary research expired. Run a new search for current information.")
        return
    try:
        report = call("GET", "/live/sessions/" + st.session_state["research_id"], headers=auth())
    except (httpx.HTTPError, ValueError):
        clear_research()
        st.info("This session expired or the backend restarted. Run fresh research.")
        return
    st.divider()
    company = report["company"]
    company_summary(report)
    st.caption(
        f"Research snapshot assembled {published_time(report['researched_at'])}."
    )
    news_items = [e for e in report["evidence"] if "news" in e["categories"]]
    tabs = dict(
        zip(
            TAB_NAMES,
            st.tabs(TAB_NAMES, default="Stock & price", key="research_tab", on_change="rerun"),
            strict=True,
        )
    )
    with tabs["Stock & price"]:
        show_stock(report.get("stock"))
    with tabs["Financial history"]:
        st.subheader("Financial performance")
        if report["calculations"]:
            columns = st.columns(min(3, len(report["calculations"])))
            for position, calculation in enumerate(report["calculations"]):
                with columns[position % len(columns)]:
                    st.metric(calculation["label"], number(calculation["value"]) + calculation["unit"])
                    st.caption("Year ended " + day(calculation["period_end"]))
            with st.expander("Calculation methods and input references"):
                for calculation in report["calculations"]:
                    st.write(calculation["label"] + ": " + calculation["formula"])
                    st.caption("Evidence: " + ", ".join(calculation["inputs"]))
        if report["financials"]:
            left, right = st.columns(2)
            reporting = left.selectbox("Reporting period", ["Annual", "Quarterly", "All periods"])
            amount_mode = right.selectbox(
                "Amount display", ["Crore / lakh for INR · compact USD", "Full amounts"]
            )
            period_type = {"Annual": "12M", "Quarterly": "3M"}.get(reporting)
            selected_rows = [
                row
                for row in report["financials"]
                if period_type is None or row["period_type"] == period_type
            ]
            st.caption(
                "INR = Indian rupees · Cr = crore (1,00,00,000) · L = lakh (1,00,000). USD stays in US dollars; no currency conversion is applied."
            )
            st.dataframe(
                financial_table(selected_rows, compact=amount_mode != "Full amounts"),
                width="stretch",
                hide_index=True,
            )
            revenue_chart = annual_revenue_chart(report["financials"])
            if revenue_chart is not None:
                st.caption("Annual revenue · actual financial period ends · hover for the reported amount")
                st.altair_chart(revenue_chart, width="stretch")
            with st.expander("How to read this financial data"):
                st.write(
                    "Annual rows cover 12 months; quarterly rows cover 3 months. Amounts are provider aggregates. Consolidated versus standalone reporting and source filings have not been independently reconciled. Evidence references link these observations to the temporary research index."
                )
            st.link_button("Financial history source", report["financials"][0]["source_url"])
        else:
            st.info("No financial history was retrieved for this company.")
    with tabs["Current news"]:
        st.caption(
            "Headlines from Google News RSS. Open publisher coverage before relying on a story; article bodies were not verified."
        )
        evidence_cards(news_items, "news")
        if not news_items:
            st.info("No matching dated news was found in the selected period.")
    with tabs["Projects & industry"]:
        st.markdown(
            '<div class="ey-section-heading"><span class="ey-section-kicker">BUSINESS RADAR</span><h2>Projects & developments</h2><p>Reported investments, expansion and company activity, with links to the original coverage.</p></div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Discovery window: {report['project_days']} days. These are reported developments, not a verified inventory of company projects."
        )
        projects = [e for e in report["evidence"] if "projects" in e["categories"]]
        evidence_cards(projects, "projects")
        if not projects:
            st.write("No project-related coverage was retrieved.")
        st.subheader("Industry developments")
        industry = [e for e in report["evidence"] if "industry" in e["categories"]]
        evidence_cards(industry, "industry")
        if not industry:
            st.write("No industry coverage was retrieved; the source may lack an industry classification.")
    with tabs["Outlook & questions"]:
        if report["analysis"]:
            st.caption(
                "AI interpretation from supplied evidence. Citation IDs are checked; semantic support still requires source review."
            )
            for section in report["analysis"]:
                st.subheader(section["title"])
                for claim in section["claims"]:
                    st.write(formatted_excerpt(claim["text"]))
                    st.caption("Sources: " + ", ".join(claim["source_ids"]))
        else:
            if report.get("generation", {}).get("state") == "unavailable":
                st.warning(
                    report.get("generation", {}).get("message")
                    or "The model did not return a usable cited outlook. Inspect the coverage gaps and retrieved evidence."
                )
            else:
                st.info(
                    "AI outlook is optional. Configure a frontier-model API key and model when you want generated analysis. Live evidence and stock research work without it."
                )
            for question in report["follow_up_questions"]:
                st.write("• " + question)
        with st.form("followup"):
            question = st.text_input("Ask about this session’s evidence")
            api_ready = capabilities.get("generation_configured", False)
            asked = st.form_submit_button("Ask with API model" if api_ready else "Find relevant evidence")
        if asked and len(question.strip()) >= 3:
            try:
                with st.spinner("Checking the current session…"):
                    st.session_state["followup_result"] = call(
                        "POST",
                        "/live/sessions/" + st.session_state["research_id"] + "/follow-up",
                        headers=auth(),
                        json={"question": question},
                    )
            except (httpx.HTTPError, ValueError) as exc:
                st.error(str(exc))
        if st.session_state.get("followup_result"):
            answer = st.session_state["followup_result"]
            if answer.get("failure_code"):
                st.warning(answer["notice"])
            else:
                st.caption(answer["notice"])
            for section in answer["analysis"]:
                for claim in section["claims"]:
                    st.write(formatted_excerpt(claim["text"]) + " [" + ", ".join(claim["source_ids"]) + "]")
            cited_ids = {
                source_id
                for section in answer["analysis"]
                for claim in section["claims"]
                for source_id in claim["source_ids"]
            }
            displayed = [match for match in answer["matches"] if match["id"] in cited_ids] if cited_ids else answer["matches"]
            passage_cards(displayed, "followup", report)
    with tabs["Sources & coverage"]:
        if st.button("Clear current research"):
            clear_research()
            st.rerun()
        reconciliation = report.get("filing_reconciliation", [])
        if reconciliation:
            st.subheader("SEC 10-K comparison")
            st.caption("Matched annual USD amounts are compared by period. Accounting concept and reporting basis still need review.")
            for item in reconciliation:
                label = item["metric"].replace("Total", "Total ").replace("Operating", "Operating ").replace("Net", "Net ")
                status = "Matches" if item["status"] == "amount_matches" else "Differs" if item["status"] == "amount_differs" else "No matching provider period"
                st.write(f"{label} · {item['period_end']}: {status} · SEC {item['sec_value']} USD" + (f" · provider {item['provider_value']} USD" if item["provider_value"] else ""))
                st.link_button(f"Inspect 10-K source for {label}", item["filing_url"])
        st.subheader("Coverage gaps")
        for gap in report["gaps"]:
            st.write("• " + gap)
        st.subheader("Source notes")
        for note in report["source_notes"]:
            st.caption(note)
        official_notices = [e for e in report["evidence"] if "official_announcement" in e["categories"]]
        if official_notices:
            st.subheader("Official NSE announcements")
            evidence_cards(official_notices, "nse")
        excerpts = [e for e in report["evidence"] if "research" in e["categories"]]
        if excerpts:
            st.subheader("Additional web research excerpts")
            evidence_cards(excerpts, "web")
        st.download_button(
            "Download research snapshot (JSON)",
            json.dumps(report, indent=2),
            file_name=f"{company['symbol']}-research.json",
            mime="application/json",
        )
    with tabs["Retrieved evidence"]:
        rag = report.get("rag", {})
        st.markdown(
            '<div class="ey-section-heading"><span class="ey-section-kicker">EVIDENCE EXPLORER</span><h2>Go straight to the source.</h2><p>Search this research session for the passages behind the numbers and reported developments.</p></div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"{'Semantic + keyword search' if rag.get('mode') == 'hybrid_semantic_bm25' else 'Keyword search only'} · {rag.get('passages', 0)} source passages · {rag.get('model') or 'Semantic model unavailable'}"
        )
        st.write(
            "Sources are chunked and searched within this session. Semantic and keyword results are combined and selected for diversity. Only retrieved passages reach the answer model."
        )
        if rag.get("notice"):
            st.info(rag["notice"])
        with st.form("retrieve_passages"):
            retrieval_question = st.text_input(
                "Search passages by meaning", placeholder="How does this company generate cash?"
            )
            retrieve_submitted = st.form_submit_button("Retrieve passages")
        if retrieve_submitted and len(retrieval_question.strip()) >= 3:
            try:
                st.session_state["retrieval_result"] = call(
                    "POST",
                    "/live/sessions/" + st.session_state["research_id"] + "/retrieve",
                    headers=auth(),
                    json={"question": retrieval_question, "top_k": 6},
                )
            except (httpx.HTTPError, ValueError) as exc:
                st.error(str(exc))
        result = st.session_state.get("retrieval_result")
        if result:
            st.caption(result["notice"])
            passage_cards(result["passages"], "retrieval", report)
        else:
            st.caption("Passages selected for the initial report context:")
            passage_cards(rag.get("selected_passages", []), "report-context", report)
        with st.expander("Source reading outcomes"):
            st.write(rag.get("source_reads", []))


show_report()
