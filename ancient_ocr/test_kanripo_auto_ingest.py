from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from kanripo_auto_ingest import (
    build_auto70_copy,
    doctor,
    parse_source_file,
    relevance_audit,
    text_quality_confidence,
)


def create_base_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE books (
                book_id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
                source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                page_count INTEGER NOT NULL, processing_mode TEXT NOT NULL
            );
            CREATE TABLE pages (
                page_id TEXT PRIMARY KEY, book_id TEXT NOT NULL,
                physical_page INTEGER NOT NULL, pdf_page_label TEXT, text TEXT NOT NULL,
                reading_direction TEXT NOT NULL, average_confidence REAL,
                low_confidence INTEGER NOT NULL, payload_json TEXT NOT NULL,
                UNIQUE(book_id, physical_page)
            );
            CREATE VIRTUAL TABLE pages_fts USING fts5(
                page_id UNINDEXED, book_id UNINDEXED, title, text,
                tokenize='unicode61'
            );
            INSERT INTO books VALUES ('ancient:base', '基线书', 'base.pdf', 'base.pdf',
                                      'aaaaaaaa', 1, 'native_text');
            INSERT INTO pages VALUES ('ancient:base:p000001', 'ancient:base', 1, '1',
                                      '基线页', 'vertical-rtl', 0.9, 0,
                                      '{"book_id":"ancient:base","text":"基线页"}');
            INSERT INTO pages_fts VALUES ('ancient:base:p000001', 'ancient:base',
                                          '基线书', '基线页');
            """
        )


def create_source_repo(path: Path) -> str:
    path.mkdir()
    (path / "Readme.org").write_text("#+TITLE: 測試外科書 / WYG\n", encoding="utf-8")
    (path / "KRTEST_001.txt").write_text(
        "#+TITLE: 測試外科書\n"
        "<pb:KRTEST_WYG_001-1a>¶\n湯火傷以此方治之有效¶\n"
        "<pb:KRTEST_OTHER_001-9a>¶\n其他版本標記不切頁¶\n"
        "<pb:KRTEST_WYG_001-1b>¶\n忍冬甘草治瘡腫疼痛¶\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(path), "-c", "user.name=Test", "-c",
            "user.email=test@example.com", "commit", "-qm", "fixture",
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def test_quality_score_is_explicit_and_strict() -> None:
    score, components = text_quality_confidence("湯火傷以忍冬甘草治之")
    assert score > 0.7
    assert components["visible_chars"] == 10
    assert text_quality_confidence("")[0] == 0.0


def test_parser_uses_only_preferred_edition_anchors(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text(
        "<pb:X_WYG_1a>¶甲乙丙¶<pb:X_ALT_9a>¶不應切頁¶<pb:X_WYG_1b>¶丁戊己¶",
        encoding="utf-8",
    )
    pages = parse_source_file(source, "WYG")
    assert [page.page_anchor for page in pages] == ["X_WYG_1a", "X_WYG_1b"]
    assert "不應切頁" in pages[0].text
    assert "<pb:" not in pages[0].text


def test_build_keeps_base_immutable_and_marks_auto_acceptance(tmp_path: Path) -> None:
    base = tmp_path / "base.db"
    create_base_database(base)
    original = base.read_bytes()
    sources = tmp_path / "sources"
    sources.mkdir()
    repo = sources / "KRTEST"
    commit = create_source_repo(repo)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "books": [
                    {
                        "repo": "KRTEST",
                        "title": "測試外科書",
                        "preferred_edition": "WYG",
                        "expected_commit": commit,
                        "project_fit": ["湯火傷"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    report = build_auto70_copy(
        base_database=base,
        sources_root=sources,
        output_dir=output,
        catalog_path=catalog,
    )
    assert base.read_bytes() == original
    assert report["new_book_count"] == 1
    assert report["new_pages_accepted"] == 2
    with sqlite3.connect(output / "ancient_rag.db") as connection:
        payloads = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload_json FROM pages WHERE book_id != 'ancient:base'"
        )]
    assert all(
        payload["acceptance"]["review_status"] == "auto_accepted_unreviewed"
        and payload["acceptance"]["human_image_reviewed"] is False
        for payload in payloads
    )
    check = doctor(output, sources)
    assert check["healthy"] is True
    assert check["auto_accepted_rows"] == 2
    relevance = relevance_audit(output)
    assert relevance["healthy"] is True
    assert relevance["books_with_burn_context"] == 1
    assert relevance["books"][0]["burn_context"]["samples"][0]["page_anchor"]
