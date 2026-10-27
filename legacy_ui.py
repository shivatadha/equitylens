"""Python-only research evidence workspace. Backend is always accessed via its API."""

import os

import httpx
import streamlit as st

API = os.getenv("API_BASE_URL", "http://127.0.0.1:8001")
st.set_page_config(page_title="EquityLens | Evidence workspace", page_icon="◈", layout="wide")
st.markdown(
    """<style>.block-container{padding-top:2rem;max-width:1300px}div[data-testid="stMetric"]{background:#f1f6f5;border:1px solid #d5e3df;border-radius:12px;padding:18px}h1{letter-spacing:-1px}.stApp{background:#fbfcfd}</style>""",
    unsafe_allow_html=True,
)


def call(method, path, **kwargs):
    response = httpx.request(method, API + path, timeout=180, **kwargs)
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = "The server returned an unexpected response."
        raise ValueError(str(detail))
    return response.json()


st.caption("EQUITYLENS / COMPANY RESEARCH")
st.title("Research starts with evidence.")
st.write("Build a traceable company library across India and the United States.")
try:
    companies = call("GET", "/companies")
except (httpx.HTTPError, ValueError):
    st.error(
        "Start the EquityLens API on port 8001 to open your workspace. See the README for the startup command."
    )
    st.stop()

with st.sidebar:
    st.header("Company workspace")
    company = st.selectbox(
        "Select a company", companies, format_func=lambda c: f"{c['name']} · {c['country']}"
    )
    st.caption("Two curated companies are available in milestone 1.")
    st.divider()
    st.write("**Local development**")
    st.caption(
        "Document ingestion and evidence search are working. Financial analysis and AI reports come in the next milestones."
    )
    st.link_button("API explorer", API + "/docs")

cid = company["id"]
st.subheader(company["name"])
st.caption(
    " · ".join(
        f"{x['exchange']}: {x['ticker']} ({x['security_type'].replace('_', ' ')})"
        for x in company["listings"]
    )
)
coverage = call("GET", f"/companies/{cid}/coverage")
cols = st.columns(3)
cols[0].metric("Source versions", coverage["document_versions"])
cols[1].metric("Searchable documents", coverage["searchable_versions"])
cols[2].metric("Reported SEC observations", coverage["financial_observations"])
tabs = st.tabs(
    [
        "Evidence search",
        "Document library",
        "Import documents",
        "Financial observations",
        "Coverage & activity",
    ]
)

with tabs[0]:
    st.write(
        "Search the documents already imported into this workspace. Every match retains its original document and location."
    )
    query = st.text_input(
        "Search evidence", placeholder="Try revenue, operating profit, or artificial intelligence"
    )
    if query and len(query) >= 2:
        results = call("GET", f"/companies/{cid}/evidence", params={"q": query})
        if not results:
            st.info("No matching evidence. Try fewer keywords or import a relevant document.")
        for result in results:
            with st.expander(f"{result['title']} — {result['locator']}", expanded=True):
                st.text(result["text"])
                st.caption(
                    f"Publication: {result['published_on'] or 'not verified'} | Collected: {result['retrieved_at'][:10]} | Evidence ID: {result['chunk_id']}"
                )
                if result["source_url"]:
                    st.link_button(
                        "Original source website", result["source_url"], key=f"source-{result['chunk_id']}"
                    )
                st.link_button(
                    "Download preserved source",
                    API + f"/documents/{result['version_id']}/original",
                    key=f"download-{result['chunk_id']}",
                )

with tabs[1]:
    documents = call("GET", f"/companies/{cid}/documents")
    if not documents:
        st.info("Your library is empty. Import a public report or upload a document.")
    for doc in documents:
        with st.expander(f"{doc['title']} · {doc['status']}"):
            st.write(
                f"**Period end:** {doc['period_end'] or 'not specified'} · **Published:** {doc['published_on'] or 'not verified'}"
            )
            st.caption(
                f"Collected {doc['retrieved_at']} | Source: {doc['source_id']} | Parser: {doc['parser_version']}"
            )
            st.code(doc["sha256"], language=None)
            if doc["warning"]:
                st.info(doc["warning"])
            st.link_button("Download this source version", API + f"/documents/{doc['id']}/original")
            if st.checkbox("Preview extracted evidence", key=doc["id"]):
                for chunk in call("GET", f"/documents/{doc['id']}/chunks", params={"limit": 5}):
                    st.caption(chunk["locator"])
                    st.text(chunk["text"])

