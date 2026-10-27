"""Audit live research and optional model answers against prewritten filing questions.

This reports observable behavior and leaves financial correctness/citation support for
human review. No model call occurs unless --allow-paid is supplied.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from equitylens.config import Settings
from equitylens.live.service import ResearchService
from equitylens.live.types import ResearchInput

ROOT = Path(__file__).resolve().parents[1]


async def evaluate(dataset, *, allow_paid=False, max_cases=None, case_ids=None):
    configured = Settings()
    if allow_paid and not configured.generation_configured():
        raise ValueError("Configure LLM_API_KEY and LLM_MODEL before paid answer evaluation.")
    # Keep initial report generation off. The budget covers only selected follow-ups.
    service = ResearchService(configured.model_copy(update={"llm_api_key": SecretStr("")}))
    cases = [case for case in dataset["cases"] if not case_ids or case["id"] in case_ids]
    cases = cases[:max_cases] if max_cases is not None else cases
    results = []
    active = None
    try:
        for case in cases:
            company_key = (case["query"], case["market"], case["symbol"])
            if active is None or active[0] != company_key:
                if active is not None:
                    service.sessions.delete(active[1]["session_id"], active[1]["session_token"])
                matches = await service.lookup(case["query"], case["market"])
                match = next((item for item in matches if item["symbol"] == case["symbol"]), None)
                if match is None:
                    results.append({"id": case["id"], "state": "listing_not_found"})
                    active = None
                    continue
                session = await service.research(ResearchInput(company_token=match["company_token"], news_days=7))
                active = (company_key, session)
            session = active[1]
            start = monotonic()
            retrieval = await service.retrieve(
                session["session_id"], session["session_token"], case["question"], top_k=6
            )
            row = {
                "id": case["id"],
                "symbol": case["symbol"],
                "question": case["question"],
                "expected": case["expected"],
                "answerability": case["answerability"],
                "reference_url": case["reference_url"],
                "reference_locator": case["reference_locator"],
                "research_evidence_items": len(session["report"]["evidence"]),
                "source_bodies_read": session["report"]["rag"]["body_sources_read"],
                "retrieved": [
                    {
                        "id": match["id"],
                        "source_id": match["source_id"],
                        "category": match["category"],
                        "evidence_level": match["evidence_level"],
                        "title": match["title"],
                        "url": match["url"],
                        "text": match["text"],
                    }
                    for match in retrieval["passages"]
                ],
            }
            if allow_paid:
                service.settings = configured
                service.provider.settings = configured
                answer = await service.follow_up(
                    session["session_id"], session["session_token"], case["question"]
                )
                claims = [claim for section in answer["analysis"] for claim in section["claims"]]
                known = {match["id"] for match in answer["matches"]}
                row.update(
                    {
                        "state": "answered" if claims else "no_answer",
                        "failure_code": answer["failure_code"],
                        "claims": claims,
                        "cited_passage_ids_valid": all(
                            set(claim["source_ids"]).issubset(known) for claim in claims
                        ),
                        "answer_matches": [
                            {
                                "id": match["id"],
                                "title": match["title"],
                                "url": match["url"],
                                "text": match["text"],
                            }
                            for match in answer["matches"]
                        ],
                    }
                )
                service.settings = service.settings.model_copy(update={"llm_api_key": SecretStr("")})
                service.provider.settings = service.settings
            else:
                row["state"] = "retrieval_only"
            row["seconds"] = round(monotonic() - start, 2)
            results.append(row)
    finally:
        if active is not None:
            service.sessions.delete(active[1]["session_id"], active[1]["session_token"])
    return {
        "dataset": dataset["kind"],
        "run_at": datetime.now(UTC).isoformat(),
        "paid_answers_enabled": allow_paid,
        "model": configured.llm_model if allow_paid else None,
        "cases": results,
        "review_required": "Check each claim's figure, unit, period, reporting basis, and actual cited passage. A valid citation ID is not proof of support.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true", help="Allow one model request per selected case.")
    parser.add_argument("--max-cases", type=int, default=None, help="Limit cases and model costs.")
    parser.add_argument("--case-id", action="append", help="Run only the named case; repeat if needed.")
    parser.add_argument("--output", type=Path, help="Write local JSON for manual review.")
    args = parser.parse_args()
    if args.max_cases is not None and args.max_cases < 1:
        parser.error("--max-cases must be positive.")
    dataset = json.loads((ROOT / "evaluation/live_answer_audit.json").read_text(encoding="utf-8"))
    if args.case_id and set(args.case_id) - {case["id"] for case in dataset["cases"]}:
        parser.error("Unknown case ID.")
    result = asyncio.run(
        evaluate(dataset, allow_paid=args.allow_paid, max_cases=args.max_cases, case_ids=args.case_id)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
