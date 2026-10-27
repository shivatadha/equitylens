# EquityLens: evidence-first company research

**Snapshot: 27 October 2026.** EquityLens lets a user confirm an Indian or US listed company, fetch current public-source data, inspect stock and financial history, and ask questions over a temporary source index. It is an advanced portfolio prototype, not a validated investment adviser or production financial-data service.

## The engineering problem

Company research mixes changing prices, dated financial statements, headlines, and extracted pages. A fluent answer can hide a wrong reporting period, currency, listing, or source. EquityLens keeps these distinctions visible and uses deterministic calculations for supported financial measures. A frontier-model API is optional for written answers; semantic retrieval uses a separate local embedding model.

```mermaid
flowchart LR
    A[Company lookup and listing confirmation] --> B[Live public sources]
    B --> C[Validate periods, currencies and stock calculations]
    C --> D[Bounded extraction and temporary passage index]
    D --> E[Hybrid retrieval: BM25 and embeddings]
    E --> F[Evidence view and optional API answer]
    F --> G[Citation, figure and unit checks]
    G --> H[Report with sources and coverage gaps]
```

Research sessions are held in process memory with a default 30-minute expiry and bearer access. Source reading has public-network, robots, byte, page, and time limits. The LangGraph pipeline is sequential; it does not yet investigate contradictions iteratively.

## What was measured

- **Regression:** 181 tests passed with Ruff clean on 27 October 2026.
- **Live flow evaluation:** The read-only `scripts/evaluate_live_flow.py --require-official-nse` run completed for Tesla, Reliance, and RBL Bank. All three returned financial history and retrieved operating-cash-flow evidence. RBL Bank had one exact-ticker NSE announcement and one linked PDF body read; Tesla and Reliance had no source body read in that run. The runner disables answer-model calls and deletes each temporary session.
- **Offline synthetic retrieval:** 13 fictional questions; BM25 found required evidence within five results on 11/13, hybrid on 13/13. This demonstrates a paraphrase improvement in a tiny fixture, not real-world accuracy.
- **Source-backed filing pilot:** Nine answerable questions from dated Reliance and Microsoft annual reports, plus two missing-period questions. Both retrieval modes found the required pilot passages within five results for all nine answerable questions; hybrid ranked the first relevant passage first in 9/9, BM25 in 8/9.
- **Groq answer pilot:** `openai/gpt-oss-20b` gave nine answers passing literal expected-figure, term, and citation checks across final-prompt runs. Both missing-period questions produced cited insufficient-evidence answers. Provider rate limiting interrupted one batch; the remaining cases passed when run separately. These checks use frozen, paraphrased filing passages and do not measure live extraction or general answer accuracy.
- **Live answer audit:** Six prewritten questions were run through request-time company lookup, research, retrieval, and follow-up. The final run returned two Microsoft amounts matching annual-report references, two appropriate Reliance evidence-limit responses, and two cited future-period coverage gaps. Neither live company session read an official filing body. See the [dated audit](LIVE-ANSWER-AUDIT-2026-10-27.md).

The pilot sources are the [Reliance FY2024-25 consolidated financial statements](https://www.ril.com/ar2024-25/pdf/Consolidated-Financial-Statements.pdf) and [Microsoft FY2025 annual report](https://www.microsoft.com/investor/reports/ar25/). The fixture records period, currency, reporting basis, and a locator for each passage. See [evaluation methodology](EVALUATION.md) and the JSON fixtures in `evaluation/`.

## Failure handling and limitations

The generator receives only bounded retrieved context. Unknown citations and claims with figures or units absent from cited passages are withheld. A provider aggregate cannot be called consolidated or standalone when its cited passage says the basis is unverified. Narrow total-revenue questions can receive an exact, deterministic unit conversion from a matching annual provider observation; missing periods and unverified reporting bases receive cited coverage-gap responses. Matching tokens cannot establish semantic support, and a legitimate value reformatted by the model may be withheld.

The live public Yahoo and Google RSS integrations are best-effort sources. NSE's public announcements RSS supplies official, exact-ticker feed summaries for some Indian listings; it is a rolling window, and an empty result does not mean there were no filings. A linked NSE PDF was read in the RBL Bank live check. Indian official financial facts and a full filing archive remain unintegrated. For US listings, an SEC contact setting enables request-time annual 10-K XBRL observations with accession links and same-period USD amount comparisons to Yahoo. Tests cover identity and period selection using mocked SEC responses; live SEC retrieval has not been verified on this machine because `SEC_USER_AGENT` is not configured. Amount equality does not establish accounting-basis equivalence. The answer pilot uses curated filing passages rather than a representative sample from the live workflow. A fresh Linux container image installed the pinned dependencies and embedding model; the two-service local stack passed API and UI health checks, with private API-to-UI connectivity. There is no demonstrated public hosted deployment, user-account authentication, multi-worker session storage, load test, or monitoring dashboard. These are the next proof points before claiming production readiness.

## Reproduce the current evidence

An [invite-only demo configuration](DEMO.md) is now available for a single host with an HTTPS reverse proxy. It has not been publicly deployed, so it does not change the deployment limitation above.
The [Streamlit Community Cloud entrypoint](STREAMLIT-CLOUD.md) runs the API inside the Streamlit process and has a tested password gate. Cloud deployment has not yet been performed.

### Public sample deployment update

The hosted page and server health endpoint were subsequently reached successfully, but Yahoo company lookup returned HTTP 429 from Community Cloud. Exact-ticker recovery did not establish usable hosted access. The public Cloud entrypoint now defaults to a separate synthetic demonstration with fictional Indian and US companies; the underlying live-source restriction remains unresolved. Sample mode performs real keyword retrieval and cited extraction over generated passages without a market-data service or model API. End-to-end tests exercise both sample markets with external HTTP requests blocked, and a Streamlit AppTest covers unlocking, sample lookup, report rendering, and the sample-price label. See [demo-data methodology](DEMO-DATA.md).

Use `python -m pytest -q` and `python -m ruff check .` for local regression. Run `python scripts/evaluate_retrieval.py --dataset evaluation/filing_pilot_2025.json --mode hybrid` for the offline filing-pilot retrieval check. `scripts/evaluate_live_flow.py --require-official-nse` checks current request-time sources without saving reports or calling the answer model. `scripts/evaluate_answers.py` requires `--allow-paid` and a configured API key; it defaults to one case. The GitHub Actions workflow runs tests, linting, and keyword-only retrieval gates without any provider key.
