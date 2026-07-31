#!/usr/bin/env python
"""Audit residual layout and OCR uncertainty without changing source data."""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sqlite3
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from reorder_ancient_pages import extract_text_boxes


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_OUTPUT = DEFAULT_DATA_DIR / "layout_quality_audit_v1.csv"
NOISE_RE = re.compile(r"[A-Za-z�]")
FIELDS = (
    "priority",
    "risk_score",
    "book_id",
    "physical_page",
    "pdf_page_label",
    "reading_direction",
    "record_count",
    "average_confidence",
    "vertical_ratio",
    "order_similarity_to_payload",
    "layout_reason",
    "ocr_reason",
    "text_preview",
)


def _sidecar(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    if not path.is_file():
        return result
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            result[str(item["page_id"])] = item
    return result


def _centers(records: list[dict[str, Any]]) -> list[tuple[float, float]]:
    values = []
    for record in records:
        x1, y1, x2, y2 = record["box"]
        values.append(((x1 + x2) / 2, (y1 + y2) / 2))
    return values


def _risk_row(row: sqlite3.Row, sidecar: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    records = extract_text_boxes(payload)
    text = str(row["text"] or "")
    item = sidecar.get(str(row["page_id"]), {})
    ordered = str(item.get("ordered_text") or text)
    payload_text = "\n".join(record["text"] for record in records)
    similarity = (
        difflib.SequenceMatcher(None, payload_text, ordered).ratio()
        if payload_text and ordered
        else 1.0
    )
    scores = [record["score"] for record in records if record.get("score") is not None]
    average_confidence = statistics.mean(scores) if scores else row["average_confidence"] or 0.0
    vertical_ratio = (
        sum(
            (record["box"][3] - record["box"][1])
            >= (record["box"][2] - record["box"][0]) * 1.35
            for record in records
        )
        / len(records)
        if records
        else 0.0
    )
    layout_reasons: list[str] = []
    ocr_reasons: list[str] = []
    if records and similarity < 0.72:
        layout_reasons.append("large_order_change")
    if records and row["reading_direction"] == "vertical-rtl" and vertical_ratio < 0.55:
        layout_reasons.append("direction_geometry_mismatch")
    if records and row["reading_direction"] == "horizontal-ltr" and vertical_ratio > 0.65:
        layout_reasons.append("direction_geometry_mismatch")
    if records and len(records) > 1:
        centers = _centers(records)
        x_gaps = [abs(right[0] - left[0]) for left, right in zip(centers, centers[1:])]
        if x_gaps and max(x_gaps) > statistics.median(x_gaps) * 8:
            layout_reasons.append("large_geometry_gap")
    if average_confidence < 0.82:
        ocr_reasons.append("low_segment_confidence")
    if len(NOISE_RE.findall(ordered)) >= 2:
        ocr_reasons.append("recognition_noise")
    if len(ordered.strip()) < 20 and records:
        ocr_reasons.append("short_text")
    risk = len(layout_reasons) * 35 + len(ocr_reasons) * 20
    if row["low_confidence"]:
        risk += 20
    priority = "P1" if risk >= 55 else ("P2" if risk >= 25 else "P3")
    return {
        "priority": priority,
        "risk_score": risk,
        "book_id": row["book_id"],
        "physical_page": row["physical_page"],
        "pdf_page_label": row["pdf_page_label"],
        "reading_direction": row["reading_direction"],
        "record_count": len(records),
        "average_confidence": round(float(average_confidence), 6),
        "vertical_ratio": round(float(vertical_ratio), 6),
        "order_similarity_to_payload": round(float(similarity), 6),
        "layout_reason": ";".join(layout_reasons),
        "ocr_reason": ";".join(ocr_reasons),
        "text_preview": " ".join(ordered.split())[:240],
    }


def audit(data_dir: Path, output: Path) -> dict[str, Any]:
    database = data_dir / "ancient_rag.db"
    sidecar = _sidecar(data_dir / "pages_layout_v2.jsonl")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT page_id, book_id, physical_page, pdf_page_label, reading_direction,
               average_confidence, low_confidence, text, payload_json
        FROM pages ORDER BY book_id, physical_page
        """
    ).fetchall()
    connection.close()
    results = [_risk_row(row, sidecar) for row in rows]
    results.sort(
        key=lambda item: (
            {"P1": 0, "P2": 1, "P3": 2}[item["priority"]],
            -int(item["risk_score"]),
            item["book_id"],
            int(item["physical_page"]),
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)
    return {
        "database": str(database),
        "sidecar": str(data_dir / "pages_layout_v2.jsonl"),
        "output": str(output),
        "pages": len(results),
        "by_priority": dict(Counter(row["priority"] for row in results)),
        "layout_risk_pages": sum(bool(row["layout_reason"]) for row in results),
        "ocr_risk_pages": sum(bool(row["ocr_reason"]) for row in results),
        "source_data_modified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit layout and OCR quality")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(audit(args.data_dir.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))
