"""Score frozen, reviewed passage IDs without making source or answer-model API calls."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from equitylens.config import Settings
from equitylens.live.rag import LocalEmbedder, Passage, SessionIndex


class LexicalOnly:
    model_name = "disabled for benchmark"

    def documents(self, texts):
        raise OSError("Semantic retrieval disabled for benchmark.")


def evaluate(dataset, *, mode="hybrid"):
    passages = dataset["passages"]
    questions = dataset["questions"]
    question_by_id = {row["id"]: row for row in questions}
    ids = [row["id"] for row in passages]
    if len(ids) != len(set(ids)):
        raise ValueError("Passage IDs must be unique.")
    company_ids = {}
    for row in passages:
        company_ids.setdefault(row["company"], set()).add(row["id"])
    if not questions or any(
        not row["relevant"] or not set(row["relevant"]) <= company_ids.get(row["company"], set())
        for row in questions
    ):
        raise ValueError("Each question must name existing relevant passage IDs.")
    timestamp = datetime.now(UTC).isoformat()
    embedder = LocalEmbedder(Settings()) if mode == "hybrid" else LexicalOnly()
    indices = {}
    try:
        for company in company_ids:
            indices[company] = SessionIndex(
                [
                    Passage(
                        id=row["id"],
                        source_id=row["id"],
                        title=row["title"],
                        text=row["text"],
                        url=row.get("url", "https://example.invalid/illustrative"),
                        locator=row.get("locator", "Frozen benchmark passage"),
                        evidence_level=row.get("evidence_level", "page_excerpt"),
                        category=row.get("category", "research"),
                        publisher=row.get("publisher", "Benchmark fixture"),
                        published_at=row.get("published_at"),
                        retrieved_at=timestamp,
                    )
                    for row in passages
                    if row["company"] == company
                ],
                embedder,
            )
        actual_modes = {index.summary()["mode"] for index in indices.values()}
        if mode == "hybrid" and actual_modes != {"hybrid_semantic_bm25"}:
            raise RuntimeError("Embedding model unavailable; install it or run --mode bm25.")
        rows = []
        for question in questions:
            hits = [hit["id"] for hit in indices[question["company"]].search(question["question"], top_k=5)]
            relevant = set(question["relevant"])
            ranks = [rank for rank, hit in enumerate(hits, 1) if hit in relevant]
            found = relevant.intersection(hits)
            rows.append(
                {
                    "id": question["id"],
                    "market": question["market"],
                    "expected": question["relevant"],
                    "retrieved": hits,
                    "first_relevant_rank": min(ranks) if ranks else None,
                    "relevant_recall_at_5": len(found) / len(relevant),
                    "missing_relevant": sorted(relevant - found),
                }
            )
        scored = [
            row for row in rows if question_by_id[row["id"]].get("answerability") != "unanswerable_from_fixture"
        ]
        total = len(scored)
        return {
            "dataset_kind": dataset["kind"],
            "mode": next(iter(actual_modes)),
            "questions": total,
            "unanswerable_context_cases": len(rows) - total,
            "hit_at_1": sum(row["first_relevant_rank"] == 1 for row in scored) / total,
            "hit_at_3": sum(
                row["first_relevant_rank"] is not None and row["first_relevant_rank"] <= 3
                for row in scored
            )
            / total,
            "hit_at_5": sum(row["first_relevant_rank"] is not None for row in scored) / total,
            "all_relevant_at_5": sum(not row["missing_relevant"] for row in scored) / total,
            "mean_relevant_recall_at_5": sum(row["relevant_recall_at_5"] for row in scored) / total,
            "mrr_at_5": sum(1 / row["first_relevant_rank"] for row in scored if row["first_relevant_rank"]) / total,
            "cases": rows,
        }
    finally:
        for index in indices.values():
            index.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evaluation/illustrative_retrieval.json",
    )
    parser.add_argument("--mode", choices=("hybrid", "bm25"), default="hybrid")
    parser.add_argument("--min-all-relevant-at-5", type=float, default=0.0)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = evaluate(dataset, mode=args.mode)
    print(json.dumps(result, indent=2))
    if result["all_relevant_at_5"] < args.min_all_relevant_at_5:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
