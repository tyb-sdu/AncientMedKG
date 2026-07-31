from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .query_planner import query_specialized


ROOT = Path(__file__).resolve().parents[1]
MODES = ("keyword", "qwen-vector", "qwen-reranked-hybrid")


def _load_runtime() -> tuple[Any, Any, Any]:
    sys.path.insert(0, str(ROOT / "app" / "src"))
    from rag_prep.config import load_config
    from rag_prep.dual_retrieval import query_any_corpus, source_any_page

    return load_config, query_any_corpus, source_any_page


def _baseline_query(
    query_any_corpus: Any,
) -> Any:
    def run(
        cfg: dict[str, Any], question: str, retrieval: str, top_k: int
    ) -> list[dict[str, Any]]:
        return query_any_corpus(
            cfg, question, retrieval, top_k=top_k, mode="ancient"
        )

    return run


def _is_relevant(result: dict[str, Any], item: dict[str, Any]) -> bool:
    return any(
        result.get("doc_id") == locus["doc_id"]
        and int(result.get("pdf_page") or 0)
        in {int(page) for page in locus["pdf_pages"]}
        for locus in item.get("expected_loci", [])
    )


def _validate_ground_truth(
    cfg: dict[str, Any], questions: list[dict[str, Any]], source_any_page: Any
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in questions:
        for locus in item.get("expected_loci", []):
            for page in locus["pdf_pages"]:
                source = source_any_page(
                    cfg, locus["doc_id"], int(page), mode="ancient"
                )
                if not source:
                    issues.append(
                        {"id": item["id"], "page": page, "reason": "source_page_missing"}
                    )
                    continue
                if not any(
                    term in str(source.get("text") or "")
                    for term in locus["evidence_terms"]
                ):
                    issues.append(
                        {
                            "id": item["id"],
                            "page": page,
                            "reason": "evidence_terms_not_found",
                        }
                    )
    return issues


def evaluate_mode(
    cfg: dict[str, Any],
    questions: list[dict[str, Any]],
    retrieval: str,
    query_any_corpus: Any,
    source_any_page: Any,
) -> dict[str, Any]:
    started = time.time()
    positives = [item for item in questions if item["expect_answer"]]
    negatives = [item for item in questions if not item["expect_answer"]]
    hit5 = hit10 = located = returned = no_answer_correct = 0
    reciprocal_rank = 0.0
    records: list[dict[str, Any]] = []
    baseline = _baseline_query(query_any_corpus)

    for item in questions:
        results, plan = query_specialized(
            cfg,
            item["question"],
            retrieval,
            top_k=10,
            baseline_query=baseline,
        )
        for result in results:
            returned += 1
            located += int(
                source_any_page(
                    cfg,
                    result["doc_id"],
                    int(result["pdf_page"]),
                    mode="ancient",
                )
                is not None
            )
        relevant_ranks = [
            rank
            for rank, result in enumerate(results, start=1)
            if _is_relevant(result, item)
        ]
        best_rank = min(relevant_ranks) if relevant_ranks else None
        if item["expect_answer"]:
            hit5 += int(best_rank is not None and best_rank <= 5)
            hit10 += int(best_rank is not None and best_rank <= 10)
            reciprocal_rank += 1.0 / best_rank if best_rank else 0.0
            passed = best_rank is not None
        else:
            no_answer_correct += int(not results and plan.abstain)
            passed = not results and plan.abstain
        records.append(
            {
                "id": item["id"],
                "category": item["category"],
                "expect_answer": item["expect_answer"],
                "best_exact_locus_rank": best_rank,
                "passed_at_10": passed,
                "result_count": len(results),
                "plan": plan.to_dict(),
                "top_results": [
                    {
                        "rank": rank,
                        "doc_id": result["doc_id"],
                        "pdf_page": result["pdf_page"],
                        "planned_score": result.get("planned_score"),
                        "research_score": result.get("research_score"),
                        "baseline_rank": result.get("baseline_rank"),
                        "research_hits": result.get("research_hits", []),
                    }
                    for rank, result in enumerate(results[:5], start=1)
                ],
            }
        )

    category_total = Counter(item["category"] for item in positives)
    category_hit = Counter(
        record["category"]
        for record in records
        if record["expect_answer"] and record["best_exact_locus_rank"] is not None
    )
    return {
        "retrieval_mode": retrieval,
        "planner": "controlled-vocabulary-v1",
        "question_count": len(questions),
        "positive_questions": len(positives),
        "no_answer_questions": len(negatives),
        "recall_at_5": hit5 / len(positives) if positives else 0.0,
        "recall_at_10": hit10 / len(positives) if positives else 0.0,
        "mrr_at_10": reciprocal_rank / len(positives) if positives else 0.0,
        "page_locatable_rate": located / returned if returned else 1.0,
        "no_answer_accuracy": (
            no_answer_correct / len(negatives) if negatives else 0.0
        ),
        "category_recall_at_10": {
            category: category_hit[category] / category_total[category]
            for category in sorted(category_total)
        },
        "elapsed_seconds": round(time.time() - started, 3),
        "failed_cases": [record for record in records if not record["passed_at_10"]],
        "records": records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the evidence-aware Rendongtang research layer"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path(__file__).parent / "evaluation" / "rendongtang_questions_v1.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_config, query_any_corpus, source_any_page = _load_runtime()
    cfg = load_config(args.config)
    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    evidence_issues = _validate_ground_truth(cfg, questions, source_any_page)
    if evidence_issues:
        raise ValueError(
            "ground truth validation failed: "
            + json.dumps(evidence_issues, ensure_ascii=False)
        )
    reports = {
        mode: evaluate_mode(
            cfg, questions, mode, query_any_corpus, source_any_page
        )
        for mode in args.modes
    }
    report = {
        "evaluation_version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "research_pipeline.controlled-vocabulary-v1",
        "raw_baseline_must_be_reported_separately": True,
        "ground_truth_uses_page_labels_in_evaluation_only": True,
        "planner_uses_expected_loci": False,
        "modes": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        mode: {
            key: result[key]
            for key in (
                "recall_at_5",
                "recall_at_10",
                "mrr_at_10",
                "page_locatable_rate",
                "no_answer_accuracy",
                "elapsed_seconds",
            )
        }
        for mode, result in reports.items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
