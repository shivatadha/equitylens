# On-demand research — version 0.2

Version 0.3 adds temporary semantic RAG, source extraction, and passage retrieval. Current details are in [SESSION-RAG.md](SESSION-RAG.md); the original version 0.2 verification record below is retained for history.

Implemented on 26 October 2026 after changing the product from a stored company library to request-time research.

## User flow

Type a company name → retrieve live matches → confirm the listing → fetch financial history, current news, and reported project/industry developments → show a temporary research report.

The default service does not query PostgreSQL, require preloaded company records, ingest reports into the old library, or write company research to disk. Re-running research makes fresh upstream calls. Follow-up requests read only the current session.

## Working features

- Dynamic equity lookup for India and the US, with explicit company confirmation.
- Annual and quarterly financial observations where the public provider supplies them.
- Revenue growth and operating margin calculated in Python from matched periods and currencies.
- Current news window of 1, 7, 30, or 90 days; strict publication-date filtering.
- Project and industry discovery through separately labelled headline searches, using at least a 30-day window.
- Source links, timestamps, duplicate grouping, and explicit source/coverage gaps.
- Request-time LangGraph workflow with no persistent checkpointer or remote tracing.
- Token-protected in-memory research sessions with a 30-minute default TTL, periodic cleanup and explicit clearing.
- Optional Tavily web excerpts, EODHD company profiles, and frontier-model API synthesis.
- Read-only stdio MCP tools: lookup_company, research_company, get_research.
- Session follow-up source matching; AI synthesis of follow-ups if a model API is configured.
- Optional report JSON download initiated by the user.

## Source decisions

The default working development flow uses public Yahoo Finance lookup/financial-history endpoints and Google News RSS. These are unofficial, best-effort sources. They have no negotiated service guarantee. Their availability and coverage must be assessed before public production deployment.

The app does not scrape Screener. Screener's public terms restrict copying and public display; an authorized integration must be established before making it a source dependency. The product does not require Screener specifically.

For a few documented symbols, including TSLA, EODHD's public demo endpoint supplies a business profile. The app labels demo provenance. It never substitutes a demo company's data for another company. Broader profiles use the user's API key and remain dependent on provider coverage/plan permissions.

No articles are automatically crawled from search-result URLs. Google RSS results are headline evidence, not verified article bodies. Tavily excerpts, if configured, are labelled search excerpts. Official announcement completeness and source-body verification are not implemented.

## Retention

Backend reports exist only in process memory and expire after the configured TTL. Lookup selections are signed and expire after 15 minutes. The UI clears expired research on its 30-second refresh. Per-request source keys are not written to configuration or added to reports. .env may contain credentials deliberately configured by the user.

Access logging is disabled for the default backend. No company-content traces are sent by LangGraph. Explicit downloads and external providers have separate retention behavior. This is a local single-user app, not a multi-tenant deployment.

The previous archive files and database were preserved; they are not consulted by the live service. No new company database is created.

## Verification

- 40 automated tests passed: the 23 archive regressions and 17 new live-flow tests.
- Live Tesla request: 63 financial observations, 10 recent news results, 8 project-category results and 8 industry-category results in the verification run.
- Live Reliance request: 47 financial observations, 9 recent news results, 10 project-category results and 7 industry-category results in the verification run.
- Counts reflect one request-time snapshot and will change.
- Streamlit AppTest passed report rendering, financial tables/charts, dynamic search, and session clearing.
- The actual stdio MCP transport initialized, exposed three read-only tools, and resolved Tesla live using the installed MCP Python SDK v2.
- The optional Tavily/EODHD-key and local-model branches have not been live-tested with private credentials. A model is not installed or running as part of this build.
- Citation-ID validation removes unknown references. It does not independently prove semantic support.

## Next improvements

1. Choose supported production data/search providers and validate permissions and country coverage.
2. Add primary filing and official announcement retrieval with bounded source-body extraction.
3. Independently reconcile financial facts against filings.
4. Connect and evaluate a model for cited business research and conditional scenarios.
5. Add operational rate limits, stronger resource controls, deployment authentication, and provider-quality evaluation.

References:
- https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds
- https://docs.tavily.com/documentation/api-reference/endpoint/search
- https://www.screener.in/guides/terms/
