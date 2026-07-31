from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from knowledge_graph.ids import sha256_text
from knowledge_graph.model import EvidenceRecord, GraphData, SourceRecord
from knowledge_graph.source_verify import verify_graph_sources


class SourceVerificationTests(unittest.TestCase):
    def _ancient_database(self, path: Path, body: str, source_sha: str) -> None:
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
                ("ancient:book", "测试古籍", "test.pdf", source_sha),
            )
            connection.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?)",
                ("ancient:book:p000138", "ancient:book", 138, body),
            )
            connection.commit()
        finally:
            connection.close()

    def test_ancient_quote_resolves_to_page_and_sha(self) -> None:
        source_sha = "b" * 64
        body = "第一行\n甲药四两，乙药三钱。\n末行"
        source = SourceRecord(
            source_id="kg:source:test",
            source_type="ancient_pdf",
            title="测试古籍",
            file_name="test.pdf",
            file_sha256=source_sha,
            attributes={"book_id": "ancient:book"},
        )
        evidence = EvidenceRecord(
            evidence_id="kg:evidence:test",
            source_id=source.source_id,
            locator={
                "page_id": "ancient:book:p000138",
                "physical_page": 138,
                "page_text_sha256": sha256_text(body),
            },
            quote="甲药四两，乙药三钱。",
            quote_sha256=sha256_text("甲药四两，乙药三钱。"),
            evidence_grade="E1",
            evidence_class="direct_ancient",
            review={"status": "approved"},
        )
        graph = GraphData(
            schema_version="1.0.0",
            graph_version="test",
            bundle_id="test",
            sources=(source,),
            nodes=(),
            evidence=(evidence,),
            edges=(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "ancient.db"
            self._ancient_database(database, body, source_sha)
            report = verify_graph_sources(graph, ancient_database=database)
        self.assertTrue(report["valid"], report["checks"])
        self.assertEqual(report["checks"][0]["physical_page"], 138)
        self.assertEqual(report["checks"][0]["quote_match"], "exact")

    def test_missing_database_is_not_silently_accepted(self) -> None:
        source = SourceRecord(
            source_id="kg:source:test",
            source_type="ancient_pdf",
            title="测试古籍",
            file_sha256="b" * 64,
        )
        quote = "原文"
        evidence = EvidenceRecord(
            evidence_id="kg:evidence:test",
            source_id=source.source_id,
            locator={"physical_page": 1},
            quote=quote,
            quote_sha256=sha256_text(quote),
            evidence_grade="E1",
            evidence_class="direct_ancient",
            review={"status": "approved"},
        )
        graph = GraphData(
            schema_version="1.0.0",
            graph_version="test",
            bundle_id="test",
            sources=(source,),
            nodes=(),
            evidence=(evidence,),
            edges=(),
        )
        report = verify_graph_sources(graph)
        self.assertFalse(report["valid"])
        self.assertEqual(report["status_counts"]["unverified"], 1)


if __name__ == "__main__":
    unittest.main()
