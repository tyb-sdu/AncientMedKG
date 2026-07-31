#!/usr/bin/env python
"""Promote a complete PaddleOCR-VL manifest into a versioned ancient OCR database."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from verify_candidate_manifest import verify_manifest


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    temporary.replace(path)


def page_payloads(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT page_id, book_id, physical_page, pdf_page_label, text,
               reading_direction, average_confidence, low_confidence, payload_json
        FROM pages
        ORDER BY book_id, physical_page
        """
    ).fetchall()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        if not isinstance(payload, dict):
            payload = {"original_payload": payload}
        payload.update(
            {
                "book_id": row["book_id"],
                "physical_page": row["physical_page"],
                "pdf_page_label": row["pdf_page_label"],
                "text": row["text"],
                "reading_direction": row["reading_direction"],
            }
        )
        payloads.append(payload)
    return payloads


def safe_candidate_path(candidate_root: Path, relative_path: str) -> Path:
    root = candidate_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"candidate path escapes candidate root: {relative_path}") from error
    return candidate


def load_manifest(path: Path, expected_count: int | None) -> list[dict[str, str]]:
    validation = verify_manifest(path)
    if not validation["valid"]:
        raise ValueError("candidate manifest validation failed: " + "; ".join(validation["issues"]))
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f"expected {expected_count} candidate rows, found {len(rows)}")
    return rows


def candidate_record(
    row: dict[str, str],
    page: dict[str, Any],
    candidate_root: Path,
) -> tuple[str, dict[str, Any]]:
    if row["source_sha256"] != page["source_sha256"]:
        raise ValueError(f"{page['page_id']}: source SHA-256 mismatch")
    original_text = str(page["text"] or "")
    if row["original_text_sha256"] != text_sha256(original_text):
        raise ValueError(f"{page['page_id']}: original text SHA-256 mismatch")

    candidate_path = safe_candidate_path(candidate_root, row["candidate_path"])
    if not candidate_path.is_file():
        raise FileNotFoundError(f"candidate JSON missing: {candidate_path}")
    item = json.loads(candidate_path.read_text(encoding="utf-8"))
    for field, expected in (
        ("page_id", page["page_id"]),
        ("book_id", page["book_id"]),
        ("physical_page", page["physical_page"]),
        ("source_sha256", page["source_sha256"]),
        ("original_text_sha256", row["original_text_sha256"]),
        ("candidate_text_sha256", row["candidate_text_sha256"]),
    ):
        if item.get(field) != expected:
            raise ValueError(f"{page['page_id']}: candidate {field} mismatch")

    candidate_text = str(item.get("candidate_text") or "")
    if text_sha256(candidate_text) != row["candidate_text_sha256"]:
        raise ValueError(f"{page['page_id']}: candidate text SHA-256 mismatch")
    flags = [flag for flag in str(row.get("review_flags") or "").split("|") if flag]
    if candidate_text.strip():
        effective_text = candidate_text
        promotion_mode = "candidate_adopted"
    else:
        if "empty_candidate" not in flags:
            raise ValueError(f"{page['page_id']}: empty candidate lacks empty_candidate flag")
        effective_text = original_text
        promotion_mode = "original_fallback_empty_candidate"

    audit = {
        "schema_version": 1,
        "page_id": page["page_id"],
        "book_id": page["book_id"],
        "physical_page": page["physical_page"],
        "pdf_page_label": page["pdf_page_label"],
        "source_filename": page["filename"],
        "source_sha256": page["source_sha256"],
        "original_text_sha256": row["original_text_sha256"],
        "candidate_text_sha256": row["candidate_text_sha256"],
        "effective_text_sha256": text_sha256(effective_text),
        "promotion_mode": promotion_mode,
        "review_flags": flags,
        "recommendation": row.get("recommendation") or "",
        "render_backend": row.get("render_backend") or "",
        "candidate_path": row["candidate_path"],
        "image_path": row["image_path"],
    }
    return effective_text, audit


