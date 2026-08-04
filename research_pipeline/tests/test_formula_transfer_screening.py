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
    for candidate in specification["candidates"]:
        locator = candidate["source_locator"]
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
        "total": 30,
        "direct_references": 18,
        "transfer_candidates_and_controls": 12,
        "high_priority_transfer": 1,
        "discarded": 11,
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
