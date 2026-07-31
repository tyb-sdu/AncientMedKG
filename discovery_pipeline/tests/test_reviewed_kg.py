from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from discovery_pipeline.annotation import AnnotationError
from discovery_pipeline.reviewed_kg import build_reviewed_kg_bundle
from knowledge_graph.build import build_bundle
from knowledge_graph.source_verify import verify_graph_sources
from knowledge_graph.validate import validate_graph


class ReviewedKgTests(unittest.TestCase):
    def _fixtures(self, root: Path) -> tuple[Path, Path, Path]:
        text = "Chlorogenic acid improved wound closure in the experimental model."
        database = root / "rag.db"
        connection = sqlite3.connect(database)
        try:
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
                    "doc:1",
                    "Synthetic wound study",
                    "2026",
                    "10.example/wound",
                    "synthetic.pdf",
                    "a" * 64,
                ),
            )
            connection.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                ("chunk:1", "doc:1", 7, text),
            )
            connection.commit()
        finally:
            connection.close()

        catalog = root / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "catalog_id": "test",
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
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        annotation = {
            "batch_id": "review:test",
            "locus_id": "locus:1",
            "candidate_id": "compound:chlorogenic_acid",
            "canonical_name": "chlorogenic acid",
            "context_class": "wound_context",
            "doc_id": "doc:1",
            "title": "Synthetic wound study",
            "year": "2026",
            "doi": "10.example/wound",
            "source_filename": "synthetic.pdf",
            "source_sha256": "a" * 64,
            "pdf_page": 7,
            "chunk_id": "chunk:1",
            "chunk_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "matched_terms": ["chlorogenic acid"],
            "context_terms": ["wound"],
            "snippet": text,
            "reviewer_a_id": "alice",
            "reviewer_a_reviewed_at": "2026-07-31",
            "reviewer_b_id": "bob",
            "reviewer_b_reviewed_at": "2026-07-31",
            "adjudicator_id": "carol",
            "adjudicated_at": "2026-07-31",
            "adjudication_decision": "approve",
            "adjudication_notes": "verified",
            "final_annotation": {
                "full_text_checked": "yes",
                "source_page_verified": "yes",
                "relevance_label": "direct_wound",
                "study_type": "animal",
                "evidence_direction": "supportive",
                "supports_c1_source": "yes",
                "supports_c2_exposure": "uncertain",
                "supports_c3_burn_wound": "yes",
                "supports_c4_target_pathway": "uncertain",
                "supports_c5_safety": "no",
                "confidence_1_to_5": "4",
            },
            "review_status": "approved",
            "scientific_evidence_approved": True,
        }
        final_dir = root / "final"
        final_dir.mkdir()
        annotations = final_dir / "final_annotations.jsonl"
        annotations.write_text(
            json.dumps(annotation, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report = final_dir / "finalization_report.json"
        report.write_text(
            json.dumps(
                {
                    "valid": True,
                    "batch_id": "review:test",
                    "item_count": 1,
                    "approved_scientific_evidence_count": 1,
                    "files": {
                        "final_annotations.jsonl": {
                            "sha256": hashlib.sha256(annotations.read_bytes()).hexdigest()
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return report, catalog, database

    def test_builds_traceable_pending_identity_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, catalog, database = self._fixtures(root)
            bundle, result = build_reviewed_kg_bundle(
                finalization_report_path=report,
                catalog_path=catalog,
                modern_database_path=database,
                graph_version="reviewed-overlay-v1",
                parent_version="base-v1",
            )
            self.assertTrue(result["valid"])
            self.assertEqual(result["evidence_count"], 1)
            self.assertEqual(result["assertion_count"], 1)
            self.assertFalse(result["release_ready"])
            self.assertEqual(bundle["assertions"][0]["predicate"], "STUDIED_IN")
            self.assertEqual(bundle["assertions"][0]["review_status"], "pending")
            self.assertEqual(bundle["evidence"][0]["review"]["status"], "approved")

            graph = build_bundle(bundle)
            self.assertTrue(validate_graph(graph, release=False)["valid"])
            release = validate_graph(graph, release=True)
            self.assertFalse(release["valid"])
            self.assertEqual(
                {issue["code"] for issue in release["issues"]},
                {"edge_not_approved"},
            )
            verification = verify_graph_sources(graph, modern_database=database)
            self.assertTrue(verification["valid"])
            self.assertEqual(verification["checks"][0]["quote_match"], "exact")

    def test_rejects_database_text_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, catalog, database = self._fixtures(root)
            connection = sqlite3.connect(database)
            try:
                connection.execute("UPDATE chunks SET text = 'changed' WHERE chunk_id = 'chunk:1'")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(AnnotationError, "differs from database"):
                build_reviewed_kg_bundle(
                    finalization_report_path=report,
                    catalog_path=catalog,
                    modern_database_path=database,
                    graph_version="reviewed-overlay-v1",
                )


if __name__ == "__main__":
    unittest.main()
