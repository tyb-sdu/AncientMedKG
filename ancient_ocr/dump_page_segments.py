#!/usr/bin/env python
"""Print OCR segment geometry for targeted ancient pages."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from reorder_ancient_pages import extract_text_boxes, order_text_boxes


def main(database: Path, book_id: str, physical_page: int) -> int:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT page_id, book_id, physical_page, reading_direction, text, payload_json
        FROM pages
        WHERE book_id = ? AND physical_page = ?
        """,
        (book_id, physical_page),
    ).fetchone()
    connection.close()
    if not row:
        raise SystemExit(f"page not found: {book_id} p{physical_page}")
    records = extract_text_boxes(row["payload_json"])
    ordered_text, column_count, layout_status = order_text_boxes(
        records, str(row["reading_direction"] or "horizontal-ltr")
    )
    print(
        json.dumps(
            {
                "page_id": row["page_id"],
                "reading_direction": row["reading_direction"],
                "database_text": row["text"],
                "record_count": len(records),
                "column_count": column_count,
                "layout_status": layout_status,
                "ordered_text": ordered_text,
                "records": [
                    {
                        "text": item["text"],
                        "box": [round(value, 2) for value in item["box"]],
                        "score": item["score"],
                        "stored_order": item.get("stored_order"),
                        "orientation": item.get("orientation"),
                    }
                    for item in records
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump ancient OCR segment geometry")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--physical-page", type=int, required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.database.resolve(), args.book_id, args.physical_page))
