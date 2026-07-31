from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .ids import normalize_text, sha256_text
from .model import EvidenceRecord, GraphData, SourceRecord


def _open_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve().as_posix()
    return sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)


def _quote_match(quote: str, body: str) -> str:
    if not quote:
        return "no_quote"
    if quote in body:
        return "exact"
    compact_quote = normalize_text(quote)
    compact_body = normalize_text(body)
    if compact_quote and compact_quote in compact_body:
        return "normalized_whitespace"
    return "missing"


def _ancient_check(
    connection: sqlite3.Connection,
    source: SourceRecord,
    evidence: EvidenceRecord,
) -> dict[str, Any]:
    locator = evidence.locator
    page_id = str(locator.get("page_id", ""))
    physical_page = locator.get("physical_page")
    book_id = str(source.attributes.get("book_id", "") or locator.get("book_id", ""))
    if page_id:
        row = connection.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.text,
                   b.title, b.filename, b.source_sha256
            FROM pages AS p
            JOIN books AS b ON b.book_id = p.book_id
            WHERE p.page_id = ?
            """,
            (page_id,),
        ).fetchone()
    elif book_id and physical_page is not None:
        row = connection.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.text,
                   b.title, b.filename, b.source_sha256
            FROM pages AS p
            JOIN books AS b ON b.book_id = p.book_id
            WHERE p.book_id = ? AND p.physical_page = ?
            """,
            (book_id, int(physical_page)),
        ).fetchone()
    else:
        return {
            "status": "failed",
            "reason": "ancient evidence requires page_id or book_id+physical_page",
        }
    if row is None:
        return {"status": "failed", "reason": "ancient page not found"}
    (
        actual_page_id,
        actual_book_id,
        actual_physical_page,
        body,
        title,
        filename,
        source_sha256,
    ) = row
    failures: list[str] = []
    if source.file_sha256 and source.file_sha256 != source_sha256:
        failures.append("source_sha256_mismatch")
    if book_id and book_id != actual_book_id:
        failures.append("book_id_mismatch")
    if physical_page is not None and int(physical_page) != int(actual_physical_page):
        failures.append("physical_page_mismatch")
    expected_page_sha = str(locator.get("page_text_sha256", ""))
    actual_page_sha = sha256_text(str(body))
    if expected_page_sha and expected_page_sha != actual_page_sha:
        failures.append("page_text_sha256_mismatch")
    quote_match = _quote_match(evidence.quote, str(body))
    if quote_match == "missing":
        failures.append("quote_not_found")
    return {
        "status": "failed" if failures else "verified",
        "failures": failures,
        "page_id": actual_page_id,
        "book_id": actual_book_id,
        "physical_page": actual_physical_page,
        "page_text_sha256": actual_page_sha,
        "quote_match": quote_match,
        "title": title,
        "filename": filename,
        "source_sha256": source_sha256,
    }


