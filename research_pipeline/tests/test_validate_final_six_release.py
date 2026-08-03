from __future__ import annotations

from research_pipeline.validate_final_six_release import validate_final_six_reports


def test_final_six_gate_accepts_complete_release() -> None:
    metric = {
        "recall_at_10": 1.0,
        "page_locatable_rate": 1.0,
        "no_answer_accuracy": 1.0,
    }
    reports = {
        "corpus_build": {
            "output_counts": {"books": 22, "pages": 26949, "fts_rows": 26949},
            "base_database_modified": False,
            "sqlite_quick_check": "ok",
        },
        "corpus_doctor": {
            "healthy": True,
            "issues": [],
            "pages_jsonl_rows": 26949,
            "source_checks": [{"healthy": True}],
        },
        "ancient_kg": {
            "valid": True,
            "approved": {"counts": {"evidence": 1744}},
            "release_doctor_valid": True,
        },
        "formula": {
            "valid": True,
            "variant_count": 2,
            "unique_composition_fingerprints": 2,
        },
        "extended_evaluation": {
            "question_count": 240,
            "modes": {mode: metric for mode in ("keyword", "qwen-vector", "qwen-reranked-hybrid")},
        },
        "legacy_evaluation": {
            "modes": {mode: metric for mode in ("keyword", "qwen-vector", "qwen-reranked-hybrid")},
        },
        "modern_structured": {
            "approved_count": 606,
            "source_database_unchanged": True,
        },
        "modern_kg": {
            "valid": True,
            "release_validation_valid": True,
            "chain_counts": {"compound_target_pathway_phenotype": 97},
            "source_database_unchanged": True,
        },
        "combined": {
            "valid": True,
            "release_doctor_valid": True,
            "automatic_treats_edges": 0,
            "counts": {"evidence": 2350},
        },
        "preflight": {"valid": True, "violations": []},
    }
    report = validate_final_six_reports(reports)
    assert report["valid"] is True
    assert report["issues"] == []
