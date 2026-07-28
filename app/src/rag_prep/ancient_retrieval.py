from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .search import (
    CONCEPTS,
    _connect,
    _query_terms,
    _snippet,
    normalize_search_text,
)


def ancient_database_path(cfg: dict[str, Any]) -> Path:
    value = cfg.get("paths", {}).get("ancient_database")
    if not value:
        raise FileNotFoundError("未配置 ancient_database")
    return Path(value)


def ancient_is_available(cfg: dict[str, Any]) -> bool:
    try:
        return ancient_database_path(cfg).is_file()
    except FileNotFoundError:
        return False


def query_ancient_keyword(
    cfg: dict[str, Any],
    question: str,
    top_k: int,
) -> list[dict[str, Any]]:
    if not question.strip():
        raise ValueError("查询不能为空")
    db_path = ancient_database_path(cfg)
    if not db_path.exists():
        raise FileNotFoundError(f"古籍数据库不存在: {db_path}")
    with _connect(db_path, readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label, p.text,
                   p.reading_direction, p.average_confidence, p.low_confidence,
                   b.title, b.filename, b.source_sha256
            FROM pages p
            JOIN books b USING(book_id)
            """,
        ).fetchall()

    qnorm = normalize_search_text(question)
    terms = [normalize_search_text(term) for term in _query_terms(question)]
    aliases = [
        normalize_search_text(alias)
        for key, values in CONCEPTS.items()
        if key in question
        for alias in values
    ]
    results: list[dict[str, Any]] = []
    for row in rows:
        title_norm = normalize_search_text(row["title"])
        text_norm = normalize_search_text(row["text"])
        combined = f"{title_norm} {text_norm}"
        exact = 1 if qnorm and qnorm in f"{title_norm} {text_norm}" else 0
        alias_title = any(alias and alias in title_norm for alias in aliases)
        alias_text = any(alias and alias in text_norm for alias in aliases)
        lexical = sum(combined.count(term) for term in terms if term)
        if lexical == 0 and not exact and not alias_title and not alias_text:
            continue
        score = float(lexical) + exact * 4.0 + int(alias_title) * 15.0 + int(alias_text) * 4.0
        results.append(
            {
                "corpus": "ancient",
                "record_type": "page",
                "chunk_id": row["page_id"],
                "doc_id": row["book_id"],
                "title": row["title"],
                "year": "",
                "doi": "",
                "pdf_page": row["physical_page"],
                "page_label": row["pdf_page_label"],
                "source_filename": row["filename"],
                "sha256": row["source_sha256"],
                "snippet": _snippet(
                    row["text"],
                    question,
                    int(cfg.get("search", {}).get("snippet_chars", 360)),
                ),
                "keyword_score": round(score, 6),
                "vector_score": None,
                "keyword_rank": None,
                "vector_rank": None,
                "fusion_score": None,
                "fusion_rank": None,
                "reading_direction": row["reading_direction"],
                "average_confidence": row["average_confidence"],
                "low_confidence": int(row["low_confidence"]),
            }
        )
    results.sort(
        key=lambda item: (-float(item["keyword_score"]), item["doc_id"], item["pdf_page"])
    )
    results = results[:top_k]
    for rank, item in enumerate(results, 1):
        item["keyword_rank"] = rank
        item["fusion_rank"] = rank
    return results


def source_ancient_page(
    cfg: dict[str, Any],
    book_id: str,
    page: int,
) -> dict[str, Any] | None:
    db_path = ancient_database_path(cfg)
    with _connect(db_path, readonly=True) as conn:
        row = conn.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label, p.text,
                   p.reading_direction, p.average_confidence, p.low_confidence,
                   b.title, b.filename, b.source_sha256
            FROM pages p
            JOIN books b USING(book_id)
            WHERE p.book_id = ? AND p.physical_page = ?
            """,
            (book_id, page),
        ).fetchone()
    if not row:
        return None
    return {
        "corpus": "ancient",
        "record_type": "page",
        "chunk_id": row["page_id"],
        "doc_id": row["book_id"],
        "title": row["title"],
        "year": "",
        "doi": "",
        "pdf_page": row["physical_page"],
        "page_label": row["pdf_page_label"],
        "source_filename": row["filename"],
        "sha256": row["source_sha256"],
        "text": row["text"],
        "reading_direction": row["reading_direction"],
        "average_confidence": row["average_confidence"],
        "low_confidence": int(row["low_confidence"]),
    }


def ancient_doctor(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        db_path = ancient_database_path(cfg)
        if not db_path.is_file():
            return {"present": False, "healthy": False}
        with _connect(db_path, readonly=True) as conn:
            counts = {
                "books": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
                "pages": conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
                "fts_rows": conn.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0],
            }
            low_confidence_pages = conn.execute(
                "SELECT COUNT(*) FROM pages WHERE low_confidence = 1"
            ).fetchone()[0]
            quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        checks = {
            "present": True,
            "database": str(db_path),
            "counts": counts,
            "low_confidence_pages": low_confidence_pages,
            "sqlite_quick_check": quick_check,
        }
        checks["healthy"] = (
            counts["books"] > 0
            and counts["pages"] > 0
            and counts["pages"] == counts["fts_rows"]
            and quick_check == "ok"
        )
        return checks
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "healthy": False, "error": str(exc)}
