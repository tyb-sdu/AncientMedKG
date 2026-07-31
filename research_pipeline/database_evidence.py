from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_loci(evidence: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for instance in evidence.get("formula_instances", []):
        yield instance["formula_instance_id"], instance["source_locus"]
    for index, locus in enumerate(evidence.get("context_loci", []), start=1):
        yield f"context-{index}", locus


def _snippet(text: str, terms: list[str], radius: int = 100) -> str:
    positions = [text.find(term) for term in terms if text.find(term) >= 0]
    start = max((min(positions) if positions else 0) - radius, 0)
    end = min(start + radius * 2 + 120, len(text))
    return " ".join(text[start:end].split())


def verify_database_evidence(
    database: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        for locus_id, locus in _iter_loci(evidence):
            row = connection.execute(
                """
                SELECT books.title, pages.page_id, pages.physical_page, pages.text
                FROM pages
                JOIN books USING (book_id)
                WHERE pages.book_id = ? AND pages.physical_page = ?
                """,
                (locus["book_id"], locus["physical_page"]),
            ).fetchone()
            if row is None:
                issues.append({"locus_id": locus_id, "reason": "page_missing"})
                continue
            text = str(row["text"] or "")
            missing_terms = [
                term for term in locus["evidence_terms"] if term not in text
            ]
            title_matches = row["title"] == locus["expected_title"]
            if not title_matches:
                issues.append(
                    {
                        "locus_id": locus_id,
                        "reason": "title_mismatch",
                        "expected": locus["expected_title"],
                        "actual": row["title"],
                    }
                )
            if missing_terms:
                issues.append(
                    {
                        "locus_id": locus_id,
                        "reason": "evidence_terms_missing",
                        "missing_terms": missing_terms,
                    }
                )
            records.append(
                {
                    "locus_id": locus_id,
                    "book_id": locus["book_id"],
                    "title": row["title"],
                    "physical_page": row["physical_page"],
                    "page_id": row["page_id"],
                    "page_text_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    "title_matches": title_matches,
                    "matched_terms": [
                        term for term in locus["evidence_terms"] if term in text
                    ],
                    "missing_terms": missing_terms,
                    "snippet": _snippet(text, locus["evidence_terms"]),
                }
            )
    finally:
        connection.close()

    if quick_check != "ok":
        issues.append({"reason": "sqlite_quick_check_failed", "value": quick_check})
    return {
        "valid": not issues,
        "issues": issues,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "database": str(database),
        "database_sha256": sha256_file(database),
        "sqlite_quick_check": quick_check,
        "locus_count": len(records),
        "loci": records,
    }
