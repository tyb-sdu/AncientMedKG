from __future__ import annotations

import sqlite3
from pathlib import Path

from research_pipeline.query_planner import (
    plan_question,
    query_curated_lexical,
    query_specialized,
    traditionalize,
)


def _database(path: Path) -> dict:
    with sqlite3.connect(path) as conn:
        conn.executescript(
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
                pdf_page_label TEXT,
                text TEXT,
                reading_direction TEXT,
                average_confidence REAL,
                low_confidence INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO books VALUES (?, ?, ?, ?)",
            ("ancient:test", "医学心悟_公开扫描版", "source.pdf", "abc"),
        )
        conn.execute(
            "INSERT INTO books VALUES (?, ?, ?, ?)",
            ("ancient:distractor", "本草测试", "other.pdf", "def"),
        )
        conn.executemany(
            "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "ancient:test:p1",
                    "ancient:test",
                    1,
                    "1",
                    "內癰胃脘癰也忍冬湯主之",
                    "rtl",
                    0.9,
                    0,
                ),
                (
                    "ancient:test:p2",
                    "ancient:test",
                    2,
                    "2",
                    "忍冬湯一切內外癰腫金銀花四兩甘草三錢水煎頓服能飲者用酒煎服",
                    "rtl",
                    0.9,
                    0,
                ),
                (
                    "ancient:test:p3",
                    "ancient:test",
                    3,
                    "3",
                    "楊梅結毒宜服忍冬湯土茯苓四兩水煎每日一劑須盡飲",
                    "rtl",
                    0.9,
                    0,
                ),
                (
                    "ancient:distractor:p1",
                    "ancient:distractor",
                    1,
                    "1",
                    "忍冬忍冬忍冬金銀花甘草",
                    "rtl",
                    0.9,
                    0,
                ),
            ],
        )
    return {"paths": {"ancient_database": str(path)}}


def test_traditionalize_relevant_terms() -> None:
    assert traditionalize("医学心悟内外痈肿忍冬汤") == "醫學心悟內外癰腫忍冬湯"


def test_boundary_questions_abstain() -> None:
    plans = [
        plan_question("《医学心悟》是否直接记载忍冬汤治疗烧伤？"),
        plan_question("《医学心悟》是否记载忍冬汤外敷烧伤创面？"),
        plan_question("忍冬汤原量换算成多少现代克数治疗烧伤？"),
    ]
    assert all(plan.abstain for plan in plans)
    assert {plan.boundary_code for plan in plans} == {
        "NO_DIRECT_ANCIENT_BURN_CLAIM",
        "NO_ANCIENT_TOPICAL_BURN_ROUTE",
        "NO_MODERN_CLINICAL_DOSE_CONVERSION",
    }


def test_same_name_variants_are_ranked_separately(tmp_path: Path) -> None:
    cfg = _database(tmp_path / "ancient.db")
    neiyong = plan_question("主治内外痈肿的忍冬汤由哪两味药组成？")
    yangmei = plan_question("含土茯苓四两的忍冬汤是否与二味方相同？")

    assert query_curated_lexical(cfg, neiyong, top_k=1)[0]["pdf_page"] == 2
    assert query_curated_lexical(cfg, yangmei, top_k=1)[0]["pdf_page"] == 3


def test_context_question_prefers_preceding_context_page(tmp_path: Path) -> None:
    cfg = _database(tmp_path / "ancient.db")
    plan = plan_question("《医学心悟》忍冬汤条目前文属于哪类内痈语境？")
    assert query_curated_lexical(cfg, plan, top_k=1)[0]["pdf_page"] == 1


def test_specialized_query_keeps_baseline_metadata(tmp_path: Path) -> None:
    cfg = _database(tmp_path / "ancient.db")

    def baseline(_cfg: dict, _query: str, _mode: str, _top_k: int) -> list[dict]:
        return [
            {
                "chunk_id": "ancient:test:p2",
                "doc_id": "ancient:test",
                "pdf_page": 2,
                "title": "医学心悟_公开扫描版",
                "reranker_score": 0.75,
            }
        ]

    results, plan = query_specialized(
        cfg,
        "《医学心悟》内外痈肿忍冬汤如何煎服？",
        "qwen-reranked-hybrid",
        baseline_query=baseline,
    )
    assert not plan.abstain
    assert results[0]["pdf_page"] == 2
    assert results[0]["baseline_rank"] == 1
    assert results[0]["reranker_score"] == 0.75
