#!/usr/bin/env python
"""Validate human OCR review rows and export a non-destructive correction sidecar."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "output" / "review_packet_v1" / "review_manifest.csv"
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
VALID_STATUSES = {"verified", "corrected", "unreadable", "not_project_relevant"}


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reviewed_action(row: dict[str, str], page: dict[str, Any]) -> dict[str, Any]:
    status = (row.get("review_status") or "").strip()
    if status not in VALID_STATUSES:
        raise ValueError(f"{row.get('filename')} p{row.get('physical_page')}: 无效审核状态 {status!r}")
    if row.get("source_sha256") != page["source_sha256"]:
        raise ValueError(f"{row.get('filename')} p{row.get('physical_page')}: 源文件 SHA-256 不一致")
    corrected = (row.get("corrected_text") or "").strip()
    if status == "corrected" and not corrected:
        raise ValueError(f"{row.get('filename')} p{row.get('physical_page')}: corrected 必须填写文本")
    if status != "corrected" and corrected:
        raise ValueError(f"{row.get('filename')} p{row.get('physical_page')}: 非 corrected 行不应填写 corrected_text")
    return {
        "page_id": page["page_id"],
        "book_id": page["book_id"],
        "physical_page": page["physical_page"],
        "pdf_page_label": page["pdf_page_label"],
        "source_filename": page["filename"],
        "source_sha256": page["source_sha256"],
        "original_text_sha256": text_sha256(page["text"]),
        "review_status": status,
        "review_note": row.get("review_note") or "",
        "corrected_text": corrected if status == "corrected" else None,
        "corrected_text_sha256": text_sha256(corrected) if corrected else None,
    }


def export_overrides(manifest: Path, database: Path, output: Path) -> dict[str, Any]:
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        pages = {
            (row["book_id"], int(row["physical_page"])): dict(row)
            for row in connection.execute(
                """
                SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label,
                       p.text, b.filename, b.source_sha256
                FROM pages p JOIN books b USING(book_id)
                """
            )
        }
    finally:
        connection.close()
    actions: list[dict[str, Any]] = []
    unreviewed = 0
    for row in rows:
        status = (row.get("review_status") or "").strip()
        if status == "unreviewed":
            unreviewed += 1
            continue
        key = (row.get("book_id") or "", int(row.get("physical_page") or 0))
        page = pages.get(key)
        if not page:
            raise ValueError(f"复核页不在当前数据库: {key}")
        actions.append(reviewed_action(row, page))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for action in actions:
            stream.write(json.dumps(action, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    temporary.replace(output)
    report = {
        "manifest": str(manifest),
        "database": str(database),
        "output": str(output),
        "reviewed_rows": len(actions),
        "unreviewed_rows": unreviewed,
        "by_status": dict(Counter(action["review_status"] for action in actions)),
        "source_data_modified": False,
    }
    report_path = output.with_name(output.stem + "_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export reviewed ancient OCR corrections")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATA_DIR / "ancient_rag.db")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output or args.manifest.parent / "review_overrides_v1.jsonl"
    report = export_overrides(args.manifest.resolve(), args.database.resolve(), output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
