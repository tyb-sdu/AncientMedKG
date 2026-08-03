from __future__ import annotations

import sqlite3
from pathlib import Path

from rag_prep.ancient_retrieval import (
    ancient_locator_hints,
    ancient_query_is_out_of_scope,
    query_ancient_keyword,
)
from rag_prep.ancient_qwen_retrieval import (
    query_ancient_qwen_reranked_hybrid,
    query_ancient_qwen_vector,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE books (
                book_id TEXT PRIMARY KEY, title TEXT, filename TEXT, source_sha256 TEXT
            );
            CREATE TABLE pages (
                page_id TEXT PRIMARY KEY, book_id TEXT, physical_page INTEGER,
                pdf_page_label TEXT, text TEXT, reading_direction TEXT,
                average_confidence REAL, low_confidence INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO books VALUES (?, ?, ?, ?)",
            ("book:a", "医学心悟", "a.pdf", "a" * 64),
        )
        connection.execute(
            "INSERT INTO books VALUES (?, ?, ?, ?)",
            ("book:b", "外科理例", "b.pdf", "b" * 64),
        )
        connection.execute(
            "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "book:a:p000138",
                "book:a",
                138,
                "138",
                "忍冬湯 金銀花四兩 甘草三錢 一切癤瘡",
                "vertical_rtl",
                0.9,
                0,
            ),
        )
        connection.execute(
            "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "book:b:p000001",
                "book:b",
                1,
                "1",
                "金銀花另见于此",
                "vertical_rtl",
                0.9,
                0,
            ),
        )


def test_source_anchored_locator_handles_simplified_traditional_terms(tmp_path: Path) -> None:
    database = tmp_path / "ancient.db"
    _database(database)
    cfg = {"paths": {"ancient_database": str(database)}, "search": {}}
    question = "请定位《医学心悟》中关于“金银花”的原文页。"
    results = query_ancient_keyword(cfg, question, 10)
    assert [row["chunk_id"] for row in results] == ["book:a:p000138"]
    assert results[0]["retrieval_planner"] == "source_anchored_locator"
    assert ancient_locator_hints(question) is not None
    assert query_ancient_qwen_vector(cfg, question, 10) == results
    assert query_ancient_qwen_reranked_hybrid(cfg, question, 10) == results
    sore_question = "请定位《医学心悟》中关于“疮”的原文页。"
    assert query_ancient_keyword(cfg, sore_question, 10)[0]["pdf_page"] == 138


def test_obviously_modern_question_abstains_before_retrieval(tmp_path: Path) -> None:
    database = tmp_path / "ancient.db"
    _database(database)
    cfg = {"paths": {"ancient_database": str(database)}, "search": {}}
    question = "哪部古籍记载了CRISPR编辑治疗烧伤？"
    assert ancient_query_is_out_of_scope(question) is True
    assert query_ancient_keyword(cfg, question, 10) == []
