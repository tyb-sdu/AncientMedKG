from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from research_pipeline.formula_transfer_screening import (
    FormulaTransferScreeningError,
    screen_formula_transfer_candidates,
)


ROOT = Path(__file__).resolve().parents[2]
SPECIFICATION_PATH = (
    ROOT / "research_pipeline" / "data" / "formula_transfer_candidates_v1.json"
)


def _specification() -> dict:
    return json.loads(SPECIFICATION_PATH.read_text(encoding="utf-8"))


def _database(path: Path, specification: dict) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE books (
            book_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_sha256 TEXT NOT NULL
        );
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            physical_page INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        """
    )
    books: dict[str, str] = {}
    pages: dict[str, dict] = {}

    def add_locator(locator: dict) -> None:
        books.setdefault(locator["book_id"], f"book-{len(books) + 1}")
        page = pages.setdefault(
            locator["page_id"],
            {
                "book_id": locator["book_id"],
                "physical_page": locator["physical_page"],
                "anchors": [],
            },
        )
        page["anchors"].append(locator["source_anchor"])

    for candidate in specification["candidates"]:
        locator = candidate["source_locator"]
        add_locator(locator)
        feature = specification["formula_characteristics"][candidate["candidate_id"]]
        for evidence_name in ("route_evidence", "composition_evidence"):
            evidence = feature[evidence_name]
            add_locator(
                {
                    "book_id": evidence.get("book_id", locator["book_id"]),
                    "page_id": evidence.get("page_id", locator["page_id"]),
                    "physical_page": evidence.get(
                        "physical_page", locator["physical_page"]
                    ),
                    "source_anchor": evidence["source_anchor"],
                }
            )
    for book_id, title in books.items():
        connection.execute(
            "INSERT INTO books VALUES (?, ?, ?)", (book_id, title, "a" * 64)
        )
    for page_id, page in pages.items():
        connection.execute(
            "INSERT INTO pages VALUES (?, ?, ?, ?)",
            (
                page_id,
                page["book_id"],
                page["physical_page"],
                "。".join(page["anchors"]),
            ),
        )
    connection.commit()
    connection.close()


def test_rendongtang_p138_is_first_high_priority_transfer_candidate() -> None:
    specification = _specification()
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "ancient.db"
        _database(database, specification)
        report = screen_formula_transfer_candidates(
            specification=specification,
            ancient_database=database,
        )
    assert report["counts"] == {
        "total": 50,
        "source_confidence_passed": 50,
        "direct_references": 18,
        "external_heavy_metal_excluded": 7,
        "after_external_heavy_metal_gate": 43,
        "formula_administration_precheck_excluded": 1,
        "after_formula_administration_precheck": 42,
        "internal_decoctions": 14,
        "eligible_transfer_candidates_and_controls": 13,
        "high_priority_transfer": 1,
        "exploratory_transfer": 1,
        "side_path_non_internal_decoction": 28,
        "discarded_or_side_path": 47,
    }
    assert report["selected_high_priority"] == ["formula.rendongtang.xinwu.p138"]
    assert report["target_result"]["transfer_rank"] == 1
    assert report["target_result"]["transfer_score"] == 0.765
    assert report["target_result"]["decision"] == "high_priority_transfer"
    assert all(row["source_verified"] for row in report["transfer_ranking"])
    p227 = next(
        row
        for row in report["transfer_ranking"]
        if row["candidate_id"] == "formula.rendongtang.xinwu.p227"
    )
    assert p227["decision"] == "discarded"
    assert p227["transfer_score"] < 0.6
    assert p227["formula_characteristics"]["heavy_metal_hits"] == []


def test_external_heavy_metal_and_non_decoction_gates_are_explicit() -> None:
    specification = _specification()
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "ancient.db"
        _database(database, specification)
        report = screen_formula_transfer_candidates(
            specification=specification,
            ancient_database=database,
        )
    by_id = {row["candidate_id"]: row for row in report["all_candidates"]}
    assert by_id["formula.tanghuoyao.pujifang.p19"]["decision"] == (
        "excluded_external_heavy_metal"
    )
    assert by_id["formula.shuishuangsan.pujifang.p24"]["decision"] == (
        "excluded_external_heavy_metal"
    )
    assert by_id["formula.longquansan.pujifang.p34"]["decision"] == (
        "side_path_non_internal_decoction"
    )
    assert by_id["formula.rushengsan.pujifang.p22"]["decision"] == (
        "excluded_unresolved_formula_administration"
    )
    unresolved = by_id["formula.rushengsan.pujifang.p22"]
    assert unresolved["formula_characteristics"]["documentation_checks"] == {
        "formula_name_present": True,
        "composition_anchor_verified": True,
        "route_anchor_verified": True,
        "administration_route_resolved": False,
        "dosage_form_resolved": False,
    }
    assert by_id["formula.niuhuangjiedusan.zhengzhizhunsheng.p33"]["decision"] == (
        "side_path_non_internal_decoction"
    )
    assert [stage["stage"] for stage in report["stages"]] == [
        "source_gate",
        "external_heavy_metal_gate",
        "formula_administration_precheck",
        "internal_decoction_focus",
        "six_dimension_transfer_scoring",
    ]


def test_source_anchor_mismatch_is_rejected() -> None:
    specification = _specification()
    broken = copy.deepcopy(specification)
    broken["candidates"][0]["source_locator"]["source_anchor"] = "不存在的原文"
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "ancient.db"
        _database(database, specification)
        with pytest.raises(FormulaTransferScreeningError, match="source anchor mismatch"):
            screen_formula_transfer_candidates(
                specification=broken,
                ancient_database=database,
            )


def test_source_confidence_below_floor_is_discarded() -> None:
    specification = _specification()
    lowered = copy.deepcopy(specification)
    target = next(
        value
        for value in lowered["candidates"]
        if value["candidate_id"] == lowered["target_candidate_id"]
    )
    target["source_confidence"] = 0.69
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "ancient.db"
        _database(database, specification)
        report = screen_formula_transfer_candidates(
            specification=lowered,
            ancient_database=database,
        )
    assert report["target_result"]["decision"] == "discarded"
    assert report["target_result"]["transfer_rank"] is None


def test_characteristics_must_cover_exactly_the_candidate_pool() -> None:
    specification = _specification()
    specification["formula_characteristics"].pop(
        "formula.rendongtang.xinwu.p138"
    )
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "ancient.db"
        _database(database, _specification())
        with pytest.raises(
            FormulaTransferScreeningError, match="formula_characteristics candidate mismatch"
        ):
            screen_formula_transfer_candidates(
                specification=specification,
                ancient_database=database,
            )


def test_weights_must_sum_to_one() -> None:
    specification = _specification()
    specification["policy"]["weights"]["burn_context"] = 0.5
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "ancient.db"
        _database(database, specification)
        with pytest.raises(FormulaTransferScreeningError, match="sum to 1.0"):
            screen_formula_transfer_candidates(
                specification=specification,
                ancient_database=database,
            )
