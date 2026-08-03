from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from discovery_pipeline.automatic_modern_kg import build_automatic_modern_bundle
from knowledge_graph.build import build_bundle
from knowledge_graph.validate import validate_graph


def test_modern_graph_builds_traceable_mechanism_chain(tmp_path: Path) -> None:
    database = tmp_path / "rag.db"
    text = (
        "Chlorogenic acid accelerated wound healing in rats through Nrf2/HO-1 "
        "signaling and reduced inflammation."
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY, title TEXT, year TEXT, doi TEXT,
                source_filename TEXT, sha256 TEXT
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY, doc_id TEXT, pdf_page INTEGER, text TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            ("doc:1", "Burn study", "2024", "10.1/test", "study.pdf", "a" * 64),
        )
        connection.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)", ("chunk:1", "doc:1", 4, text))
        connection.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)", ("chunk:2", "doc:1", 5, text))
    structured = tmp_path / "structured.jsonl"
    first_record = {
                "locus_id": "locus:1",
                "candidate_id": "compound:chlorogenic_acid",
                "chunk_id": "chunk:1",
                "doc_id": "doc:1",
                "pdf_page": 4,
                "source_sha256": "a" * 64,
                "chunk_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "semantic_confidence": 0.9,
                "review_status": "approved",
                "structured_fields": {
                    "study_type": "animal",
                    "outcomes": ["wound_healing", "inflammation"],
                    "targets": ["NFE2L2", "HMOX1"],
                    "pathways": ["pathway:nrf2_ho1"],
                    "direction": "beneficial",
                    "target_relation_signals": ["through"],
                    "safety_signals": [],
                },
            }
    second_record = {
        **first_record,
        "locus_id": "locus:2",
        "chunk_id": "chunk:2",
        "pdf_page": 5,
        "structured_fields": {
            **first_record["structured_fields"],
            "study_type": "in_vitro",
        },
    }
    structured.write_text(
        "\n".join(json.dumps(row) for row in (first_record, second_record)) + "\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "compound:chlorogenic_acid",
                        "canonical_name": "chlorogenic acid",
                        "name_zh": "绿原酸",
                        "aliases": [],
                        "herb_ids": ["herb:lonicera"],
                        "candidate_role": "parent_compound",
                        "expected_pubchem_cid": 1794427,
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
        graph_version="modern-test-v1",
    )
    graph = build_bundle(bundle)
    assert validate_graph(graph, release=True)["valid"] is True
    assert report["chain_counts"]["compound_target_pathway_phenotype"] == 2
    predicates = {edge.predicate for edge in graph.edges}
    assert {"STUDIED_IN", "REPORTS_OUTCOME", "TARGETS", "PARTICIPATES_IN"} <= predicates
    assert "TREATS" not in predicates
    assert all(edge.review_status == "approved" for edge in graph.edges)
    assert {row.locator["locus_id"] for row in graph.evidence} == {"locus:1", "locus:2"}
    studies = [node for node in graph.nodes if node.entity_type == "Study"]
    assert len(studies) == 1
    assert "study_type" not in studies[0].attributes
