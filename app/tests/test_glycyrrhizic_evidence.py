from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from discovery_pipeline.automatic_modern_kg import build_automatic_modern_bundle
from discovery_pipeline.structured_evidence import _candidate_context, _extract_fields


GLYCYRRHIZIC_TEXT = (
    "In an animal wound healing study, glycyrrhizin-based topical hydrogel accelerated "
    "wound closure. Glycyrrhizic acid inhibits HMGB1 by direct binding. Excessive oral "
    "consumption can lead to hypertension and hypokalemia."
)


def test_glycyrrhizic_context_extracts_specific_mechanism_and_risk() -> None:
    text = "NFE2L2 " + ("unrelated background " * 120) + GLYCYRRHIZIC_TEXT
    fields = _extract_fields(
        text,
        candidate_id="compound:glycyrrhizic_acid",
        matched_terms=["glycyrrhizin", "glycyrrhizic acid"],
    )

    assert fields["compound_identity"]["explicit_parent_mention"] is True
    assert "HMGB1" in fields["targets"]
    assert "NFE2L2" not in fields["targets"]
    assert fields["direct_target_relations"] == [
        {
            "target": "HMGB1",
            "predicate": "BINDS_TO",
            "evidence_scope": "source_reported_direct_binding",
        },
        {
            "target": "HMGB1",
            "predicate": "INHIBITS",
            "evidence_scope": "source_reported_functional_inhibition",
        },
    ]
    assert fields["formulations"] == ["hydrogel"]
    assert fields["safety_signals"] == ["hypertension", "hypokalemia"]
    assert set(fields["routes"]) == {"oral", "topical"}


def test_short_uppercase_candidate_alias_is_case_sensitive_and_word_bounded() -> None:
    text = "ordinary aggregate material. GA directly binds the target."
    scoped, spans = _candidate_context(text, ["GA"], radius=5)

    assert scoped == "ial. GA dire"
    assert spans == [{"start": 24, "end": 36}]


def test_glycyrrhizic_bundle_preserves_direct_edges_and_source_locator(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rag.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            title TEXT,
            year TEXT,
            doi TEXT,
            source_filename TEXT,
            sha256 TEXT
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT,
            pdf_page INTEGER,
            text TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
        (
            "doi:10.0000/glycyrrhizic-test",
            "Glycyrrhizic acid hydrogel wound study",
            "2026",
            "10.0000/glycyrrhizic-test",
            "glycyrrhizic-test.pdf",
            "a" * 64,
        ),
    )
    connection.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?)",
        (
            "chunk:glycyrrhizic:p2",
            "doi:10.0000/glycyrrhizic-test",
            2,
            GLYCYRRHIZIC_TEXT,
        ),
    )
    connection.commit()
    connection.close()

    fields = _extract_fields(
        GLYCYRRHIZIC_TEXT,
        candidate_id="compound:glycyrrhizic_acid",
        matched_terms=["glycyrrhizin", "glycyrrhizic acid"],
    )
    structured = tmp_path / "approved.jsonl"
    structured.write_text(
        json.dumps(
            {
                "locus_id": "locus:glycyrrhizic:p2",
                "candidate_id": "compound:glycyrrhizic_acid",
                "chunk_id": "chunk:glycyrrhizic:p2",
                "chunk_text_sha256": hashlib.sha256(
                    GLYCYRRHIZIC_TEXT.encode("utf-8")
                ).hexdigest(),
                "review_status": "approved",
                "semantic_confidence": 0.91,
                "structured_fields": fields,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "compound:glycyrrhizic_acid",
                        "canonical_name": "glycyrrhizic acid",
                        "name_zh": "甘草酸",
                        "aliases": ["glycyrrhizin"],
                        "herb_ids": ["herb:glycyrrhiza_root"],
                        "candidate_role": "parent_compound",
                        "expected_pubchem_cid": 14982,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bundle, report = build_automatic_modern_bundle(
        structured_evidence_path=structured,
        catalog_path=catalog,
        database_path=database,
        graph_version="glycyrrhizic-test-v1",
    )

    predicates = {value["predicate"] for value in bundle["assertions"]}
    assert {"BINDS_TO", "INHIBITS", "FORMULATED_AS", "HAS_SAFETY_SIGNAL"} <= predicates
    assert "TARGETS" not in predicates
    assert report["compound_summary"]["compound:glycyrrhizic_acid"] == {
        "approved_evidence_records": 1,
        "source_documents": 1,
        "targets": ["HMGB1"],
        "pathways": [],
        "outcomes": ["wound_closure", "wound_healing"],
        "safety_signals": ["hypertension", "hypokalemia"],
        "formulations": ["hydrogel"],
        "direct_target_relations": {"BINDS_TO": 1, "INHIBITS": 1},
    }
    evidence = bundle["evidence"][0]
    assert evidence["locator"]["pdf_page"] == 2
    assert evidence["quote"] == GLYCYRRHIZIC_TEXT
