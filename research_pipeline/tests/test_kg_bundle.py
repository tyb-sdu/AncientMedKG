from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from knowledge_graph.build import build_bundle
from knowledge_graph.source_verify import verify_graph_sources
from knowledge_graph.validate import validate_graph
from research_pipeline.build_kg_bundle import build_kg_evidence_bundle


EVIDENCE = (
    Path(__file__).resolve().parents[1] / "data" / "rendongtang_evidence_v1.json"
)


def _database(path: Path) -> Path:
    package = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    source_sha = "b" * 64
    page_texts = {
        137: "內癰諸證胃脘癰也忍冬湯主之",
        138: "忍冬湯一切內外癰腫皆可立消但宜蚤服金銀花四兩甘草三錢水煎頓服能飲者用酒煎服",
        227: "楊梅結毒不可搽輕粉宜服忍冬湯金銀花一甘草二錢黑料豆二兩土茯苓四兩水煎每日一劑須盡飲",
    }
    with sqlite3.connect(path) as connection:
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
                package["work"]["book_id"],
                package["work"]["title"],
                package["work"]["filename"],
                source_sha,
            ),
        )
        connection.executemany(
            "INSERT INTO pages VALUES (?, ?, ?, ?)",
            [
                (
                    f"{package['work']['book_id']}:p{page:06d}",
                    package["work"]["book_id"],
                    page,
                    text,
                )
                for page, text in page_texts.items()
            ],
        )
    return path


def _bundle(tmp_path: Path) -> tuple[dict, Path]:
    package = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    database = _database(tmp_path / "ancient.db")
    return (
        build_kg_evidence_bundle(
            package,
            database,
            evidence_input_sha256="c" * 64,
        ),
        database,
    )


def test_bundle_is_deterministic_and_source_complete(tmp_path: Path) -> None:
    first, database = _bundle(tmp_path)
    package = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    second = build_kg_evidence_bundle(
        package, database, evidence_input_sha256="c" * 64
    )
    assert first == second
    ancient_source = next(
        item for item in first["sources"] if item["source_type"] == "ancient_pdf"
    )
    assert ancient_source["file_sha256"] == "b" * 64
    assert ancient_source["attributes"]["book_id"].startswith("ancient:")
    assert all(
        {"page_id", "physical_page", "page_text_sha256"}
        <= set(item["locator"])
        for item in first["evidence"]
        if item["evidence_class"] == "direct_ancient"
    )


def test_bundle_builds_as_valid_draft_but_not_release(tmp_path: Path) -> None:
    bundle, database = _bundle(tmp_path)
    graph = build_bundle(bundle)
    draft_report = validate_graph(graph, release=False)
    release_report = validate_graph(graph, release=True)
    source_report = verify_graph_sources(graph, ancient_database=database)

    assert draft_report["valid"], draft_report["issues"]
    assert not release_report["valid"]
    assert "evidence_not_approved" in {
        item["code"] for item in release_report["issues"]
    }
    assert source_report["valid"], source_report["checks"]


def test_same_name_variants_and_burn_boundary(tmp_path: Path) -> None:
    bundle, _ = _bundle(tmp_path)
    graph = build_bundle(bundle)
    variants = [node for node in graph.nodes if node.entity_type == "FormulaVariant"]
    burn_ids = {
        node.node_id for node in graph.nodes if node.entity_type == "BurnPhenotype"
    }
    assert len(variants) == 2
    assert len({node.node_id for node in variants}) == 2
    assert len(
        {node.attributes["composition_fingerprint"] for node in variants}
    ) == 2
    burn_edges = [edge for edge in graph.edges if edge.object_id in burn_ids]
    assert burn_edges
    assert all(edge.predicate == "MECHANISM_TRANSFER" for edge in burn_edges)
    assert all(edge.evidence_grade in {"E4", "E5"} for edge in burn_edges)
    assert all(edge.assertion_mode == "hypothesis" for edge in burn_edges)
    assert all(edge.review_status == "pending" for edge in graph.edges)
