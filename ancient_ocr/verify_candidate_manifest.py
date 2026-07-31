#!/usr/bin/env python
"""Verify the traceability fields of a PaddleOCR-VL candidate manifest."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_FIELDS = (
    "book_id",
    "physical_page",
    "source_sha256",
    "original_text_sha256",
    "candidate_text_sha256",
    "candidate_path",
    "image_path",
)
EMPTY_TEXT_SHA256 = hashlib.sha256(b"").hexdigest()


def expected_paths(book_id: str, physical_page: int) -> tuple[str, str]:
    book_path = book_id.replace(":", "_")
    page_name = f"page_{physical_page:06d}"
    return (
        f"candidates/{book_path}/{page_name}.json",
        f"rendered/{book_path}/{page_name}.png",
    )


def validate_row(row: dict[str, str], row_number: int) -> list[str]:
    issues: list[str] = []
    missing = [field for field in REQUIRED_FIELDS if not str(row.get(field) or "").strip()]
    if missing:
        return [f"row {row_number}: missing required fields: {', '.join(missing)}"]

    book_id = row["book_id"]
    for field in ("source_sha256", "original_text_sha256", "candidate_text_sha256"):
        if not SHA256_RE.fullmatch(row[field]):
            issues.append(f"row {row_number}: invalid {field}")

    try:
        physical_page = int(row["physical_page"])
    except ValueError:
        return issues + [f"row {row_number}: physical_page is not an integer"]
    if physical_page < 1:
        issues.append(f"row {row_number}: physical_page must be positive")

    expected_book_id = f"ancient:{row['source_sha256'][:20]}"
    if book_id != expected_book_id:
        issues.append(f"row {row_number}: book_id does not match source_sha256 prefix")

    candidate_path, image_path = expected_paths(book_id, physical_page)
    if row["candidate_path"] != candidate_path:
        issues.append(f"row {row_number}: candidate_path does not match book_id and physical_page")
    if row["image_path"] != image_path:
        issues.append(f"row {row_number}: image_path does not match book_id and physical_page")
    return issues


def verify_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = set(reader.fieldnames or [])
        rows = list(reader)

    issues: list[str] = []
    missing_headers = [field for field in REQUIRED_FIELDS if field not in fieldnames]
    if missing_headers:
        issues.append("missing required columns: " + ", ".join(missing_headers))
    if not rows:
        issues.append("manifest contains no candidate rows")
    page_keys: Counter[tuple[str, str]] = Counter()
    source_hashes: dict[str, set[str]] = defaultdict(set)
    candidate_hash_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row_number, row in enumerate(rows, start=2):
        issues.extend(validate_row(row, row_number))
        page_keys[(row.get("book_id", ""), row.get("physical_page", ""))] += 1
        source_hashes[row.get("book_id", "")].add(row.get("source_sha256", ""))
        candidate_hash_rows[row.get("candidate_text_sha256", "")].append(row)

    duplicate_page_keys = [key for key, count in page_keys.items() if count > 1]
    issues.extend(
        f"duplicate book_id and physical_page: {book_id} page {physical_page}"
        for book_id, physical_page in duplicate_page_keys
    )
    for book_id, hashes in source_hashes.items():
        if book_id and len(hashes) > 1:
            issues.append(f"book_id maps to multiple source_sha256 values: {book_id}")

    duplicate_candidate_groups = []
    for candidate_hash, candidate_rows in candidate_hash_rows.items():
        if len(candidate_rows) < 2:
            continue
        all_empty_flagged = all(
            "empty_candidate" in str(row.get("review_flags") or "")
            for row in candidate_rows
        )
        duplicate_candidate_groups.append(
            {
                "candidate_text_sha256": candidate_hash,
                "rows": len(candidate_rows),
                "all_empty_flagged": all_empty_flagged,
            }
        )
        if candidate_hash == EMPTY_TEXT_SHA256 and not all_empty_flagged:
            issues.append("empty candidate text hash found without empty_candidate review flag")

    books = [
        {
            "book_id": book_id,
            "pages": sum(1 for row in rows if row.get("book_id") == book_id),
            "distinct_source_sha256": len(hashes),
        }
        for book_id, hashes in sorted(source_hashes.items())
        if book_id
    ]
    return {
        "manifest": str(path),
        "rows": len(rows),
        "valid": not issues,
        "issues": issues,
        "books": books,
        "duplicate_candidate_text_hash_groups": duplicate_candidate_groups,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a PaddleOCR-VL candidate manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = verify_manifest(args.manifest)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
