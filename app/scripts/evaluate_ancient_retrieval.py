#!/usr/bin/env python
"""Evaluate page-level retrieval for the independent ancient-book corpus."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_prep.config import load_config
from rag_prep.dual_retrieval import query_any_corpus, source_any_page


MODES = ("keyword", "qwen-vector", "qwen-reranked-hybrid")


def load_questions(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("评测题集必须是 JSON 数组")
    return payload


def validate_question_schema(questions: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(questions, start=1):
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen_ids:
            errors.append(f"第 {index} 题 id 缺失或重复: {item_id!r}")
        seen_ids.add(item_id)
        if not str(item.get("question") or "").strip():
            errors.append(f"{item_id}: question 为空")
        if not str(item.get("category") or "").strip():
            errors.append(f"{item_id}: category 为空")
        expect_answer = item.get("expect_answer")
        loci = item.get("expected_loci", [])
        if expect_answer is True and not loci:
            errors.append(f"{item_id}: 有答案题缺少 expected_loci")
        if expect_answer is False and loci:
            errors.append(f"{item_id}: 无答案题不应声明 expected_loci")
        if expect_answer not in {True, False}:
            errors.append(f"{item_id}: expect_answer 必须为布尔值")
        for locus in loci:
            if not str(locus.get("doc_id") or "").startswith("ancient:"):
                errors.append(f"{item_id}: 标签必须使用 ancient: book_id")
            pages = locus.get("pdf_pages", [])
            if not pages or any(int(page) < 1 for page in pages):
                errors.append(f"{item_id}: pdf_pages 非法")
            terms = locus.get("evidence_terms", [])
            if not terms or not all(str(term).strip() for term in terms):
                errors.append(f"{item_id}: evidence_terms 不能为空")
    return errors


def validate_ground_truth(
    cfg: dict[str, Any], questions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in questions:
        for locus in item.get("expected_loci", []):
            for page in locus["pdf_pages"]:
                source = source_any_page(cfg, locus["doc_id"], int(page), mode="ancient")
                if not source:
                    issues.append(
                        {"id": item["id"], "page": page, "reason": "source_page_missing"}
                    )
                    continue
                text = str(source.get("text") or "")
                matched_terms = [
                    term for term in locus["evidence_terms"] if term in text
                ]
                if not matched_terms:
                    issues.append(
                        {
                            "id": item["id"],
                            "page": page,
                            "reason": "evidence_terms_not_found",
                            "evidence_terms": locus["evidence_terms"],
                        }
                    )
    return issues


def is_relevant(result: dict[str, Any], item: dict[str, Any]) -> bool:
    return any(
        result.get("doc_id") == locus["doc_id"]
        and int(result.get("pdf_page") or 0) in {int(page) for page in locus["pdf_pages"]}
        for locus in item.get("expected_loci", [])
    )


def evaluate_mode(
    cfg: dict[str, Any], questions: list[dict[str, Any]], mode: str
) -> dict[str, Any]:
    started = time.time()
    positives = [item for item in questions if item["expect_answer"]]
    negatives = [item for item in questions if not item["expect_answer"]]
    hit5 = hit10 = located = returned = no_answer_correct = 0
    reciprocal_rank = 0.0
    records: list[dict[str, Any]] = []

    for item in questions:
        results = query_any_corpus(
            cfg, item["question"], mode, top_k=10, mode="ancient"
        )
        for result in results:
            returned += 1
            located += int(
                source_any_page(
                    cfg, result["doc_id"], int(result["pdf_page"]), mode="ancient"
                )
                is not None
            )
        relevant_ranks = [
            rank for rank, result in enumerate(results, start=1) if is_relevant(result, item)
        ]
        best_rank = min(relevant_ranks) if relevant_ranks else None
        if item["expect_answer"]:
            hit5 += int(best_rank is not None and best_rank <= 5)
            hit10 += int(best_rank is not None and best_rank <= 10)
            reciprocal_rank += 1.0 / best_rank if best_rank else 0.0
            passed = best_rank is not None
        else:
            no_answer_correct += int(not results)
            passed = not results
        records.append(
            {
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "expect_answer": item["expect_answer"],
                "expected_loci": item.get("expected_loci", []),
                "best_exact_locus_rank": best_rank,
                "passed_at_10": passed,
                "result_count": len(results),
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
        "mode": mode,
        "question_count": len(questions),
        "positive_questions": len(positives),
        "no_answer_questions": len(negatives),
        "recall_at_5": hit5 / len(positives) if positives else 0.0,
        "recall_at_10": hit10 / len(positives) if positives else 0.0,
        "mrr_at_10": reciprocal_rank / len(positives) if positives else 0.0,
        "page_locatable_rate": located / returned if returned else 1.0,
        "returned_results_checked": returned,
        "no_answer_accuracy": no_answer_correct / len(negatives) if negatives else 0.0,
        "abstention_note": (
            "当前古籍三条检索通道均未配置拒答阈值；"
            "无答案题用于量化后续拒答模块，而非作为当前通道的通过条件。"
        ),
        "elapsed_seconds": round(time.time() - started, 3),
        "category_recall_at_10": {
            category: category_hit[category] / category_total[category]
            for category in sorted(category_total)
        },
        "failed_cases": [
            record
            for record in records
            if (record["expect_answer"] and not record["passed_at_10"])
            or (not record["expect_answer"] and not record["passed_at_10"])
        ],
        "records": records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ancient corpus retrieval acceptance")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument(
        "--questions", type=Path, default=ROOT / "evaluation" / "ancient_questions_v1.json"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT.parent / "ancient_ocr" / "data" / "ancient_retrieval_eval_v1.json",
    )
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--skip-ground-truth-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    questions = load_questions(args.questions)
    schema_errors = validate_question_schema(questions)
    if schema_errors:
        raise ValueError("题集结构无效: " + "; ".join(schema_errors))
    evidence_issues = [] if args.skip_ground_truth_check else validate_ground_truth(cfg, questions)
    if evidence_issues:
        raise ValueError("题集证据页校验失败: " + json.dumps(evidence_issues, ensure_ascii=False))
    reports = {mode: evaluate_mode(cfg, questions, mode) for mode in args.modes}
    report = {
        "evaluation_version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ground_truth": {
            "independent_of_retrieval_outputs": True,
            "unit": "fixed ancient book_id plus physical PDF page",
            "evidence_validation": "at least one curated evidence term must occur on each labeled page",
            "no_answer_labels": "out-of-corpus concepts without an evidence locus",
        },
        "question_count": len(questions),
        "positive_questions": sum(item["expect_answer"] for item in questions),
        "no_answer_questions": sum(not item["expect_answer"] for item in questions),
        "modes": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        mode: {
            key: value
            for key, value in result.items()
            if key
            in {
                "recall_at_5",
                "recall_at_10",
                "mrr_at_10",
                "page_locatable_rate",
                "no_answer_accuracy",
                "elapsed_seconds",
            }
        }
        for mode, result in reports.items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
