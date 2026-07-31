from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from discovery_pipeline.corpus_scan import matched_terms, scan_corpus


class CorpusScanTests(unittest.TestCase):
    def _database(self, path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE documents (
                    doc_id TEXT PRIMARY KEY,
                    title TEXT,
                    year TEXT,
                    doi TEXT,
                    source_filename TEXT,
                    sha256 TEXT,
                    relevance_score INTEGER,
                    topic_tags TEXT
                );
                CREATE TABLE chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT,
                    pdf_page INTEGER,
                    chunk_index INTEGER,
                    text TEXT,
                    normalized_text TEXT
                );
                """
            )
            connection.executemany(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("doc:1", "Relevant", "2025", "10.example/1", "one.pdf", "a" * 64, 100, "[]"),
                    ("doc:2", "Boundary distractor", "2025", "10.example/2", "two.pdf", "b" * 64, 50, "not-json"),
                ],
            )
            connection.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("chunk:1", "doc:1", 3, 0, "Rutin was studied in wound healing.", "rutin was studied in wound healing."),
                    ("chunk:2", "doc:2", 4, 0, "Routine wound care.", "routine wound care."),
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def test_ascii_compound_uses_word_boundaries(self) -> None:
        self.assertEqual(matched_terms("routine analysis", ["rutin"]), [])
        self.assertEqual(matched_terms("rutin analysis", ["rutin"]), ["rutin"])

    def test_scan_emits_only_true_term_match(self) -> None:
        catalog = {
            "catalog_id": "test",
            "candidates": [
                {
                    "candidate_id": "compound:rutin",
                    "canonical_name": "rutin",
                    "name_zh": "芦丁",
                    "aliases": [],
                    "herb_ids": ["herb:test"],
                    "candidate_role": "backup",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "rag.db"
            self._database(database)
            summary = scan_corpus(database, catalog, root / "output")
            self.assertEqual(summary["locus_count"], 1)
            candidate = summary["candidates"][0]
            self.assertEqual(candidate["document_count"], 1)
            self.assertEqual(candidate["wound_or_burn_document_count"], 1)
            locus = json.loads(
                (root / "output" / "compound_loci.jsonl")
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertEqual(locus["chunk_id"], "chunk:1")
            self.assertEqual(locus["review_status"], "pending_full_text_review")

    def test_malformed_topic_tags_are_reported_without_losing_hits(self) -> None:
        catalog = {
            "catalog_id": "test",
            "candidates": [
                {
                    "candidate_id": "compound:rutin",
                    "canonical_name": "rutin",
                    "name_zh": "",
                    "aliases": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "rag.db"
            self._database(database)
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE chunks SET text = 'rutin', normalized_text = 'rutin' "
                    "WHERE doc_id = 'doc:2'"
                )
                connection.commit()
            finally:
                connection.close()
            summary = scan_corpus(database, catalog, root / "output")
            self.assertEqual(
                summary["data_quality"]["malformed_topic_tags_document_ids"],
                ["doc:2"],
            )
            records = [
                json.loads(line)
                for line in (root / "output" / "compound_loci.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            malformed = next(value for value in records if value["doc_id"] == "doc:2")
            self.assertEqual(malformed["document_topic_tags_status"], "malformed")
            self.assertEqual(malformed["document_topic_tags"], [])


if __name__ == "__main__":
    unittest.main()
