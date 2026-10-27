# Milestone 1 — Evidence foundation

Implemented and verified on 26 October 2026.

## Working system

Python API + Python interface → PostgreSQL 17 + immutable local source objects.

The company registry contains Microsoft and Infosys. Infosys has three distinct exchange listings linked to one company. The database stores sources, documents, content versions, evidence chunks, ingestion outcomes, and selected reported SEC observations.

Public documents successfully collected and parsed:

| Company | Document | Evidence chunks |
|---|---|---:|
| Microsoft | Annual Report 2025, HTML | 142 |
| Infosys | Integrated Annual Report 2024–25, PDF | 941 |

These historical documents validate the pipeline. They do not establish up-to-date or complete company coverage. Publication dates were not independently confirmed during ingestion and remain unset. Reporting period ends are recorded separately.

The Infosys financial overview webpage returned HTTP 403. Its automatic import is disabled in the interface; the official annual-report PDF succeeded. No access-control bypass was attempted.

## Verification completed

- 23 tests passed using temporary SQLite databases.
- The same 23 tests passed using a separate PostgreSQL test database.
- Alembic reports that the migrated schema matches the current models.
- Code checks passed.
- Running API returned real-source evidence for both companies.
- Streamlit AppTest passed initial rendering, evidence search and company switching.

Tests cover company identity, document versioning, byte preservation, source-company separation, unknown dates, strict observed-as-of cutoffs, failure records, textless-PDF handling, numeric precision, accession revisions, rejected destinations, redirects, size limits and cross-origin requests.

The API test dependency emits a deprecation notice for its httpx test transport. Tests pass; this dependency change should be addressed during the next dependency maintenance pass.

## Boundaries of this milestone

This is a working local development foundation, not a finished production service. Imports are synchronous and recorded; there is no durable recovery after a process crash yet. PostgreSQL full-text search is implemented; semantic embeddings are not. The first milestone does not infer financial statement tables, generate AI reports, ingest news, or monitor a watchlist.

SEC adapter behavior is tested with explicitly synthetic fixtures. Live SEC companyfacts retrieval was not run because a real user-agent contact email has not been configured. No artificial financial fixtures were inserted into the application database.

## Next step

Milestone 2: preserve financial table structure, create reviewable statement observations, map a small set of core financial metrics, and reconcile each value with its original source before charting or AI interpretation.

Start with revenue, operating profit, net income, assets, liabilities, operating cash flow and capital expenditure. Keep consolidated/standalone, currency, reported scale, reporting standard, and annual/quarter/year-to-date periods explicit. Derived metrics must retain their input observation IDs.
