#!/usr/bin/env python
"""Create a review queue for low-confidence ancient OCR pages without changing OCR data."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
PRIMARY_DOMAIN_TERMS = ("汤火", "汤泼", "火疮", "火伤", "灼疮", "忍冬", "金银花", "甘草")
SECONDARY_DOMAIN_TERMS = ("痈疽", "生肌")


def priority(row: dict[str, Any]) -> tuple[int, str, str]:
    payload = json.loads(row["payload_json"])
    quality = payload.get("quality", {})
    visible = int(quality.get("visible_character_count") or 0)
    cjk_ratio = float(quality.get("cjk_character_ratio") or 0.0)
    confidence = float(row.get("average_confidence") or 0.0)
    text = str(row.get("text") or "")
    score = 0
    reasons: list[str] = []
    if visible >= 200:
        score += 25
        reasons.append("正文较长")
    elif visible >= 80:
        score += 15
        reasons.append("存在可审阅正文")
    if confidence < 0.55:
        score += 35
        reasons.append("平均置信度低于0.55")
    elif confidence < 0.70:
        score += 22
        reasons.append("平均置信度低于0.70")
    if cjk_ratio < 0.35:
        score += 20
        reasons.append("汉字比例异常")
    elif cjk_ratio < 0.45:
        score += 10
        reasons.append("汉字比例偏低")
    primary_terms = [term for term in PRIMARY_DOMAIN_TERMS if term in text]
    secondary_terms = [term for term in SECONDARY_DOMAIN_TERMS if term in text]
    if primary_terms:
        score += 30
        reasons.append("命中核心项目词:" + "/".join(primary_terms))
    elif secondary_terms:
        score += 5
        reasons.append("命中次级外科词:" + "/".join(secondary_terms))
    if "目录" in text or text.count("…") >= 4:
        score -= 30
        reasons.append("目录页降权")
    level = "P0" if score >= 60 else "P1" if score >= 35 else "P2"
    return score, level, "；".join(reasons) or "低置信度标记"


def build_audit(database: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT p.book_id, p.physical_page, p.pdf_page_label, p.text,
                   p.reading_direction, p.average_confidence, p.payload_json,
                   b.filename, b.source_sha256
            FROM pages p JOIN books b USING(book_id)
            WHERE p.low_confidence = 1
            """
        ).fetchall()
    finally:
        connection.close()
    audit: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        score, level, reason = priority(row)
        payload = json.loads(row["payload_json"])
        quality = payload.get("quality", {})
        preview = " ".join(str(row["text"]).split())[:180]
        audit.append(
            {
                "priority": level,
                "priority_score": score,
                "book_id": row["book_id"],
                "filename": row["filename"],
                "physical_page": row["physical_page"],
                "pdf_page_label": row["pdf_page_label"],
                "average_confidence": row["average_confidence"],
                "visible_character_count": quality.get("visible_character_count"),
                "cjk_character_ratio": quality.get("cjk_character_ratio"),
                "ink_ratio": quality.get("ink_ratio"),
                "reading_direction": row["reading_direction"],
                "source_sha256": row["source_sha256"],
                "review_reason": reason,
                "text_preview": preview,
            }
        )
    audit.sort(key=lambda item: (-item["priority_score"], item["filename"], item["physical_page"]))
    summary = {
        "low_confidence_pages": len(audit),
        "by_priority": dict(Counter(item["priority"] for item in audit)),
        "by_book": dict(Counter(item["filename"] for item in audit)),
        "database": str(database),
        "database_modified": False,
    }
    return audit, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ancient OCR low-confidence audit queue")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    output = args.output or data_dir / "low_confidence_audit_v1.csv"
    summary_path = args.summary or data_dir / "low_confidence_audit_v1_summary.json"
    audit, summary = build_audit(data_dir / "ancient_rag.db")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(audit[0]) if audit else [])
        if audit:
            writer.writeheader()
            writer.writerows(audit)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
