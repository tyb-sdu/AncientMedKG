from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from knowledge_graph.build import build_bundle_file
from knowledge_graph.ids import make_node_id
from knowledge_graph.source_verify import verify_graph_sources
from knowledge_graph.store import load_graph, write_graph
from knowledge_graph.validate import validate_graph
from research_pipeline.build_ancient_candidate_kg import (
    build_ancient_candidate_bundle,
)
from research_pipeline.formula_candidates import (
    extract_formula_candidates,
    load_formula_lexicon,
)
from research_pipeline.promote_confident_candidates import (
    promote_confident_candidates,
)
from research_pipeline.run_automatic_ancient_kg import (
    run_automatic_ancient_kg,
)


ROOT = Path(__file__).resolve().parents[2]
LEXICON_PATH = ROOT / "research_pipeline" / "data" / "formula_herb_lexicon_v1.json"
ONTOLOGY_PATH = ROOT / "research_pipeline" / "data" / "burn_ontology_v1.json"


def test_extracts_exact_rendongtang_composition_and_preparation() -> None:
    lexicon = load_formula_lexicon(LEXICON_PATH)
    candidates = extract_formula_candidates(
        "一切內外癰腫皆可立消但宜蚤服。忍冬湯，金銀花四兩，甘草三錢，水煎頓服。",
        lexicon,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["canonical_name"] == "忍冬汤"
    assert candidate["composition"] == [
        {
            "herb": "金银花",
            "dose_value": "四",
            "dose_unit": "两",
            "dose_text_original": "四兩",
        },
        {
            "herb": "甘草",
            "dose_value": "三",
            "dose_unit": "钱",
            "dose_text_original": "三錢",
        },
    ]
    assert candidate["semantic_confidence"] >= 0.7
    assert candidate["composition_complete"] is False
    assert set(candidate["preparation_markers"]) == {"水煎", "頓服"}


def test_same_name_distinct_compositions_produce_distinct_variant_ids() -> None:
    lexicon = load_formula_lexicon(LEXICON_PATH)
    first = extract_formula_candidates(
        "忍冬湯金銀花四兩甘草三錢水煎頓服", lexicon
    )[0]
    second = extract_formula_candidates(
        "忍冬湯金銀花一[OCR待影像核定]甘草二錢黑料豆二兩土茯苓四兩水煎每日一劑",
        lexicon,
    )[0]
    assert first["composition"] != second["composition"]
    assert second["undosed_ingredients"] == ["金银花"]
    assert {value["herb"] for value in second["composition"]} == {
        "甘草",
        "黑料豆",
        "土茯苓",
    }
    first_attributes = {
        "formula_name": first["canonical_name"],
        "composition": first["composition"],
        "source_locator": {"page_id": "p138", "physical_page": 138},
    }
    second_attributes = {
        "formula_name": second["canonical_name"],
        "composition": second["composition"],
        "source_locator": {"page_id": "p227", "physical_page": 227},
    }
    assert make_node_id(
        "FormulaVariant", first["canonical_name"], attributes=first_attributes
    ) != make_node_id(
        "FormulaVariant", second["canonical_name"], attributes=second_attributes
    )


def test_rejects_formula_name_without_two_dosed_target_herbs() -> None:
    lexicon = load_formula_lexicon(LEXICON_PATH)
    assert extract_formula_candidates("忍冬湯名見目錄。", lexicon) == []
    assert extract_formula_candidates("忍冬湯，甘草三錢。", lexicon) == []
    assert extract_formula_candidates("甘草三錢，金銀花四兩。", lexicon) == []
    assert extract_formula_candidates("湯火傷，水煎服。", lexicon) == []


def test_two_same_name_entries_on_one_page_are_kept_separate() -> None:
    lexicon = load_formula_lexicon(LEXICON_PATH)
    candidates = extract_formula_candidates(
        "忍冬湯金銀花四兩甘草三錢。又方忍冬湯甘草二錢黑料豆二兩土茯苓四兩。",
        lexicon,
    )
    assert len(candidates) == 2
    assert [len(value["composition"]) for value in candidates] == [2, 3]
    assert candidates[0]["window_end"] <= candidates[1]["name_start"]


def test_lexicon_is_json_serializable_and_versioned() -> None:
    lexicon = load_formula_lexicon(LEXICON_PATH)
    assert lexicon["schema_version"] == 1
    assert lexicon["lexicon_id"] == "tcm-burn-target-formulas-v1"
    json.dumps(lexicon, ensure_ascii=False)


def _formula_database(path: Path) -> None:
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
                "ancient:formula-test",
                "医学心悟_测试版",
                "yixuexinwu.pdf",
                "/private/yixuexinwu.pdf",
                "b" * 64,
                3,
                "ocr_test",
            ),
        )
        pages = (
            "忍冬湯金銀花四兩甘草三錢水煎頓服",
            "忍冬湯金銀花一[OCR待影像核定]甘草二錢黑料豆二兩土茯苓四兩水煎每日一劑",
            "忍冬湯名見目錄",
        )
        for page, text in enumerate(pages, start=1):
            connection.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"ancient:formula-test:p{page:06d}",
                    "ancient:formula-test",
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


