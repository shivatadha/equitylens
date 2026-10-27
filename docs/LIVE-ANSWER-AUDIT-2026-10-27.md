# Live answer audit — 27 October 2026

Six questions were prewritten in `evaluation/live_answer_audit.json`, using [Reliance consolidated statements](https://www.ril.com/ar2024-25/pdf/Consolidated-Financial-Statements.pdf) and [Microsoft's FY2025 annual report](https://www.microsoft.com/investor/reports/ar25/) as reviewer references. The live app obtained its own sources; the reference passages were not supplied to it. The final run used the configured Groq `openai/gpt-oss-20b` for eligible follow-ups. The local detailed record is `data/live-answer-audit-paid.json`.

| Case | Observed answer | Review |
|---|---|---|
| RIL-CASH-2025 | The provider observation does not verify the requested consolidated basis. | Appropriate abstention. The Yahoo amount equals the annual-report number after exact crore conversion, but matching amount alone does not prove reporting basis. |
| RIL-REVENUE-2025 | Insufficient evidence for revenue *from operations after GST recovered*. | Appropriate abstention. A generic provider “Revenue” value is not the requested statement line. |
| RIL-MISSING-2035 | Evidence does not cover the requested reporting period. | Appropriate cited coverage gap. |
| MSFT-REVENUE-2025 | Provider reports 281,724 USD million for the 12-month period ended 2025-06-30; basis unverified. | Numeric value matches the annual report. Citation is to the live provider observation, not a read annual-report body. |
| MSFT-CASH-2025 | 136,162 USD million in fiscal 2025. | Numeric value matches the annual report. Citation is to the live provider observation. |
| MSFT-MISSING-2035 | Evidence does not cover the requested reporting period. | Appropriate cited coverage gap. |

All six responses cited passages selected from the temporary research session. In both company runs, **zero source bodies were read**. The observed correctness assessment therefore rests on the two official reports checked separately by the reviewer; the app did not independently verify the provider's accounting basis in those sessions. Public endpoints and model output can change. Six questions cannot estimate the system's general accuracy.

The first run exposed an unsupported “consolidated” claim. The validator now rejects that basis when the cited provider passage explicitly says it is unverified, and the app gives a cited basis-gap response. The audit also exposed unstable formatting of USD millions and silent no-answer behavior for future periods. Exact display conversions and deterministic coverage-gap responses now address those cases. This record reports the final rerun, not just the passing cases.
