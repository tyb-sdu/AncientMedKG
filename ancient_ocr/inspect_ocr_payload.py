#!/usr/bin/env python
"""Inspect stored OCR payload shapes without changing the ancient database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from reorder_ancient_pages import extract_text_boxes, order_text_boxes


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = SCRIPT_DIR / "data" / "ancient_rag.db"


def payload_shape(value: Any) -> str:
    if isinstance(value, dict):
        return "dict:" + ",".join(sorted(value)[:20])
    if isinstance(value, list):
        return f"list:{len(value)}"
    return type(value).__name__


def inspect_database(database: Path, sample_limit: int) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    total_pages = connection.execute("SELECT COUNT(1) FROM pages").fetchone()[0]
    rows = connection.execute(
        """
        SELECT page_id, reading_direction, payload_json
        FROM pages
        WHERE payload_json IS NOT NULL AND LENGTH(payload_json) > 0
        ORDER BY page_id
        """
    )
    payload_pages = extractable_pages = 0
    shapes: Counter[str] = Counter()
    column_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    for row in rows:
        payload_pages += 1
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            shapes["invalid_json"] += 1
            continue
        shapes[payload_shape(payload)] += 1
        records = extract_text_boxes(payload)
        if not records:
            continue
        extractable_pages += 1
        _, columns, status = order_text_boxes(
            records, str(row["reading_direction"] or "horizontal-ltr")
        )
        column_counts[str(columns)] += 1
        if len(samples) < sample_limit:
            samples.append(
                {
                    "page_id": row["page_id"],
                    "reading_direction": row["reading_direction"],
                    "boxes": len(records),
                    "columns": columns,
                    "layout_status": status,
                    "first_texts": [record["text"] for record in records[:5]],
                }
            )
    connection.close()
    return {
        "database": str(database),
        "total_pages": total_pages,
        "payload_pages": payload_pages,
        "extractable_box_pages": extractable_pages,
        "payload_shapes": dict(shapes),
        "column_counts": dict(column_counts),
        "samples": samples,
        "source_data_modified": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect ancient OCR payload structure")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--sample-limit", type=int, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(
        json.dumps(
            inspect_database(args.database.resolve(), args.sample_limit),
            ensure_ascii=False,
            indent=2,
        )
    )
