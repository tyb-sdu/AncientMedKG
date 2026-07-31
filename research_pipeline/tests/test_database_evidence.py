from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from research_pipeline.database_evidence import sha256_file, verify_database_evidence
from research_pipeline.validation import load_json


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = (
    ROOT / "research_pipeline" / "data" / "rendongtang_evidence_v1.json"
)
BOOK_ID = "ancient:da1657c4376cd7e6ba9e"
TITLE = "医学心悟_公开扫描版"


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
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
        (BOOK_ID, TITLE, "医学心悟.pdf", "/private/source.pdf", "a" * 64, 235, "ocr"),
    )
    rows = [
        (
            f"{BOOK_ID}:p000137",
            137,
            "內癰。胃脘癰也忍冬湯主之。其后列方。",
        ),
        (
            f"{BOOK_ID}:p000138",
            138,
            "忍冬湯 一切內外癰腫皆可立消但宜蚤服 "
            "金銀花四兩 甘草三錢 水煎頓服能飲者用酒煎服",
        ),
        (
            f"{BOOK_ID}:p000227",
            227,
            "楊梅結毒宜服忍冬湯 金銀花一兩 甘草二錢 "
            "黑料豆二兩 土茯苓四兩 水煎每日一劑須盡飲",
        ),
    ]
    connection.executemany(
        "INSERT INTO pages VALUES (?, ?, ?, NULL, ?, 'vertical', 0.99, 0, '{}')",
        [(page_id, BOOK_ID, page, text) for page_id, page, text in rows],
    )
    connection.commit()
    connection.close()


def test_database_evidence_verification_is_read_only(tmp_path: Path) -> None:
    database = tmp_path / "ancient_rag.db"
    create_database(database)
    before = sha256_file(database)

    report = verify_database_evidence(database, load_json(EVIDENCE_PATH))

    assert report["valid"] is True
    assert report["issues"] == []
    assert report["read_only"] is True
    assert report["sqlite_quick_check"] == "ok"
    assert report["locus_count"] == 3
    assert report["database_sha256"] == before
    assert sha256_file(database) == before
    assert {record["physical_page"] for record in report["loci"]} == {137, 138, 227}


def test_database_evidence_reports_missing_required_term(tmp_path: Path) -> None:
    database = tmp_path / "ancient_rag.db"
    create_database(database)
    evidence = copy.deepcopy(load_json(EVIDENCE_PATH))
    evidence["formula_instances"][0]["source_locus"]["evidence_terms"].append(
        "原页不存在的测试词"
    )

    report = verify_database_evidence(database, evidence)

    assert report["valid"] is False
    assert any(issue["reason"] == "evidence_terms_missing" for issue in report["issues"])
