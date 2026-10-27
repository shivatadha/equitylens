# EquityLens

Company research for India and the US. **Milestone 1: the evidence foundation.** All application logic is Python. This is a local development build, not a production deployment or a completed research agent.

## What is implemented

- FastAPI API and a Streamlit evidence workspace, backed by PostgreSQL 17.
- Separate company and exchange-listing identities: Microsoft and Infosys; Infosys's NSE, BSE and NYSE listings resolve to one company.
- Curated issuer-source imports and user PDF/HTML/text uploads (25 MB limit).
- Immutable source objects addressed by SHA-256, document revisions, duplicate detection, recorded ingestion outcomes.
- Page/text-offset citations, full-text evidence search, downloads of preserved original sources.
- Strict observed-as-of filtering: evidence must have a stated publication date and have been collected by the cutoff.
- SEC companyfacts adapter with explicit contact configuration; selected reported concepts retain decimal values, units, periods, accessions and revisions.
- Alembic database migration, isolated tests, dependency lock, local startup scripts.

## Start on this computer

Open this folder in VS Code. Docker Desktop must be running.

```powershell
.\.venv\Scripts\python.exe scripts\setup.py
docker compose up -d db
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m equitylens.cli seed
.\.venv\Scripts\python.exe -m equitylens.cli serve
```

In a second terminal:

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui.py
```

- Interface: http://127.0.0.1:8501
- API explorer: http://127.0.0.1:8001/docs

To import the curated public sample reports:

```powershell
.\.venv\Scripts\python.exe -m equitylens.cli import-samples
```

These include historical annual reports selected to test the pipeline. They are not automatically the most recent reports. The live Infosys overview can change and is retained as a versioned snapshot.

## Fresh machine

Use Python 3.12 and create `.venv`, then install `requirements.lock.txt`. Run setup, database migration and seed commands above. PostgreSQL credentials are generated in `.env`, which is ignored by Git. The database port is local-only at 55432. Source objects live under `data/objects`; the database lives in the named Docker volume. Both must be backed up together.

## SEC configuration

Set `SEC_USER_AGENT` in `.env` to your project name and real contact email, then restart the API. The SEC adapter uses a narrow URL pattern and a local limiter of four requests per second. A shared limiter is required before multiple backend processes are deployed. No contact address or API credentials have been invented.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check equitylens tests scripts ui.py
```

The default tests use temporary SQLite databases for fast local checks. The same suite can run against an isolated PostgreSQL database named `equitylens_test` by setting `TEST_DATABASE_URL`. Tests refuse other PostgreSQL database names. Do not point tests at your application database.

## Important behavior

- Upload publication dates are labelled user-provided. Unknown dates remain unknown; retrieval time never substitutes for publication time.
- Using the same stable document key with different content creates another immutable version. Repeating identical content returns the existing version; altered metadata alone does not revise that version.
- SEC JSON facts are reported observations, not a normalized financial model. Reporting basis needs source review; overlapping concepts must not be added together.
- Text extraction is not financial statement extraction. HTML table relationships and PDF numeric cells are not automatically normalized yet.
- Empty/scanned PDFs are flagged for review. OCR is not present.
- Fetching is restricted to curated issuer documents and the selected company's SEC facts endpoint. Uploaded source links are stored as metadata, never fetched.
- Original HTML is downloaded as an attachment, never served as active same-origin HTML.
- Parsing runs in a subprocess with a timeout. This is not a hardened public-file sandbox; memory isolation, upload quotas and durable workers remain future work.

## Next milestones

1. Financial schema mapping and review, statement/table extraction, reconciliation and calculation functions.
2. Durable research jobs, model integration, evidence-backed answers and evaluation sets.
3. Management commitment tracking, news/event adapters and report comparisons.
4. Workspace authentication, tenant isolation, quotas, monitored deployment and restore drills.

Milestone 1 has no AI synthesis, news feed, valuation, trading, multi-user authentication, background job recovery, semantic embeddings, or MCP server. The API contracts are the foundation for those additions. Keep the service bound to localhost.

## Sources and design references

- SEC API: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC fair access: https://www.sec.gov/about/webmaster-frequently-asked-questions
- Microsoft annual report: https://www.microsoft.com/investor/reports/ar25/index.html
- Infosys annual report: https://www.infosys.com/investors/reports-filings/annual-report/annual/documents/infosys-ar-25.pdf
- Infosys financial overview: https://www.infosys.com/investors/reports-filings/financials/data-sheet.html

Issuer documents are collected for local research. Source display and redistribution rights need review before a public product launch.
