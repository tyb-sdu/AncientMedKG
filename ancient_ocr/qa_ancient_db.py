from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def main() -> int:
    data_dir = Path("data")
    books_path = data_dir / "books.jsonl"
    db_path = data_dir / "ancient_rag.db"

    books = [
        json.loads(line)
        for line in books_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    connection = sqlite3.connect(db_path)
    try:
        print("BOOK_STATS")
        for book in books:
            low_conf = connection.execute(
                "SELECT COUNT(*) FROM pages WHERE book_id = ? AND low_confidence = 1",
                (book["book_id"],),
            ).fetchone()[0]
            page_count = connection.execute(
                "SELECT COUNT(*) FROM pages WHERE book_id = ?",
                (book["book_id"],),
            ).fetchone()[0]
            print(
                json.dumps(
                    {
                        "filename": book["filename"],
                        "mode": book["processing_mode"],
                        "pages": page_count,
                        "low_conf": low_conf,
                        "book_id": book["book_id"],
                    },
                    ensure_ascii=False,
                )
            )

        print("LOW_SAMPLES")
        query = """
            SELECT b.filename, p.book_id, p.physical_page, p.pdf_page_label,
                   substr(p.text, 1, 120)
            FROM pages p
            JOIN books b USING(book_id)
            WHERE p.low_confidence = 1
            ORDER BY b.filename, p.physical_page
            LIMIT 12
        """
        for row in connection.execute(query):
            print(
                json.dumps(
                    {
                        "filename": row[0],
                        "book_id": row[1],
                        "page": row[2],
                        "label": row[3],
                        "text": row[4],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
