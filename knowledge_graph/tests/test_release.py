from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from knowledge_graph.build import build_bundle_file
from knowledge_graph.export_neo4j import export_neo4j
from knowledge_graph.release import release_doctor, verify_neo4j_export
from knowledge_graph.store import load_graph, write_graph
from knowledge_graph.validate import validate_graph


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "evidence_bundle.example.json"
)


class ReleaseVerificationTests(unittest.TestCase):
    def _write_example_ancient_database(self, path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE books (
                    book_id TEXT PRIMARY KEY,
                    title TEXT,
                    filename TEXT,
                    source_sha256 TEXT
                );
                CREATE TABLE pages (
                    page_id TEXT PRIMARY KEY,
                    book_id TEXT,
                    physical_page INTEGER,
                    text TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO books VALUES (?, ?, ?, ?)",
                (
                    "ancient:example",
                    "示例古籍",
                    "example.pdf",
                    "a" * 64,
                ),
            )
            connection.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?)",
                (
                    "ancient:example:p000010",
                    "ancient:example",
                    10,
                    "示例病证，用示例治法，示例方由甲药一两、乙药三钱组成。",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_neo4j_export_is_bound_to_graph_build_fingerprint(self) -> None:
        source_graph = build_bundle_file(EXAMPLE)
        report = validate_graph(source_graph, release=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph_dir = root / "graph"
            neo4j_dir = root / "neo4j"
            write_graph(source_graph, graph_dir, validation_report=report)
            loaded_graph = load_graph(graph_dir)
            export_neo4j(loaded_graph, neo4j_dir)
            verification = verify_neo4j_export(loaded_graph, neo4j_dir)
            self.assertTrue(verification["valid"], verification["issues"])
            ancient_database = root / "ancient.db"
            self._write_example_ancient_database(ancient_database)
            doctor = release_doctor(
                graph_dir,
                neo4j_dir=neo4j_dir,
                ancient_database=ancient_database,
            )
            self.assertTrue(doctor["valid"], doctor)

            relationships = neo4j_dir / "relationships.csv"
            relationships.write_text(
                relationships.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            tampered = verify_neo4j_export(loaded_graph, neo4j_dir)
            self.assertFalse(tampered["valid"])
            self.assertIn(
                "neo4j_file_sha256_mismatch",
                {value["code"] for value in tampered["issues"]},
            )


if __name__ == "__main__":
    unittest.main()
