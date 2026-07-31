import csv
import hashlib
import json
import sqlite3

import pytest

from promote_vl_candidates import promote_candidates


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_database(path) -> tuple[str, str]:
    source_sha = "a" * 64
    book_id = f"ancient:{source_sha[:20]}"
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
            payload_json TEXT NOT NULL,
            UNIQUE(book_id, physical_page)
        );
        CREATE VIRTUAL TABLE pages_fts USING fts5(
            page_id UNINDEXED,
            book_id UNINDEXED,
            title,
            text,
            tokenize='unicode61'
        );
        """
    )
    connection.execute(
        "INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, "测试古籍", "book.pdf", "book.pdf", source_sha, 2, "ocr"),
    )
    for page, text in ((1, "原文一"), (2, "原文二")):
        page_id = f"{book_id}:p{page:06d}"
        connection.execute(
            "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (page_id, book_id, page, str(page), text, "horizontal-ltr", 0.5, 1, "{}"),
        )
        connection.execute(
            "INSERT INTO pages_fts VALUES (?, ?, ?, ?)",
            (page_id, book_id, "测试古籍", text),
        )
    connection.commit()
    connection.close()
    return source_sha, book_id


def write_inputs(tmp_path, source_sha, book_id):
    candidate_root = tmp_path / "candidates_root"
    manifest = tmp_path / "manifest.csv"
    rows = []
    for page, original, candidate, flags in (
        (1, "原文一", "候选一", ""),
        (2, "原文二", "", "empty_candidate"),
    ):
        relative = f"candidates/{book_id.replace(':', '_')}/page_{page:06d}.json"
        candidate_path = candidate_root / relative
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        item = {
            "page_id": f"{book_id}:p{page:06d}",
            "book_id": book_id,
            "physical_page": page,
            "source_sha256": source_sha,
            "original_text_sha256": sha256_text(original),
            "candidate_text": candidate,
            "candidate_text_sha256": sha256_text(candidate),
        }
        candidate_path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        rows.append(
            {
                "book_id": book_id,
                "physical_page": page,
                "source_sha256": source_sha,
                "original_text_sha256": sha256_text(original),
                "candidate_text_sha256": sha256_text(candidate),
                "candidate_path": relative,
                "image_path": f"rendered/{book_id.replace(':', '_')}/page_{page:06d}.png",
                "review_flags": flags,
                "recommendation": "manual_compare_required" if flags else "vl_candidate_ready_for_review",
                "render_backend": "poppler_pdftoppm",
            }
        )
    with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return manifest, candidate_root


def test_promotes_nonempty_and_preserves_original_for_empty_candidate(tmp_path) -> None:
    source_database = tmp_path / "ancient_rag.db"
    source_sha, book_id = build_database(source_database)
    manifest, candidate_root = write_inputs(tmp_path, source_sha, book_id)
    output_database = tmp_path / "ancient_rag_vnext.db"

    report = promote_candidates(
        manifest,
        candidate_root,
        source_database,
        output_database,
        expected_count=2,
        expected_page_count=2,
        expected_adopted_count=1,
        expected_fallback_count=1,
    )

    assert report["promoted_rows"] == 2
    assert report["by_promotion_mode"] == {
        "candidate_adopted": 1,
        "original_fallback_empty_candidate": 1,
    }
    assert report["source_database_sha256_before"] == report["source_database_sha256_after"]
    assert report["output_pages_jsonl_rows"] == 2
    pages_jsonl = output_database.with_name("pages.jsonl")
    exported_pages = [
        json.loads(line)
        for line in pages_jsonl.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["text"] for row in exported_pages] == [
        "候选一",
        "原文二",
    ]
    source = sqlite3.connect(source_database)
    promoted = sqlite3.connect(output_database)
    try:
        assert [row[0] for row in source.execute("SELECT text FROM pages ORDER BY physical_page")] == [
            "原文一",
            "原文二",
        ]
        assert [row[0] for row in promoted.execute("SELECT text FROM pages ORDER BY physical_page")] == [
            "候选一",
            "原文二",
        ]
        assert promoted.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert promoted.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 2
        promoted_payloads = [
            json.loads(row[0])
            for row in promoted.execute(
                "SELECT payload_json FROM pages ORDER BY physical_page"
            )
        ]
        assert [payload["text"] for payload in promoted_payloads] == [
            "候选一",
            "原文二",
        ]
    finally:
        source.close()
        promoted.close()


def test_rejects_candidate_hash_mismatch_without_writing_output(tmp_path) -> None:
    source_database = tmp_path / "ancient_rag.db"
    source_sha, book_id = build_database(source_database)
    manifest, candidate_root = write_inputs(tmp_path, source_sha, book_id)
    candidate_file = next(candidate_root.rglob("page_000001.json"))
    item = json.loads(candidate_file.read_text(encoding="utf-8"))
    item["candidate_text"] = "被篡改"
    candidate_file.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    output_database = tmp_path / "ancient_rag_vnext.db"

    with pytest.raises(ValueError, match="candidate text SHA-256 mismatch"):
        promote_candidates(
            manifest,
            candidate_root,
            source_database,
            output_database,
            expected_count=2,
            expected_page_count=2,
            expected_adopted_count=1,
            expected_fallback_count=1,
        )
    assert not output_database.exists()
    assert not output_database.with_name("pages.jsonl").exists()


def test_rejects_unexpected_promotion_modes_without_writing_outputs(tmp_path) -> None:
    source_database = tmp_path / "ancient_rag.db"
    source_sha, book_id = build_database(source_database)
    manifest, candidate_root = write_inputs(tmp_path, source_sha, book_id)
    output_database = tmp_path / "ancient_rag_vnext.db"

    with pytest.raises(RuntimeError, match="expected 105 adopted candidates, found 1"):
        promote_candidates(
            manifest,
            candidate_root,
            source_database,
            output_database,
            expected_count=2,
            expected_page_count=2,
        )
    assert not output_database.exists()
    assert not output_database.with_name("pages.jsonl").exists()


def test_rejects_unexpected_page_count_without_writing_outputs(tmp_path) -> None:
    source_database = tmp_path / "ancient_rag.db"
    source_sha, book_id = build_database(source_database)
    manifest, candidate_root = write_inputs(tmp_path, source_sha, book_id)
    output_database = tmp_path / "ancient_rag_vnext.db"

    with pytest.raises(RuntimeError, match="expected 5624 database pages, found 2"):
        promote_candidates(
            manifest,
            candidate_root,
            source_database,
            output_database,
            expected_count=2,
        )
    assert not output_database.exists()
    assert not output_database.with_name("pages.jsonl").exists()