def test_formula_candidates_integrate_and_auto_release_without_efficacy_claims() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "ancient.db"
        bundle_path = root / "bundle.json"
        manifest_path = root / "candidates.jsonl"
        candidate_graph_dir = root / "candidate_graph"
        approved_graph_dir = root / "approved_graph"
        approval_report_path = root / "approval.json"
        _formula_database(database)
        _, report = build_ancient_candidate_bundle(
            database_path=database,
            ontology_path=ONTOLOGY_PATH,
            formula_lexicon_path=LEXICON_PATH,
            output_bundle_path=bundle_path,
            output_manifest_path=manifest_path,
            graph_version="formula-candidate-test-v1",
        )
        assert report["selected_pages"] == 2
        assert report["formula_candidates"] == 2
        assert report["classification_counts"] == {
            "formula_reference_candidate": 2,
            "not_selected": 1,
        }

        graph = build_bundle_file(bundle_path)
        assert validate_graph(graph, release=False)["valid"] is True
        assert verify_graph_sources(graph, ancient_database=database)["valid"] is True
        variants = [value for value in graph.nodes if value.entity_type == "FormulaVariant"]
        assert len(variants) == 2
        assert len({value.node_id for value in variants}) == 2
        assert len([value for value in graph.nodes if value.entity_type == "FormulaConcept"]) == 1
        assert len([value for value in graph.nodes if value.entity_type == "Herb"]) == 4
        assert len([value for value in graph.edges if value.predicate == "HAS_INGREDIENT"]) == 5
        assert not any(value.predicate == "TREATS" for value in graph.edges)

        write_graph(
            graph,
            candidate_graph_dir,
            validation_report=validate_graph(graph, release=False),
        )
        approval = promote_confident_candidates(
            input_graph_dir=candidate_graph_dir,
            output_graph_dir=approved_graph_dir,
            output_report_path=approval_report_path,
            graph_version="formula-approved-test-v1",
            threshold=0.7,
            approved_at="2026-08-02",
        )
        assert approval["release_validation_valid"] is True
        approved = load_graph(approved_graph_dir)
        assert validate_graph(approved, release=True)["valid"] is True
        assert len([value for value in approved.nodes if value.entity_type == "FormulaVariant"]) == 2
        assert not any(value.predicate == "TREATS" for value in approved.edges)


def test_end_to_end_automatic_pipeline_is_atomic_and_release_valid() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "ancient.db"
        output = root / "release-v1"
        _formula_database(database)
        report = run_automatic_ancient_kg(
            database_path=database,
            ontology_path=ONTOLOGY_PATH,
            formula_lexicon_path=LEXICON_PATH,
            output_root=output,
            candidate_graph_version="formula-candidate-test-v2",
            approved_graph_version="formula-approved-test-v2",
            threshold=0.7,
            approved_at="2026-08-02",
        )
        assert report["valid"] is True
        assert report["human_review_required"] is False
        assert report["release_doctor_valid"] is True
        assert (output / "pipeline_report.json").is_file()
        assert (output / "neo4j" / "neo4j_import_manifest.json").is_file()
        assert load_graph(output / "approved_graph").graph_version == (
            "formula-approved-test-v2"
        )
        assert not output.with_name(f".{output.name}.tmp").exists()


def test_formula_and_burn_evidence_do_not_share_a_confidence_identity() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "ancient.db"
        bundle_path = root / "bundle.json"
        manifest_path = root / "manifest.jsonl"
        _formula_database(database)
        text = "忍冬湯金銀花四兩甘草三錢水煎頓服治湯火傷"
        connection = sqlite3.connect(database)
        try:
            connection.execute("DELETE FROM pages WHERE physical_page > 1")
            connection.execute("UPDATE books SET page_count = 1")
            connection.execute(
                "UPDATE pages SET text = ?, payload_json = ? WHERE physical_page = 1",
                (text, json.dumps({"text": text}, ensure_ascii=False)),
            )
            connection.commit()
        finally:
            connection.close()
        build_ancient_candidate_bundle(
            database_path=database,
            ontology_path=ONTOLOGY_PATH,
            formula_lexicon_path=LEXICON_PATH,
            output_bundle_path=bundle_path,
            output_manifest_path=manifest_path,
            graph_version="channel-isolation-test-v1",
        )
        graph = build_bundle_file(bundle_path)
        page_evidence = [
            value for value in graph.evidence if value.locator["physical_page"] == 1
        ]
        duplicate_quotes = {
            value.quote
            for value in page_evidence
            if sum(other.quote == value.quote for other in page_evidence) > 1
        }
        assert duplicate_quotes
        assert len({value.evidence_id for value in page_evidence}) == len(page_evidence)
        assert any("char_start" in value.locator for value in page_evidence)
        assert any("char_start" not in value.locator for value in page_evidence)