with tabs[2]:
    st.subheader("Public issuer documents")
    st.caption(
        "Selected historical documents for validating the ingestion pipeline; this list is not a latest-filings feed."
    )
    for entry in call("GET", f"/companies/{cid}/catalog"):
        left, right = st.columns([4, 1])
        left.write(entry["title"])
        blocked = entry["key"] == "infosys-financial-overview"
        if blocked:
            left.caption(
                "This page currently rejects automated downloads. You can upload a saved copy below."
            )
        if right.button("Import", key=entry["key"], disabled=blocked):
            try:
                with st.spinner("Downloading and indexing the source…"):
                    result = call("POST", f"/companies/{cid}/imports/catalog/{entry['key']}")
                st.success(f"Import status: {result['status']}")
                st.rerun()
            except (httpx.HTTPError, ValueError) as exc:
                st.error(str(exc))
    st.divider()
    st.subheader("Upload your document")
    with st.form("upload", clear_on_submit=False):
        title = st.text_input("Document title")
        key = st.text_input(
            "Stable document key",
            help="Use the same key when uploading a revised version, e.g. annual-report-2025.",
        )
        source_url = st.text_input("Source URL (optional)")
        uploaded = st.file_uploader(
            "PDF, HTML, or UTF-8 text · maximum 25 MB", type=["pdf", "html", "htm", "txt"]
        )
        published = st.date_input("Publication date, if verified", value=None)
        period = st.date_input("Reporting period end, if known", value=None)
        submitted = st.form_submit_button("Import document")
    if submitted:
        if not uploaded or not title or not key:
            st.error("Provide a document, title, and stable document key.")
        else:
            try:
                metadata = {"title": title, "document_key": key}
                if source_url:
                    metadata["source_url"] = source_url
                if published:
                    metadata["published_on"] = published.isoformat()
                if period:
                    metadata["period_end"] = period.isoformat()
                with st.spinner("Extracting and preserving evidence…"):
                    result = call(
                        "POST",
                        f"/companies/{cid}/imports/upload",
                        data=metadata,
                        files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                    )
                st.success(f"Import status: {result['status']}")
                st.rerun()
            except (httpx.HTTPError, ValueError) as exc:
                st.error(str(exc))
    st.divider()
    st.subheader("SEC structured financial observations")
    st.caption(
        "Requires SEC_USER_AGENT with your real contact email in the local .env file. Imports reported concepts and units without cross-company normalization."
    )
    if st.button("Import SEC company facts"):
        try:
            with st.spinner("Importing reported SEC observations…"):
                result = call("POST", f"/companies/{cid}/imports/sec")
            st.success(f"Import status: {result['status']}")
            st.rerun()
        except (httpx.HTTPError, ValueError) as exc:
            st.error(str(exc))

with tabs[3]:
    st.info(
        "Reported observations preserve revisions, units, and source accessions. Do not add overlapping revenue concepts or compare different reporting periods."
    )
    facts = call("GET", f"/companies/{cid}/facts")
    if facts:
        st.dataframe(facts, width="stretch")
    else:
        st.write(
            "No structured financial observations imported. PDF and HTML sources are currently available as searchable evidence."
        )

with tabs[4]:
    st.write("**Current coverage limitations**")
    for limitation in coverage["limitations"]:
        st.write("• " + limitation)
    st.write(f"**Unknown publication dates:** {coverage['unknown_publication_dates']}")
    st.caption("Collection date is not publication date. Unknown dates remain unknown.")
    st.dataframe(call("GET", f"/companies/{cid}/runs"), width="stretch")
