#!/usr/bin/env python
"""Render a private, page-addressable OCR review packet without altering source data."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

import fitz


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output" / "review_packet_v1"
MANIFEST_FIELDS = (
    "review_order",
    "priority",
    "priority_score",
    "book_id",
    "filename",
    "physical_page",
    "pdf_page_label",
    "source_sha256",
    "image_path",
    "average_confidence",
    "visible_character_count",
    "cjk_character_ratio",
    "reading_direction",
    "review_reason",
    "ocr_preview",
    "review_status",
    "review_note",
    "corrected_text",
)


def candidate_rows(rows: list[dict[str, str]], priority: str, limit: int) -> list[dict[str, str]]:
    selected = [row for row in rows if row.get("priority") == priority]
    selected.sort(
        key=lambda row: (
            -int(row.get("priority_score") or 0),
            row.get("filename") or "",
            int(row.get("physical_page") or 0),
        )
    )
    return selected[:limit]


def source_paths(database: Path) -> dict[str, str]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT book_id, source_path FROM books").fetchall()
    finally:
        connection.close()
    return {str(book_id): str(source_path) for book_id, source_path in rows}


def page_image_name(order: int, row: dict[str, str]) -> str:
    stem = Path(row["filename"]).stem
    safe = "".join(character if character.isalnum() else "_" for character in stem)
    return f"{order:03d}_{safe}_p{int(row['physical_page']):06d}.png"


def render_page(source_pdf: Path, physical_page: int, destination: Path, dpi: int) -> None:
    document = fitz.open(source_pdf)
    try:
        page = document.load_page(physical_page - 1)
        zoom = dpi / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pixmap.save(destination)
    finally:
        document.close()


def write_instructions(output_dir: Path) -> None:
    (output_dir / "README.txt").write_text(
        "Review each rendered PDF page against its OCR preview.\n"
        "Use review_status: verified / corrected / unreadable / not_project_relevant.\n"
        "Keep corrected_text only when the page contains project-relevant evidence.\n"
        "Do not edit original PDFs, page JSON, SQLite databases, or vector indexes.\n"
        "A later sidecar correction stage will consume this manifest.\n",
        encoding="utf-8",
    )


def build_packet(
    data_dir: Path,
    output_dir: Path,
    priority: str,
    limit: int,
    dpi: int,
) -> dict[str, Any]:
    audit_path = data_dir / "low_confidence_audit_v1.csv"
    database = data_dir / "ancient_rag.db"
    with audit_path.open(encoding="utf-8-sig", newline="") as stream:
        audit_rows = list(csv.DictReader(stream))
    selected = candidate_rows(audit_rows, priority, limit)
    paths = source_paths(database)

    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, str]] = []
    for order, row in enumerate(selected, start=1):
        source_pdf = Path(paths[row["book_id"]])
        image_name = page_image_name(order, row)
        image_path = pages_dir / image_name
        render_page(source_pdf, int(row["physical_page"]), image_path, dpi)
        manifest_rows.append(
            {
                "review_order": str(order),
                "priority": row["priority"],
                "priority_score": row["priority_score"],
                "book_id": row["book_id"],
                "filename": row["filename"],
                "physical_page": row["physical_page"],
                "pdf_page_label": row["pdf_page_label"],
                "source_sha256": row["source_sha256"],
                "image_path": str(image_path.relative_to(output_dir)),
                "average_confidence": row["average_confidence"],
                "visible_character_count": row["visible_character_count"],
                "cjk_character_ratio": row["cjk_character_ratio"],
                "reading_direction": row["reading_direction"],
                "review_reason": row["review_reason"],
                "ocr_preview": row["text_preview"],
                "review_status": "unreviewed",
                "review_note": "",
                "corrected_text": "",
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "review_manifest.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(manifest_rows)
    write_instructions(output_dir)
    report = {
        "priority": priority,
        "requested_limit": limit,
        "rendered_pages": len(manifest_rows),
        "dpi": dpi,
        "manifest": str(manifest),
        "source_data_modified": False,
    }
    (output_dir / "packet_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ancient OCR page review packet")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--priority", choices=("P1", "P2"), default="P1")
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--dpi", type=int, default=180)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0 or args.dpi <= 0:
        raise ValueError("limit 和 dpi 必须为正整数")
    report = build_packet(
        args.data_dir.resolve(),
        args.output_dir.resolve(),
        args.priority,
        args.limit,
        args.dpi,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
