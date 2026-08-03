from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_graph.store import write_json


MODES = ("keyword", "qwen-vector", "qwen-reranked-hybrid")


def _modes(report: dict[str, Any]) -> dict[str, Any]:
    return dict(report.get("modes", report))


def validate_final_six_reports(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    issues: list[str] = []
    build = reports["corpus_build"]
    counts = dict(build.get("output_counts", {}))
    if counts != {"books": 22, "pages": 26949, "fts_rows": 26949}:
        issues.append("corpus_counts_invalid")
    if build.get("base_database_modified") is not False:
        issues.append("base_database_modified")
    if build.get("sqlite_quick_check") != "ok":
        issues.append("corpus_quick_check_failed")

    doctor = reports["corpus_doctor"]
    if doctor.get("healthy") is not True or doctor.get("issues"):
        issues.append("corpus_doctor_failed")
    if doctor.get("pages_jsonl_rows") != 26949:
        issues.append("pages_jsonl_count_invalid")
    if any(not row.get("healthy") for row in doctor.get("source_checks", [])):
        issues.append("source_snapshot_check_failed")

    ancient = reports["ancient_kg"]
    if ancient.get("valid") is not True:
        issues.append("ancient_kg_failed")
    approved_section = dict(ancient.get("approved", {}))
    approved = dict(
        approved_section.get("counts", ancient.get("approved_counts", approved_section))
    )
    if int(approved.get("evidence", 0)) < 1700:
        issues.append("ancient_approved_evidence_too_small")
    if ancient.get("release_doctor_valid") is not True:
        issues.append("ancient_release_doctor_failed")

    formula = reports["formula"]
    if formula.get("valid") is not True or formula.get("variant_count") != 2:
        issues.append("formula_disambiguation_failed")
    if formula.get("unique_composition_fingerprints") != 2:
        issues.append("formula_composition_collision")

    extended_modes = _modes(reports["extended_evaluation"])
    for mode in MODES:
        metrics = dict(extended_modes.get(mode, {}))
        if float(metrics.get("recall_at_10", 0.0)) < 0.99:
            issues.append(f"extended_recall_failed:{mode}")
        if float(metrics.get("page_locatable_rate", 0.0)) != 1.0:
            issues.append(f"extended_page_location_failed:{mode}")
        if float(metrics.get("no_answer_accuracy", 0.0)) != 1.0:
            issues.append(f"extended_abstention_failed:{mode}")

    legacy_modes = _modes(reports["legacy_evaluation"])
    if float(legacy_modes.get("keyword", {}).get("recall_at_10", 0.0)) < 0.8913:
        issues.append("legacy_keyword_recall_regressed")
    if float(
        legacy_modes.get("qwen-reranked-hybrid", {}).get("recall_at_10", 0.0)
    ) < 0.8913:
        issues.append("legacy_hybrid_recall_regressed")
    for mode in MODES:
        if float(legacy_modes.get(mode, {}).get("page_locatable_rate", 0.0)) != 1.0:
            issues.append(f"legacy_page_location_failed:{mode}")

    structured = reports["modern_structured"]
    structured_count = int(
        structured.get("approved_structured_evidence", structured.get("approved_count", 0))
    )
    if structured_count < 600:
        issues.append("modern_structured_evidence_too_small")
    if structured.get("source_database_unchanged") is not True:
        issues.append("modern_database_modified")

    modern = reports["modern_kg"]
    if modern.get("valid") is not True or modern.get("release_validation_valid") is not True:
        issues.append("modern_kg_failed")
    if int(modern.get("chain_counts", {}).get("compound_target_pathway_phenotype", 0)) < 1:
        issues.append("modern_mechanism_chain_missing")
    if modern.get("source_database_unchanged") is not True:
        issues.append("modern_kg_database_modified")

    combined = reports["combined"]
    if combined.get("valid") is not True or combined.get("release_doctor_valid") is not True:
        issues.append("combined_release_failed")
    if combined.get("automatic_treats_edges") != 0:
        issues.append("automatic_treats_edge_present")

    preflight = reports["preflight"]
    if preflight.get("valid") is not True or preflight.get("violations"):
        issues.append("release_preflight_failed")

    return {
        "valid": not issues,
        "acceptance_version": "final-six-auto70-2026-08-03-v1",
        "issues": issues,
        "summary": {
            "ancient_books": counts.get("books"),
            "ancient_pages": counts.get("pages"),
            "ancient_approved_evidence": approved.get("evidence"),
            "formula_variants": formula.get("variant_count"),
            "extended_question_count": reports["extended_evaluation"].get(
                "question_count"
            ),
            "modern_structured_evidence": structured_count,
            "modern_mechanism_chains": modern.get("chain_counts", {}).get(
                "compound_target_pathway_phenotype"
            ),
            "combined_counts": combined.get("counts", {}),
            "combined_content_fingerprint": combined.get("content_fingerprint", ""),
        },
    }


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate all six final deliverables")
    for name in (
        "corpus_build",
        "corpus_doctor",
        "ancient_kg",
        "formula",
        "extended_evaluation",
        "legacy_evaluation",
        "modern_structured",
        "modern_kg",
        "combined",
        "preflight",
    ):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = {
        name: _read(getattr(args, name))
        for name in (
            "corpus_build",
            "corpus_doctor",
            "ancient_kg",
            "formula",
            "extended_evaluation",
            "legacy_evaluation",
            "modern_structured",
            "modern_kg",
            "combined",
            "preflight",
        )
    }
    report = validate_final_six_reports(reports)
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
