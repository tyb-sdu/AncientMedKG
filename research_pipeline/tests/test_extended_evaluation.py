from __future__ import annotations

import sqlite3
from pathlib import Path

from research_pipeline.build_extended_evaluation import build_extended_questions


def test_extended_questions_are_source_anchored_and_not_rank_derived(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ancient.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE books (book_id TEXT PRIMARY KEY, title TEXT);
            CREATE TABLE pages (
                page_id TEXT PRIMARY KEY,
                book_id TEXT,
                physical_page INTEGER,
                text TEXT
            );
            INSERT INTO books VALUES ('ancient:a', '外科甲'), ('ancient:b', '外科乙');
            INSERT INTO pages VALUES
                ('ancient:a:p1', 'ancient:a', 1, '汤火疮宜清热解毒外敷甘草'),
                ('ancient:a:p2', 'ancient:a', 2, '忍冬汤金银花甘草水煎'),
                ('ancient:b:p1', 'ancient:b', 1, '火烧成疮宜生肌止痛'),
                ('ancient:b:p2', 'ancient:b', 2, '研末调敷消肿');
            """
        )
    questions, report = build_extended_questions(
        database, per_book=4, minimum_positive=4
    )
    positives = [item for item in questions if item["expect_answer"]]
    assert report["book_count"] == 2
    assert report["positive_questions"] >= 4
    assert report["no_answer_questions"] == 20
    assert all(item["question_type"] == "source_anchored_locator" for item in positives)
    assert all(item["expected_loci"][0]["doc_id"].startswith("ancient:") for item in positives)
    assert "not generated from retrieval rankings" in report["independence_note"]