def _modern_check(
    connection: sqlite3.Connection,
    source: SourceRecord,
    evidence: EvidenceRecord,
) -> dict[str, Any]:
    locator = evidence.locator
    chunk_id = str(locator.get("chunk_id", ""))
    doc_id = str(source.attributes.get("doc_id", "") or locator.get("doc_id", ""))
    pdf_page = locator.get("pdf_page")
    if chunk_id:
        row = connection.execute(
            """
            SELECT c.chunk_id, c.doc_id, c.pdf_page, c.text,
                   d.title, d.source_filename, d.sha256, d.doi
            FROM chunks AS c
            JOIN documents AS d ON d.doc_id = c.doc_id
            WHERE c.chunk_id = ?
            """,
            (chunk_id,),
        ).fetchone()
    elif doc_id and pdf_page is not None:
        row = connection.execute(
            """
            SELECT '', p.doc_id, p.pdf_page, p.text,
                   d.title, d.source_filename, d.sha256, d.doi
            FROM pages AS p
            JOIN documents AS d ON d.doc_id = p.doc_id
            WHERE p.doc_id = ? AND p.pdf_page = ?
            """,
            (doc_id, int(pdf_page)),
        ).fetchone()
    else:
        return {
            "status": "failed",
            "reason": "modern evidence requires chunk_id or doc_id+pdf_page",
        }
    if row is None:
        return {"status": "failed", "reason": "modern page or chunk not found"}
    (
        actual_chunk_id,
        actual_doc_id,
        actual_pdf_page,
        body,
        title,
        filename,
        source_sha256,
        doi,
    ) = row
    failures: list[str] = []
    if source.file_sha256 and source.file_sha256 != source_sha256:
        failures.append("source_sha256_mismatch")
    if source.doi and source.doi.casefold() != str(doi or "").casefold():
        failures.append("doi_mismatch")
    if doc_id and doc_id != actual_doc_id:
        failures.append("doc_id_mismatch")
    if pdf_page is not None and int(pdf_page) != int(actual_pdf_page):
        failures.append("pdf_page_mismatch")
    expected_text_sha = str(
        locator.get("chunk_text_sha256") or locator.get("page_text_sha256") or ""
    )
    actual_text_sha = sha256_text(str(body))
    if expected_text_sha and expected_text_sha != actual_text_sha:
        failures.append("text_sha256_mismatch")
    quote_match = _quote_match(evidence.quote, str(body))
    if quote_match == "missing":
        failures.append("quote_not_found")
    return {
        "status": "failed" if failures else "verified",
        "failures": failures,
        "chunk_id": actual_chunk_id,
        "doc_id": actual_doc_id,
        "pdf_page": actual_pdf_page,
        "text_sha256": actual_text_sha,
        "quote_match": quote_match,
        "title": title,
        "filename": filename,
        "source_sha256": source_sha256,
        "doi": doi,
    }


def verify_graph_sources(
    graph: GraphData,
    *,
    ancient_database: Path | None = None,
    modern_database: Path | None = None,
) -> dict[str, Any]:
    source_by_id = {value.source_id: value for value in graph.sources}
    ancient_connection = (
        _open_read_only(ancient_database) if ancient_database is not None else None
    )
    modern_connection = (
        _open_read_only(modern_database) if modern_database is not None else None
    )
    checks: list[dict[str, Any]] = []
    try:
        for evidence in graph.evidence:
            source = source_by_id.get(evidence.source_id)
            if source is None:
                result = {
                    "status": "failed",
                    "reason": "source_id missing from graph",
                }
            elif source.source_type == "ancient_pdf":
                if ancient_connection is None:
                    result = {
                        "status": "unverified",
                        "reason": "ancient database not supplied",
                    }
                else:
                    result = _ancient_check(ancient_connection, source, evidence)
            elif source.source_type == "modern_pdf":
                if modern_connection is None:
                    result = {
                        "status": "unverified",
                        "reason": "modern database not supplied",
                    }
                else:
                    result = _modern_check(modern_connection, source, evidence)
            else:
                result = {
                    "status": "not_applicable",
                    "reason": f"source type {source.source_type} has no SQLite resolver",
                }
            result = {
                "evidence_id": evidence.evidence_id,
                "source_id": evidence.source_id,
                "source_type": source.source_type if source else "",
                **result,
            }
            checks.append(result)
    finally:
        if ancient_connection is not None:
            ancient_connection.close()
        if modern_connection is not None:
            modern_connection.close()

    status_counts: dict[str, int] = {}
    for check in checks:
        status = str(check["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    failed = status_counts.get("failed", 0)
    unverified = status_counts.get("unverified", 0)
    return {
        "valid": failed == 0 and unverified == 0,
        "graph_version": graph.graph_version,
        "source_graph_content_fingerprint": graph.metadata.get(
            "build_content_fingerprint", ""
        ),
        "ancient_database": str(ancient_database or ""),
        "modern_database": str(modern_database or ""),
        "status_counts": status_counts,
        "checks": checks,
    }
