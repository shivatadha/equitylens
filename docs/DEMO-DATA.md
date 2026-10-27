# Public portfolio demonstration data

**Every company, price, financial figure, and scenario in public demo mode is synthetic.** These fixtures do not describe Reliance, Microsoft, or any other actual issuer. They are not market quotes, filings, forecasts, or investment research.

## Fictional companies

| Symbol | Name | Sample currency | Sample listing |
| --- | --- | --- | --- |
| DEMO-IN | EquityLens Demo India Ltd | INR | NSE (fictional) |
| DEMO-US | EquityLens Demo US Inc. | USD | NASDAQ (fictional) |

The displayed exchange names demonstrate formatting and market selection. Neither fictional company is represented as an actual listing.

## Sample prices and financials

`equitylens/live/demo.py` generates 500 weekday price bars from deterministic trend and sine functions. Open, high, low, close, and volume are generated examples. Dates end on a weekday before the current day; they do not reconstruct exchange holidays or observed market activity. Adjusted closes equal the generated closes, and no actual dividend or split history is used. There is no exact quote timestamp.

Four sample annual periods contain generated revenue, operating income, net income, cash flow, capital expenditure, debt, and cash balances. Sample valuation inputs demonstrate the same calculation and display functions used by live mode. Dates, figures, and growth patterns are illustrative.

## News-1

The sample subscription-growth story is a fictional scenario used to demonstrate news cards and evidence search.

## News-2

The sample operating-cost story is fictional and does not report an actual business event.

## Projects-1

The sample product pilot demonstrates project discovery without claiming a real company announcement.

## Industry-1

The sample competition scenario demonstrates industry evidence and is not current market news.

## Questions and citations

The public demo performs keyword retrieval over its generated passages. Its extractive answer copies a selected sample passage and cites that passage's identifier. This is not an LLM answer and does not call a model API. The separate live mode supports local embeddings and optional remote-model answers.

## Scope

Public demo mode calls no market-data provider, news feed, filing service, or model API and needs no API key. It preserves the invitation password, session expiry, explicit clearing, source inspection, JSON export, and normal chart interaction. Downloaded reports include `data_mode: demo` and synthetic source labels.

Use `EQUITYLENS_DATA_MODE=live` only when you deliberately want request-time sources. Live-source availability and usage terms remain separate from this self-contained public demonstration; demo mode does not resolve the Yahoo HTTP 429 problem.
