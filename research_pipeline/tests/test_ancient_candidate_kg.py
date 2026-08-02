from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from knowledge_graph.build import build_bundle_file
from knowledge_graph.source_verify import verify_graph_sources
from knowledge_graph.validate import validate_graph
from research_pipeline.build_ancient_candidate_kg import (
    build_ancient_candidate_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "research_pipeline" / "data" / "burn_ontology_v1.json"


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE books (
                book_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                page_count INTEGER NOT NULL,
                processing_mode TEXT NOT NULL
            );
            CREATE TABLE pages (
                page_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                physical_page INTEGER NOT NULL,
                pdf_page_label TEXT,
                text TEXT NOT NULL,
                reading_direction TEXT NOT NULL,
                average_confidence REAL,
                low_confidence INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "ancient:test",
                "01_测试古籍_卷一_汤火疮",
                "test.pdf",
                "/private/test.pdf",
                "a" * 64,
                4,
                "ocr_test",
            ),
        )
        pages = (
            "湯火傷，皮肉潰爛。宜清熱解毒，外塗之。",
            "瘡瘍腫痛，熱毒內盛，宜消腫止痛。",
            "傷寒煩渴，藥物燒灰後入湯。",
            "艾灸治療後見火瘡。",
        )
        for page, text in enumerate(pages, start=1):
            connection.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"ancient:test:p{page:06d}",
                    "ancient:test",
                    page,
                    str(page),
                    text,
                    "rtl_vertical",
                    0.95,
                    0,
                    json.dumps({"text": text}, ensure_ascii=False),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def test_candidate_graph_is_traceable_pending_and_conservative() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "ancient.db"
        bundle_path = root / "candidate_bundle.json"
        manifest_path = root / "candidate_manifest.jsonl"
        _database(database)
        _, report = build_ancient_candidate_bundle(
            database_path=database,
            ontology_path=ONTOLOGY,
            output_bundle_path=bundle_path,
            output_manifest_path=manifest_path,
            graph_version="candidate-test-v1",
            parent_version="accepted-sample-v1",
        )
        assert report["source_database_unchanged"] is True
        assert report["scanned_pages"] == 4
        assert report["selected_pages"] == 2
        rows = [
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [value["classification"] for value in rows] == [
            "direct_burn_candidate",
            "ulcer_transfer_candidate",
        ]
        assert all("text" not in value for value in rows)

        graph = build_bundle_file(bundle_path)
        draft = validate_graph(graph, release=False)
        assert draft["valid"] is True
        assert not [value for value in draft["issues"] if value["severity"] == "error"]
        release = validate_graph(graph, release=True)
        assert release["valid"] is False
        assert {
            value["code"] for value in release["issues"] if value["severity"] == "error"
        } <= {"evidence_not_approved", "edge_not_approved"}
        verification = verify_graph_sources(graph, ancient_database=database)
        assert verification["valid"] is True
        assert verification["status_counts"]["verified"] == len(graph.evidence)
        assert all(value.review["status"] == "pending" for value in graph.evidence)
        assert all(value.review_status == "pending" for value in graph.edges)
        assert not any(value.predicate == "TREATS" for value in graph.edges)
        hypotheses = [
            value for value in graph.edges if value.predicate == "HAS_TREATMENT_METHOD"
        ]
        assert hypotheses
        assert all(value.evidence_grade == "E5" for value in hypotheses)
        assert all(value.assertion_mode == "hypothesis" for value in hypotheses)
