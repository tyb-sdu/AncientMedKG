from validate_vnext_release import validate_release


def passing_reports():
    promotion = {
        "manifest_rows": 113,
        "promoted_rows": 113,
        "page_count": 5624,
        "fts_count": 5624,
        "output_pages_jsonl_rows": 5624,
        "by_promotion_mode": {
            "candidate_adopted": 105,
            "original_fallback_empty_candidate": 8,
        },
        "sqlite_quick_check": "ok",
        "source_database_modified": False,
        "source_database_sha256_before": "a" * 64,
        "source_database_sha256_after": "a" * 64,
        "output_database_sha256": "b" * 64,
        "output_pages_jsonl_sha256": "c" * 64,
    }
    doctor = {
        "healthy": True,
        "ancient_corpus": {
            "healthy": True,
            "counts": {"pages": 5624, "fts_rows": 5624},
            "sqlite_quick_check": "ok",
        },
        "ancient_qwen_vector": {
            "healthy": True,
            "pages_sha256_matches": True,
            "corpus_text_sha256_matches": True,
            "database_sha256_matches": True,
            "layout_sidecar_sha256_matches": True,
        },
    }
    modes = {
        mode: {"page_locatable_rate": 1.0}
        for mode in ("keyword", "qwen-vector", "qwen-reranked-hybrid")
    }
    modes["keyword"]["recall_at_10"] = 0.8913
    evaluation = {
        "question_count": 52,
        "positive_questions": 46,
        "no_answer_questions": 6,
        "modes": modes,
    }
    preflight = {"valid": True, "violations": []}
    return promotion, doctor, evaluation, preflight


def test_accepts_complete_release_evidence() -> None:
    assert validate_release(*passing_reports())["valid"] is True


def test_rejects_regressed_keyword_recall_and_missing_vector_proof() -> None:
    promotion, doctor, evaluation, preflight = passing_reports()
    evaluation["modes"]["keyword"]["recall_at_10"] = 0.8696
    doctor["ancient_qwen_vector"]["database_sha256_matches"] = False

    result = validate_release(promotion, doctor, evaluation, preflight)

    assert result["valid"] is False
    assert any("recall_at_10" in issue for issue in result["issues"])
    assert any("database_sha256_matches" in issue for issue in result["issues"])
