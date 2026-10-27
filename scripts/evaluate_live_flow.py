"""Measure the current request-time flow without storing reports or calling the answer model."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import monotonic

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from equitylens.config import Settings
from equitylens.live.service import ResearchService
from equitylens.live.types import ResearchInput

CASES = (
    ("Tesla", "US", "TSLA", "USD"),
    ("Reliance", "IN", "RELIANCE.NS", "INR"),
    ("RBL Bank", "IN", "RBLBANK.NS", "INR"),
)


async def evaluate(*, require_official_nse=False, require_sec=False):
    settings = Settings(llm_api_key=SecretStr(""))  # The evaluation never incurs answer-model calls.
    service = ResearchService(settings)
    cases = []
    for query, market, symbol, currency in CASES:
        start = monotonic()
        matches = await service.lookup(query, market)
        match = next((item for item in matches if item["symbol"] == symbol), None)
        if match is None:
            cases.append({"symbol": symbol, "passed": False, "reason": "listing_not_found"})
            continue
        result = await service.research(ResearchInput(company_token=match["company_token"], news_days=7))
        try:
            report = result["report"]
            retrieval = await service.retrieve(
                result["session_id"], result["session_token"], "operating cash flow", top_k=6
            )
            official = [item for item in report["evidence"] if item["category"] == "official_announcement"]
            cash_hit = any(
                item["category"] == "financial" and "operating cash flow" in item["text"].lower()
                for item in retrieval["passages"]
            )
            passed = bool(
                report["stock"]
                and report["stock"]["currency"] == currency
                and report["financials"]
                and report["rag"]["passages"]
                and cash_hit
            )
            if symbol == "RBLBANK.NS" and require_official_nse:
                passed = passed and bool(official) and report["rag"]["body_sources_read"] > 0
            if symbol == "TSLA" and require_sec:
                passed = passed and bool(report["filing_reconciliation"])
            cases.append(
                {
                    "symbol": symbol,
                    "passed": passed,
                    "seconds": round(monotonic() - start, 2),
                    "financial_rows": len(report["financials"]),
                    "evidence_items": len(report["evidence"]),
                    "official_nse_notices": len(official),
                    "body_sources_read": report["rag"]["body_sources_read"],
                    "sec_amount_comparisons": len(report["filing_reconciliation"]),
                    "cashflow_retrieved": cash_hit,
                    "generation": report["generation"]["state"],
                    "coverage_gaps": len(report["gaps"]),
                }
            )
        finally:
            service.sessions.delete(result["session_id"], result["session_token"])
    return {"passed": all(case["passed"] for case in cases), "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-official-nse", action="store_true")
    parser.add_argument("--require-sec", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(evaluate(require_official_nse=args.require_official_nse, require_sec=args.require_sec))
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
