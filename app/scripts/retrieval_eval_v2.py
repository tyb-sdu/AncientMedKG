#!/usr/bin/env python
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_prep.config import load_config
from rag_prep.search import query_retrieval, source_page


MODES = (
    "keyword",
    "vector",
    "hybrid",
    "bge-vector",
    "reranked-hybrid",
    "qwen-vector",
    "qwen-reranked-hybrid",
)


def _is_relevant(result: dict[str, Any], item: dict[str, Any]) -> bool:
    for locus in item["expected_loci"]:
        if (
            result["doc_id"] == locus["doc_id"]
            and int(result["pdf_page"]) in {int(page) for page in locus["pdf_pages"]}
        ):
            return True
    return False


def evaluate_mode(
    cfg: dict[str, Any], questions: list[dict[str, Any]], mode: str
) -> dict[str, Any]:
    started = time.time()
    positive = [item for item in questions if item["expect_answer"]]
    no_answer = [item for item in questions if not item["expect_answer"]]
    hit5 = hit10 = 0
    reciprocal_rank = 0.0
    document_reciprocal_rank = 0.0
    doc_hit5 = doc_hit10 = 0
    located = returned = 0
    no_answer_correct = 0
    records = []

    for item in questions:
        results = query_retrieval(cfg, item["question"], mode, top_k=10)
        for result in results:
            returned += 1
            located += int(
                source_page(cfg, result["doc_id"], int(result["pdf_page"]))
                is not None
            )
        relevant_ranks = [
            rank
            for rank, result in enumerate(results, 1)
            if _is_relevant(result, item)
        ]
        expected_doc_ids = {
            locus["doc_id"] for locus in item.get("expected_loci", [])
        }
        doc_ranks = [
            rank
            for rank, result in enumerate(results, 1)
            if result["doc_id"] in expected_doc_ids
        ]
        if item["expect_answer"]:
            best = min(relevant_ranks) if relevant_ranks else None
            best_doc = min(doc_ranks) if doc_ranks else None
            hit5 += int(best is not None and best <= 5)
            hit10 += int(best is not None and best <= 10)
            doc_hit5 += int(best_doc is not None and best_doc <= 5)
            doc_hit10 += int(best_doc is not None and best_doc <= 10)
            reciprocal_rank += (1.0 / best) if best else 0.0
            document_reciprocal_rank += (1.0 / best_doc) if best_doc else 0.0
            passed = best is not None
        else:
            best = None
            no_answer_correct += int(not results)
            passed = not results
        records.append(
            {
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "expect_answer": item["expect_answer"],
                "expected_loci": item["expected_loci"],
                "best_exact_locus_rank": best,
                "best_document_rank": best_doc if item["expect_answer"] else None,
                "result_count": len(results),
                "document_passed_at_10": (
                    best_doc is not None if item["expect_answer"] else passed
                ),
                "strict_locus_passed_at_10": passed,
                "top_results": [
                    {
                        "rank": rank,
                        "doc_id": result["doc_id"],
                        "pdf_page": result["pdf_page"],
                        "title": result["title"],
                        "chunk_id": result["chunk_id"],
                        "keyword_score": result.get("keyword_score"),
                        "vector_score": result.get("vector_score"),
                        "fusion_score": result.get("fusion_score"),
                        "reranker_score": result.get("reranker_score"),
                    }
                    for rank, result in enumerate(results[:3], 1)
                ],
            }
        )

    category_total = Counter(item["category"] for item in positive)
    category_hit = Counter(
        record["category"]
        for record in records
        if record["expect_answer"] and record["best_exact_locus_rank"] is not None
    )
    count = len(positive)
    return {
        "mode": mode,
        "question_count": len(questions),
        "positive_questions": count,
        "no_answer_questions": len(no_answer),
        "recall_at_5": doc_hit5 / count if count else 0.0,
        "recall_at_10": doc_hit10 / count if count else 0.0,
        "mrr_at_10": document_reciprocal_rank / count if count else 0.0,
        "strict_locus_recall_at_5": hit5 / count if count else 0.0,
        "strict_locus_recall_at_10": hit10 / count if count else 0.0,
        "strict_locus_mrr_at_10": reciprocal_rank / count if count else 0.0,
        "page_locatable_rate": located / returned if returned else 1.0,
        "returned_results_checked": returned,
        "no_answer_accuracy": (
            no_answer_correct / len(no_answer) if no_answer else 0.0
        ),
        "elapsed_seconds": round(time.time() - started, 3),
        "category_exact_locus_recall_at_10": {
            category: category_hit[category] / category_total[category]
            for category in sorted(category_total)
        },
        "failed_cases": [
            {
                "id": record["id"],
                "category": record["category"],
                "question": record["question"],
                "expected_loci": record["expected_loci"],
                "top_results": record["top_results"],
            }
            for record in records
            if record["expect_answer"] and not record["document_passed_at_10"]
        ],
        "strict_locus_failed_cases": [
            {
                "id": record["id"],
                "category": record["category"],
                "question": record["question"],
                "expected_loci": record["expected_loci"],
                "top_results": record["top_results"],
            }
            for record in records
            if record["expect_answer"]
            and not record["strict_locus_passed_at_10"]
        ],
        "records": records,
    }


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    questions = json.loads(
        (ROOT / "data" / "retrieval_questions_v2.json").read_text(
            encoding="utf-8"
        )
    )
    reports = {mode: evaluate_mode(cfg, questions, mode) for mode in MODES}
    report = {
        "evaluation_version": 2,
        "ground_truth": {
            "independent_of_retrieval_outputs": True,
            "unit": "fixed doc_id plus physical PDF page",
            "positive_labels": "curated document plus original-page evidence terms",
            "no_answer_labels": "synthetic out-of-corpus identifiers",
        },
        "question_count": len(questions),
        "positive_questions": sum(item["expect_answer"] for item in questions),
        "no_answer_questions": sum(not item["expect_answer"] for item in questions),
        "modes": reports,
    }
    out = ROOT / "data" / "retrieval_eval_v2.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        mode: {
            key: value
            for key, value in result.items()
            if key
            in {
                "recall_at_5",
                "recall_at_10",
                "document_recall_at_5",
                "document_recall_at_10",
                "mrr_at_10",
                "page_locatable_rate",
                "no_answer_accuracy",
                "elapsed_seconds",
            }
        }
        for mode, result in reports.items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