def promote_candidates(
    manifest: Path,
    candidate_root: Path,
    database: Path,
    output_database: Path,
    *,
    output_pages_jsonl: Path | None = None,
    expected_count: int | None = 113,
    expected_page_count: int | None = 5624,
    expected_adopted_count: int | None = 105,
    expected_fallback_count: int | None = 8,
    force: bool = False,
) -> dict[str, Any]:
    manifest = manifest.resolve()
    candidate_root = candidate_root.resolve()
    database = database.resolve()
    output_database = output_database.resolve()
    output_pages_jsonl = (
        output_pages_jsonl.resolve()
        if output_pages_jsonl is not None
        else output_database.with_name("pages.jsonl")
    )
    if database == output_database:
        raise ValueError("output database must differ from the source database")
    if output_pages_jsonl == output_database:
        raise ValueError("output pages JSONL must differ from the output database")
    if output_database.exists() and not force:
        raise FileExistsError(f"output database already exists: {output_database}")
    if output_pages_jsonl.exists() and not force:
        raise FileExistsError(f"output pages JSONL already exists: {output_pages_jsonl}")
    rows = load_manifest(manifest, expected_count)
    source_database_sha256_before = file_sha256(database)

    output_database.parent.mkdir(parents=True, exist_ok=True)
    output_pages_jsonl.parent.mkdir(parents=True, exist_ok=True)
    temporary_database = output_database.with_suffix(output_database.suffix + ".tmp")
    temporary_pages_jsonl = output_pages_jsonl.with_suffix(
        output_pages_jsonl.suffix + ".tmp"
    )
    temporary_database.unlink(missing_ok=True)
    temporary_pages_jsonl.unlink(missing_ok=True)
    source_connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    backup_connection = sqlite3.connect(temporary_database)
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()

    connection = sqlite3.connect(temporary_database)
    connection.row_factory = sqlite3.Row
    audit_rows: list[dict[str, Any]] = []
    try:
        page_rows = {
            (row["book_id"], int(row["physical_page"])): dict(row)
            for row in connection.execute(
                """
                SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label,
                       p.text, p.payload_json, b.title, b.filename, b.source_sha256
                FROM pages p JOIN books b USING(book_id)
                """
            )
        }
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            key = (row["book_id"], int(row["physical_page"]))
            page = page_rows.get(key)
            if page is None:
                raise ValueError(f"candidate page is absent from database: {key}")
            effective_text, audit = candidate_record(row, page, candidate_root)

            payload = json.loads(page["payload_json"] or "{}")
            if not isinstance(payload, dict):
                payload = {"original_payload": payload}
            payload["text"] = effective_text
            payload["vl_promotion_v1"] = {
                **audit,
                "original_text": page["text"],
            }
            connection.execute(
                "UPDATE pages SET text = ?, payload_json = ? WHERE page_id = ?",
                (
                    effective_text,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    page["page_id"],
                ),
            )
            connection.execute("DELETE FROM pages_fts WHERE page_id = ?", (page["page_id"],))
            connection.execute(
                "INSERT INTO pages_fts VALUES (?, ?, ?, ?)",
                (page["page_id"], page["book_id"], page["title"], effective_text),
            )
            audit_rows.append(audit)
        connection.commit()

        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        page_count = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        fts_count = connection.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"promoted database quick_check failed: {quick_check}")
        if page_count != fts_count:
            raise RuntimeError(f"page/FTS row count mismatch: {page_count} != {fts_count}")
        if expected_page_count is not None and page_count != expected_page_count:
            raise RuntimeError(
                f"expected {expected_page_count} database pages, found {page_count}"
            )
        modes = Counter(row["promotion_mode"] for row in audit_rows)
        adopted_count = modes["candidate_adopted"]
        fallback_count = modes["original_fallback_empty_candidate"]
        if (
            expected_adopted_count is not None
            and adopted_count != expected_adopted_count
        ):
            raise RuntimeError(
                f"expected {expected_adopted_count} adopted candidates, "
                f"found {adopted_count}"
            )
        if (
            expected_fallback_count is not None
            and fallback_count != expected_fallback_count
        ):
            raise RuntimeError(
                f"expected {expected_fallback_count} empty-candidate fallbacks, "
                f"found {fallback_count}"
            )
        for audit in audit_rows:
            page_row = connection.execute(
                "SELECT text, payload_json FROM pages WHERE page_id = ?",
                (audit["page_id"],),
            ).fetchone()
            page_text = page_row["text"]
            fts_text = connection.execute(
                "SELECT text FROM pages_fts WHERE page_id = ?", (audit["page_id"],)
            ).fetchone()[0]
            if page_text != fts_text or text_sha256(page_text) != audit["effective_text_sha256"]:
                raise RuntimeError(f"{audit['page_id']}: promoted page/FTS verification failed")
            payload = json.loads(page_row["payload_json"])
            if not isinstance(payload, dict) or payload.get("text") != page_text:
                raise RuntimeError(f"{audit['page_id']}: payload text verification failed")

        atomic_jsonl(temporary_pages_jsonl, page_payloads(connection))
    except Exception:
        connection.rollback()
        connection.close()
        temporary_database.unlink(missing_ok=True)
        temporary_pages_jsonl.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    source_database_sha256_after = file_sha256(database)
    if source_database_sha256_after != source_database_sha256_before:
        temporary_database.unlink(missing_ok=True)
        temporary_pages_jsonl.unlink(missing_ok=True)
        raise RuntimeError("source database changed while creating the vNext version")

    temporary_database.replace(output_database)
    temporary_pages_jsonl.replace(output_pages_jsonl)
    promotion_log = output_database.with_name(output_database.stem + "_promotion.jsonl")
    atomic_jsonl(promotion_log, audit_rows)
    flags = Counter(flag for row in audit_rows for flag in row["review_flags"])
    report = {
        "schema_version": 1,
        "manifest": str(manifest),
        "candidate_root": str(candidate_root),
        "source_database": str(database),
        "source_database_sha256": source_database_sha256_after,
        "source_database_sha256_before": source_database_sha256_before,
        "source_database_sha256_after": source_database_sha256_after,
        "output_database": str(output_database),
        "output_database_sha256": file_sha256(output_database),
        "output_pages_jsonl": str(output_pages_jsonl),
        "output_pages_jsonl_sha256": file_sha256(output_pages_jsonl),
        "output_pages_jsonl_rows": page_count,
        "promotion_log": str(promotion_log),
        "manifest_rows": len(rows),
        "promoted_rows": len(audit_rows),
        "by_promotion_mode": dict(modes),
        "by_review_flag": dict(flags),
        "page_count": page_count,
        "fts_count": fts_count,
        "sqlite_quick_check": quick_check,
        "source_database_modified": False,
        "original_pdf_modified": False,
        "rollback_database": str(database),
    }
    report_path = output_database.with_name(output_database.stem + "_promotion_report.json")
    atomic_json(report_path, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote PaddleOCR-VL candidates to a vNext database")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-database", type=Path, required=True)
    parser.add_argument(
        "--output-pages-jsonl",
        type=Path,
        help="Defaults to pages.jsonl beside --output-database",
    )
    parser.add_argument("--expected-count", type=int, default=113)
    parser.add_argument("--expected-page-count", type=int, default=5624)
    parser.add_argument("--expected-adopted-count", type=int, default=105)
    parser.add_argument("--expected-fallback-count", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = promote_candidates(
        args.manifest,
        args.candidate_root,
        args.database,
        args.output_database,
        output_pages_jsonl=args.output_pages_jsonl,
        expected_count=args.expected_count,
        expected_page_count=args.expected_page_count,
        expected_adopted_count=args.expected_adopted_count,
        expected_fallback_count=args.expected_fallback_count,
        force=args.force,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
