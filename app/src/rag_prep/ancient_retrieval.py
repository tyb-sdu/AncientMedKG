from __future__ import annotations

import hashlib
import json
import re
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

_LAYOUT_CACHE: dict[str, dict[str, dict[str, Any]]] = {}

_LOCATOR_QUERY_RE = re.compile(
    r"\u300a([^\u300b]+)\u300b.*?\u201c([^\u201d]+)\u201d"
)
_ANCIENT_TERM_VARIANTS = {
    "\u521b": ("\u521b", "\u5275"),
    "\u5916\u6577": ("\u5916\u6577", "\u5857\u6577"),
    "\u5927\u9ec4": ("\u5927\u9ec4", "\u5927\u9ec3"),
    "\u6d88\u80bf": ("\u6d88\u80bf", "\u6d88\u816b"),
    "\u6e83": ("\u6e83", "\u6f70"),
    "\u706b\u70e7": ("\u706b\u70e7", "\u706b\u71d2"),
    "\u706b\u75ae": ("\u706b\u75ae", "\u706b\u7621"),
    "\u707c\u4f24": ("\u707c\u4f24", "\u707c\u50b7", "\u707c\u7621"),
    "\u70eb\u4f24": ("\u70eb\u4f24", "\u6e6f\u706b", "\u6e6f\u6ce1"),
    "\u70ed\u6bd2": ("\u70ed\u6bd2", "\u71b1\u6bd2"),
    "\u8840\u7600": ("\u8840\u7600", "\u7600\u8840"),
    "\u91d1\u94f6\u82b1": ("\u91d1\u94f6\u82b1", "\u91d1\u9280\u82b1"),
}
_OUT_OF_SCOPE_MARKERS = (
    "crispr",
    "pd-1",
    "3d\u6253\u5370",
    "\u968f\u673a\u53cc\u76f2",
    "mrna",
    "\u7eb3\u7c73\u673a\u5668\u4eba",
    "rna\u6d4b\u5e8f",
    "\u5355\u7ec6\u80de\u8f6c\u5f55\u7ec4",
    "faiss",
    "ct\u4e09\u7ef4",
    "elisa",
    "western blot",
    "\u52a8\u7269\u4f26\u7406\u5ba1\u6279\u53f7",
    "car-t",
    "\u8010\u836f\u57fa\u56e0\u6d4b\u5e8f",
    "\u6fc0\u5149\u5171\u805a\u7126",
    "\u591a\u4e2d\u5fc3\u524d\u77bb\u6027\u961f\u5217",
    "\u7eb3\u7c73\u9176\u50ac\u5316\u52a8\u529b\u5b66",
    "\u751f\u7269\u4fe1\u606f\u5b66\u5bcc\u96c6\u5206\u6790",
    "\u673a\u5668\u4eba\u81ea\u52a8\u6362\u836f",
)


def ancient_query_is_out_of_scope(question: str) -> bool:
    normalized = normalize_search_text(question)
    return any(marker in normalized for marker in _OUT_OF_SCOPE_MARKERS)


def ancient_locator_hints(question: str) -> tuple[str, tuple[str, ...]] | None:
    match = _LOCATOR_QUERY_RE.search(question)
    if match is None:
        return None
    title = normalize_search_text(match.group(1))
    focus = normalize_search_text(match.group(2))
    variants = _ANCIENT_TERM_VARIANTS.get(focus, (focus,))
    return title, tuple(normalize_search_text(value) for value in variants)


def ancient_database_path(cfg: dict[str, Any]) -> Path:
    value = cfg.get("paths", {}).get("ancient_database")
    if not value:
        raise FileNotFoundError("未配置 ancient_database")
    return Path(value)


def ancient_layout_sidecar_path(cfg: dict[str, Any]) -> Path | None:
    value = cfg.get("paths", {}).get("ancient_layout_sidecar")
    return Path(value) if value else None


def _layout_rows(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    path = ancient_layout_sidecar_path(cfg)
    if path is None or not path.is_file():
        return {}
    key = str(path.resolve())
    if key not in _LAYOUT_CACHE:
        rows: dict[str, dict[str, Any]] = {}
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                page_id = str(item.get("page_id") or "")
                if page_id:
                    rows[page_id] = item
        _LAYOUT_CACHE[key] = rows
    return _LAYOUT_CACHE[key]


def ancient_text_for_row(cfg: dict[str, Any], row: Any) -> tuple[str, str]:
    raw_text = str(row["text"] or "")
    item = _layout_rows(cfg).get(str(row["page_id"]))
    if not item or not item.get("ordered_text"):
        return raw_text, "database"
    expected = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    if item.get("original_text_sha256") != expected:
        return raw_text, "database_hash_mismatch"
    return str(item["ordered_text"]), str(item.get("layout_status") or "ordered")


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
    if ancient_query_is_out_of_scope(question):
        return []
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
    locator_hints = ancient_locator_hints(question)
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
        text, layout_status = ancient_text_for_row(cfg, row)
        text_norm = normalize_search_text(text)
        combined = f"{title_norm} {text_norm}"
        if locator_hints is not None:
            source_title, focus_variants = locator_hints
            source_match = source_title in title_norm or title_norm in source_title
            focus_hits = sum(text_norm.count(value) for value in focus_variants if value)
            if not source_match or focus_hits == 0:
                continue
            score = 100.0 + float(focus_hits)
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
                        text,
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
                    "layout_status": layout_status,
                    "retrieval_planner": "source_anchored_locator",
                    "locator_focus_variants": list(focus_variants),
                }
            )
            continue
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
                    text,
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
                "layout_status": layout_status,
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
    text, layout_status = ancient_text_for_row(cfg, row)
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
        "text": text,
        "reading_direction": row["reading_direction"],
        "average_confidence": row["average_confidence"],
        "low_confidence": int(row["low_confidence"]),
        "layout_status": layout_status,
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
            db_page_ids = {
                str(page_id)
                for (page_id,) in conn.execute("SELECT page_id FROM pages").fetchall()
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
        layout_path = ancient_layout_sidecar_path(cfg)
        if layout_path and layout_path.is_file():
            layout_rows = _layout_rows(cfg)
            sidecar_page_ids = set(layout_rows)
            checks["layout_sidecar"] = {
                "present": True,
                "path": str(layout_path),
                "rows": len(layout_rows),
                "missing_db_page_ids": len(db_page_ids - sidecar_page_ids),
                "orphan_page_ids": len(sidecar_page_ids - db_page_ids),
                "healthy": sidecar_page_ids == db_page_ids,
            }
        else:
            checks["layout_sidecar"] = {
                "present": False,
                "path": str(layout_path) if layout_path else None,
                "healthy": True,
            }
        checks["healthy"] = (
            counts["books"] > 0
            and counts["pages"] > 0
            and counts["pages"] == counts["fts_rows"]
            and quick_check == "ok"
            and checks["layout_sidecar"]["healthy"]
        )
        return checks
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "healthy": False, "error": str(exc)}
