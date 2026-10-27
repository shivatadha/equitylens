"""Optional paid model check on the small, frozen public-filing pilot."""

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from equitylens.config import Settings
from equitylens.live.llm import APIModel
from equitylens.live.service import validate_analysis
from equitylens.live.types import CompanyMatch, Evidence
from scripts.evaluate_retrieval import evaluate


def figures(text):
    pattern = r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?(?![A-Za-z0-9])"
    return {match.group().replace(",", "").lstrip("+") for match in re.finditer(pattern, text)}


async def run(dataset, limit, start=1):
    settings = Settings(llm_max_output_tokens=768)
    if not settings.generation_configured():
        raise ValueError("Configure LLM_API_KEY and LLM_MODEL before running paid answer evaluation.")
    retrieval = evaluate(dataset, mode="hybrid")
    by_id = {row["id"]: row for row in dataset["passages"]}
    by_question = {row["id"]: row for row in dataset["questions"]}
    model = APIModel(settings)
    results = []
    for result in retrieval["cases"][start - 1 : start - 1 + limit]:
        question = by_question[result["id"]]
        company = CompanyMatch(
            symbol="RIL" if question["market"] == "IN" else "MSFT",
            name=question["company"],
            exchange="NSE" if question["market"] == "IN" else "NASDAQ",
            market=question["market"],
            retrieved_at=datetime.now(UTC),
        )
        evidence = [
            Evidence(
                id=identifier,
                title=by_id[identifier]["title"],
                url=by_id[identifier]["url"],
                publisher=question["company"],
                retrieved_at=datetime.now(UTC),
                category="financial",
                evidence_level="page_excerpt",
                locator=by_id[identifier]["locator"],
                content=by_id[identifier]["text"],
            )
            for identifier in result["retrieved"]
        ]
        try:
            payload = await model.generate(company, evidence, [], question=question["question"])
            sections, dropped = validate_analysis(payload, evidence)
            claims = [claim for section in sections for claim in section["claims"]]
            text = " ".join(claim["text"] for claim in claims)
            cited = {ref for claim in claims for ref in claim["source_ids"]}
            missing_figures = sorted(set(question["expected_figures"]) - figures(text))
            missing_citations = sorted(set(question["relevant"]) - cited)
            missing_terms = sorted(
                term for term in question.get("expected_terms", []) if term.lower() not in text.lower()
            )
            answerable = question.get("answerability") != "unanswerable_from_fixture"
            results.append(
                {
                    "id": question["id"],
                    "outcome": "answer",
                    "passes_literal_checks": (
                        bool(claims) and not missing_figures and not missing_citations and not missing_terms
                    )
                    if answerable
                    else None,
                    "abstention_language": bool(
                        re.search(r"\b(?:insufficient|cannot|unavailable|not provided|not include|no data)\b", text, re.I)
                    )
                    if not answerable
                    else None,
                    "missing_figures": missing_figures,
                    "missing_citations": missing_citations,
                    "missing_terms": missing_terms,
                    "dropped_claims": dropped,
                    "claims_for_human_review": claims,
                }
            )
        except httpx.HTTPStatusError as exc:
            results.append(
                {
                    "id": question["id"],
                    "outcome": "provider_error",
                    "passes_literal_checks": False,
                    "http_status": exc.response.status_code,
                }
            )
            if exc.response.status_code == 429:
                break
        except Exception as exc:
            results.append(
                {
                    "id": question["id"],
                    "outcome": "error",
                    "passes_literal_checks": False,
                    "error_type": type(exc).__name__,
                }
            )
    return {
        "dataset_kind": dataset["kind"],
        "model": settings.llm_model,
        "tested": len(results),
        "answers": sum(row["outcome"] == "answer" for row in results),
        "literal_check_passes": sum(row["passes_literal_checks"] is True for row in results),
        "warning": "Literal checks are not semantic citation review or end-to-end live-source validation.",
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true", help="Required to send paid model requests.")
    parser.add_argument("--start-case", type=int, default=1, help="One-based case number to start from.")
    parser.add_argument("--max-cases", type=int, default=1)
    args = parser.parse_args()
    if not args.allow_paid:
        parser.error("Use --allow-paid to explicitly enable model requests.")
    path = Path(__file__).resolve().parents[1] / "evaluation/filing_pilot_2025.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    count = len(dataset["questions"])
    if not 1 <= args.start_case <= count or not 1 <= args.max_cases <= count + 1 - args.start_case:
        parser.error(f"Case range must stay within the {count}-case pilot.")
    print(json.dumps(asyncio.run(run(dataset, args.max_cases, args.start_case)), indent=2))


if __name__ == "__main__":
    main()
