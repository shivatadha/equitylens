# EquityLens evaluation baseline

Run the frozen retrieval fixtures without network or answer-model calls:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --mode bm25
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --mode hybrid
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --dataset evaluation\filing_pilot_2025.json --mode hybrid
```

The runner creates one temporary index per company, as the app does, and reports hit@1/3/5, all required passages found at five, and mean reciprocal rank at five. Missing-period cases are shown but excluded from retrieval accuracy denominators. It prints every question's expected and retrieved passage IDs so misses can be investigated. Hybrid mode fails if the local embedding model is unavailable instead of silently reporting keyword results as hybrid results. The runner clears each index afterward. `--min-all-relevant-at-5` turns the score into a failing CI gate.

The included `evaluation/illustrative_retrieval.json` has 13 **fictional, synthetic** questions covering Indian and US company examples. It checks that the benchmark works and that paraphrases can reveal differences between BM25 and hybrid retrieval. On 27 October 2026, BM25 hit@5 was 11/13 and hybrid hit@5 was 13/13 on this fixture. These scores do **not** measure accuracy on real companies, source extraction, generated answers, or financial facts.

The dated `evaluation/filing_pilot_2025.json` contains nine answerable questions and two missing-period questions about Reliance Industries FY2024-25 and Microsoft FY2025, using short paraphrased passages linked to their own annual reports. On 27 October 2026, both retrieval modes found all required passage IDs for the nine answerable questions within the first five results; BM25 put the first relevant passage first on 8/9 questions, while hybrid did so on 9/9. This is a small, curated retrieval pilot. It does not test whether the live providers extract those passages or whether the app uses official filings during on-demand research.

The optional answer check requires an explicit paid-call flag and defaults to one case:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_answers.py --allow-paid --max-cases 1
```

With the final prompt, all nine answerable cases passed their literal figure, term, and citation checks across two batches. Both missing-period cases returned a cited insufficient-evidence answer. Groq returned an HTTP 429 rate limit during the first answerable batch, so the remaining four cases were run separately after the limit cleared. These are observations from a tiny, curated sample, not an estimated reliability rate. Human review of the printed claims is still necessary, and the runner never saves them.

The generation validator now drops claims containing figures, currencies, or scale words absent from their cited passages, in addition to rejecting unknown or missing passage IDs. It compares literal tokens; it does not prove that a passage semantically supports a claim or that a number and unit belong together. Legitimate reformatted values or calculations may be omitted. Research output retains that limitation.

## Building the real benchmark

1. Freeze dated, legally shareable excerpts from official company filings for a reviewed set of Indian and US companies. Record source URL, document date, page or section, reporting basis, currency, and period for each passage. Do not publish licensed third-party feed content without permission.
2. Have a reviewer write questions and expected passage IDs before tuning retrieval. Include paraphrases, period changes, consolidated versus standalone figures, conflicting evidence, missing information, and questions requiring abstention.
3. Run both retrieval modes against the same frozen corpus. Report sample counts and per-company misses, not only averages. Preserve a held-out set for final evaluation.
4. With an explicitly configured answer model and budget, separately grade numeric correctness, period and currency correctness, citation support, and abstention. Review claims against cited passages manually or with a documented adjudication process; valid citation IDs and matching figures alone are insufficient.
5. Repeat the benchmark after changes to prompts, extraction, chunking, retrieval, or model configuration. Publish failures and latency/cost alongside improvements.

The illustrative fixture is a mechanics check and must never be presented as a production reliability score.

## Live answer audit

`evaluation/live_answer_audit.json` contains six prewritten questions against the Reliance FY2024-25 and Microsoft FY2025 annual-report references. It includes four answerable reference questions and two deliberately unavailable future-period questions. The runner asks the live app to look up and research each company, then inspects retrieval and optional follow-up answers. Reference figures are **not** injected into the app's research session.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_live_answers.py --output data\live-answer-audit-retrieval.json
.\.venv\Scripts\python.exe scripts\evaluate_live_answers.py --allow-paid --output data\live-answer-audit-paid.json
```

The first command makes no answer-model requests, even when `.env` has a key. The second can make at most one configured model request per selected question. Use `--max-cases 1` or repeated `--case-id ID` options to limit cost. Each run creates temporary research sessions, deletes them afterward, and writes local JSON only when `--output` is supplied. The JSON includes retrieved passages and model claims for manual review; do not publish it without checking source redistribution rights.

On 27 October 2026, a six-case model-enabled run produced cited responses for all six. Microsoft FY2025 revenue and operating cash flow returned the expected **provider** amounts. Reliance FY2025 consolidated operating cash flow correctly declined to assert a consolidated basis because the live provider does not verify it; the “revenue after GST” question also abstained because the provider's generic revenue observation does not establish that definition. Both FY2035 questions returned a cited coverage-gap statement. See the [dated audit record](LIVE-ANSWER-AUDIT-2026-10-27.md). This is a six-question pilot with changing public endpoints and only one model run; it is not an accuracy rate or proof of official-filing reconciliation.

## Live flow check

Run `scripts/evaluate_live_flow.py --require-official-nse` with outbound access to measure the current request-time flow for Tesla, Reliance, and RBL Bank. It uses the same service as the app, disables answer-model calls, retrieves cash-flow passages, and deletes each temporary session. It reports per-company source counts and latency without writing source data or reports to disk. On 27 October 2026, all three checks passed; RBL Bank had one ticker-matched official NSE announcement and one successfully read source body. Tesla and Reliance had zero body reads in that run. The rolling feed changes, so this is a dated observation, not a stable CI gate.

Add `--require-sec` after configuring an identifiable `SEC_USER_AGENT` to require a live SEC amount comparison for Tesla. The checked mock responses in unit tests do not substitute for that live check.
