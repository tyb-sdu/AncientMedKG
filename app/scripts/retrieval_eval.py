#!/usr/bin/env python
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_prep.config import load_config
from rag_prep.search import normalize_search_text, query_index, source_page


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    questions = json.loads(
        (ROOT / "data" / "retrieval_questions.json").read_text(encoding="utf-8")
    )
    records = []
    positive_total = positive_hits = 0
    no_result_total = no_result_correct = 0
    located = returned = 0

    for item in questions:
        results = query_index(cfg, item["question"], top_k=10)
        expected = [normalize_search_text(x) for x in item["expected_terms"]]
        matched = False
        matched_rank = None
        for rank, result in enumerate(results, 1):
            returned += 1
            if source_page(cfg, result["doc_id"], int(result["pdf_page"])):
                located += 1
            hay = normalize_search_text(
                f"{result['title']} {result['snippet']}"
            )
            if expected and any(term in hay for term in expected):
                matched = True
                matched_rank = matched_rank or rank

        if item["expect_results"]:
            positive_total += 1
            positive_hits += int(matched)
            passed = matched
        else:
            no_result_total += 1
            no_result_correct += int(not results)
            passed = not results
        records.append(
            {
                **item,
                "passed": passed,
                "matched_rank": matched_rank,
                "result_count": len(results),
                "top_results": results[:3],
            }
        )

    category = Counter()
    category_pass = Counter()
    for record in records:
        category[record["category"]] += 1
        category_pass[record["category"]] += int(record["passed"])
    report = {
        "question_count": len(records),
        "positive_questions": positive_total,
        "recall_at_10": positive_hits / positive_total if positive_total else 0.0,
        "positive_hits": positive_hits,
        "no_result_questions": no_result_total,
        "no_result_accuracy": (
            no_result_correct / no_result_total if no_result_total else 0.0
        ),
        "page_locatable_rate": located / returned if returned else 1.0,
        "returned_results_checked": returned,
        "category_results": {
            key: {"passed": category_pass[key], "total": category[key]}
            for key in sorted(category)
        },
        "failed_questions": [
            {"id": r["id"], "question": r["question"], "category": r["category"]}
            for r in records
            if not r["passed"]
        ],
        "records": records,
    }
    out = Path(cfg["paths"]["retrieval_eval"])
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
