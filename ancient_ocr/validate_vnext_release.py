#!/usr/bin/env python
"""Validate the non-negotiable evidence for the versioned ancient vNext release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_MANIFEST_ROWS = 113
EXPECTED_ADOPTED_ROWS = 105
EXPECTED_FALLBACK_ROWS = 8
EXPECTED_PAGE_ROWS = 5624
KEYWORD_RECALL_AT_10_FLOOR = 0.8913
REQUIRED_MODES = ("keyword", "qwen-vector", "qwen-reranked-hybrid")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def as_float(value: Any, label: str, issues: list[str]) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        issues.append(f"{label} is missing or not numeric")
        return None


def validate_promotion(report: dict[str, Any], issues: list[str]) -> None:
    for key, expected in (
        ("manifest_rows", EXPECTED_MANIFEST_ROWS),
        ("promoted_rows", EXPECTED_MANIFEST_ROWS),
        ("page_count", EXPECTED_PAGE_ROWS),
        ("fts_count", EXPECTED_PAGE_ROWS),
        ("output_pages_jsonl_rows", EXPECTED_PAGE_ROWS),
    ):
        if report.get(key) != expected:
            issues.append(f"promotion {key} expected {expected}, found {report.get(key)!r}")
    modes = report.get("by_promotion_mode")
    if not isinstance(modes, dict):
        issues.append("promotion by_promotion_mode is missing")
    else:
        if modes.get("candidate_adopted") != EXPECTED_ADOPTED_ROWS:
            issues.append("promotion candidate_adopted count is not 105")
        if modes.get("original_fallback_empty_candidate") != EXPECTED_FALLBACK_ROWS:
            issues.append("promotion empty-candidate fallback count is not 8")
    if report.get("sqlite_quick_check") != "ok":
        issues.append("promotion SQLite quick_check is not ok")
    if report.get("source_database_modified") is not False:
        issues.append("promotion does not prove source_database_modified=false")
    before = report.get("source_database_sha256_before")
    after = report.get("source_database_sha256_after")
    if not isinstance(before, str) or not before or before != after:
        issues.append("promotion source database SHA-256 changed or is missing")
    if not isinstance(report.get("output_database_sha256"), str):
        issues.append("promotion output database SHA-256 is missing")
    if not isinstance(report.get("output_pages_jsonl_sha256"), str):
        issues.append("promotion output pages JSONL SHA-256 is missing")


def validate_doctor(report: dict[str, Any], issues: list[str]) -> None:
    if report.get("healthy") is not True:
        issues.append("doctor top-level healthy is not true")
    corpus = report.get("ancient_corpus")
    if not isinstance(corpus, dict) or corpus.get("healthy") is not True:
        issues.append("doctor ancient_corpus is not healthy")
    else:
        counts = corpus.get("counts")
        if not isinstance(counts, dict):
            issues.append("doctor ancient_corpus counts are missing")
        else:
            for key in ("pages", "fts_rows"):
                if counts.get(key) != EXPECTED_PAGE_ROWS:
                    issues.append(
                        f"doctor ancient_corpus {key} expected {EXPECTED_PAGE_ROWS}, "
                        f"found {counts.get(key)!r}"
                    )
        if corpus.get("sqlite_quick_check") != "ok":
            issues.append("doctor ancient_corpus SQLite quick_check is not ok")
    vector = report.get("ancient_qwen_vector")
    if not isinstance(vector, dict) or vector.get("healthy") is not True:
        issues.append("doctor ancient_qwen_vector is not healthy")
        return
    for key in (
        "pages_sha256_matches",
        "corpus_text_sha256_matches",
        "database_sha256_matches",
        "layout_sidecar_sha256_matches",
    ):
        if vector.get(key) is not True:
            issues.append(f"doctor ancient_qwen_vector {key} is not true")


def validate_evaluation(report: dict[str, Any], issues: list[str]) -> None:
    if report.get("question_count") != 52:
        issues.append("evaluation question_count is not 52")
    if report.get("positive_questions") != 46:
        issues.append("evaluation positive_questions is not 46")
    if report.get("no_answer_questions") != 6:
        issues.append("evaluation no_answer_questions is not 6")
    modes = report.get("modes")
    if not isinstance(modes, dict):
        issues.append("evaluation modes are missing")
        return
    for mode in REQUIRED_MODES:
        data = modes.get(mode)
        if not isinstance(data, dict):
            issues.append(f"evaluation {mode} result is missing")
            continue
        located = as_float(data.get("page_locatable_rate"), f"evaluation {mode} page_locatable_rate", issues)
        if located is not None and located != 1.0:
            issues.append(f"evaluation {mode} page_locatable_rate is not 1.0")
    keyword = modes.get("keyword")
    if isinstance(keyword, dict):
        recall = as_float(keyword.get("recall_at_10"), "evaluation keyword recall_at_10", issues)
        if recall is not None and recall < KEYWORD_RECALL_AT_10_FLOOR:
            issues.append(
                "evaluation keyword recall_at_10 is below "
                f"{KEYWORD_RECALL_AT_10_FLOOR}"
            )


def validate_preflight(report: dict[str, Any], issues: list[str]) -> None:
    if report.get("valid") is not True:
        issues.append("release preflight valid is not true")
    violations = report.get("violations")
    if not isinstance(violations, list):
        issues.append("release preflight violations list is missing")
    elif violations:
        issues.append("release preflight contains tracked private/generated files")


def validate_release(
    promotion: dict[str, Any],
    doctor: dict[str, Any],
    evaluation: dict[str, Any],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    issues: list[str] = []
    validate_promotion(promotion, issues)
    validate_doctor(doctor, issues)
    validate_evaluation(evaluation, issues)
    validate_preflight(preflight, issues)
    return {
        "valid": not issues,
        "issues": issues,
        "requirements": {
            "manifest_rows": EXPECTED_MANIFEST_ROWS,
            "candidate_adopted": EXPECTED_ADOPTED_ROWS,
            "empty_candidate_fallback": EXPECTED_FALLBACK_ROWS,
            "page_rows": EXPECTED_PAGE_ROWS,
            "keyword_recall_at_10_floor": KEYWORD_RECALL_AT_10_FLOOR,
            "page_locatable_rate": 1.0,
            "evaluation_modes": list(REQUIRED_MODES),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate vNext release evidence")
    parser.add_argument("--promotion-report", type=Path, required=True)
    parser.add_argument("--doctor-report", type=Path, required=True)
    parser.add_argument("--evaluation-report", type=Path, required=True)
    parser.add_argument("--preflight-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate_release(
        read_json(args.promotion_report),
        read_json(args.doctor_report),
        read_json(args.evaluation_report),
        read_json(args.preflight_report),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
