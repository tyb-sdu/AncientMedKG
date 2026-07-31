#!/usr/bin/env python
"""Reconstruct ancient OCR reading order without modifying the source database."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = SCRIPT_DIR / "data" / "ancient_rag.db"
DEFAULT_OUTPUT = SCRIPT_DIR / "data" / "pages_layout_v2.jsonl"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    return str(value).strip()


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, dict):
        if all(key in value for key in ("x", "y", "w", "h")):
            x, y = _number(value["x"]), _number(value["y"])
            width, height = _number(value["w"]), _number(value["h"])
            if None not in (x, y, width, height):
                return (x, y, x + width, y + height)
        for key in ("bbox", "box", "polygon", "points"):
            if key in value:
                result = _box(value[key])
                if result:
                    return result
        return None
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    points: list[tuple[float, float]] = []
    if len(value) == 4 and all(
        isinstance(item, (list, tuple)) and len(item) >= 2 for item in value
    ):
        points = [
            (float(item[0]), float(item[1]))
            for item in value
            if _number(item[0]) is not None and _number(item[1]) is not None
        ]
    elif len(value) >= 8 and all(_number(item) is not None for item in value[:8]):
        numbers = [float(item) for item in value[:8]]
        points = list(zip(numbers[::2], numbers[1::2]))
    elif len(value) == 4 and all(_number(item) is not None for item in value):
        x1, y1, x2, y2 = [float(item) for item in value]
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if not points:
        return None
    xs, ys = zip(*points)
    return (min(xs), min(ys), max(xs), max(ys))


def _records_from_parallel(
    boxes: Any, texts: Any, scores: Any = None
) -> list[dict[str, Any]]:
    if not isinstance(boxes, list) or not isinstance(texts, list):
        return []
    records: list[dict[str, Any]] = []
    for index, (raw_box, raw_text) in enumerate(zip(boxes, texts)):
        box = _box(raw_box)
        text = _text(raw_text)
        if not box or not text:
            continue
        score = scores[index] if isinstance(scores, list) and index < len(scores) else None
        records.append(
            {
                "text": text,
                "box": box,
                "score": _number(score),
                "stored_order": None,
                "orientation": None,
            }
        )
    return records


def extract_text_boxes(payload: Any) -> list[dict[str, Any]]:
    """Accept common PaddleOCR JSON shapes and a normalized list-of-records shape."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        records = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            box = _box(item)
            text = _text(item.get("text") or item.get("rec_text") or item.get("transcription"))
            if box and text:
                records.append(
                    {
                        "text": text,
                        "box": box,
                        "score": _number(
                            item.get("confidence", item.get("score", item.get("rec_score")))
                        ),
                        "stored_order": _number(item.get("order")),
                        "orientation": item.get("orientation"),
                    }
                )
        if records:
            return records
        for item in payload:
            records.extend(extract_text_boxes(item))
        return records
    if not isinstance(payload, dict):
        return []
    for box_key, text_key, score_key in (
        ("dt_polys", "rec_texts", "rec_scores"),
        ("rec_boxes", "rec_texts", "rec_scores"),
        ("boxes", "texts", "scores"),
        ("polys", "texts", "scores"),
    ):
        records = _records_from_parallel(
            payload.get(box_key), payload.get(text_key), payload.get(score_key)
        )
        if records:
            return records
    for key in ("segments", "ocr_res", "ocr_result", "result", "res", "data", "pages"):
        if key in payload:
            records = extract_text_boxes(payload[key])
            if records:
                return records
    return []


def _center(record: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = record["box"]
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _median(values: list[float]) -> float:
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _cluster_columns(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cluster horizontal-layout columns using page-scale x gaps."""
    if len(records) < 2:
        return [records]
    centers = sorted((_center(record)[0] for record in records))
    gaps = [right - left for left, right in zip(centers, centers[1:])]
    page_span = max(centers) - min(centers)
    threshold = max(page_span * 0.18, 18.0)
    split_positions = [index + 1 for index, gap in enumerate(gaps) if gap > threshold]
    if not split_positions:
        return [records]
    groups: list[list[dict[str, Any]]] = []
    start = 0
    sorted_records = sorted(records, key=lambda record: _center(record)[0])
    for end in [*split_positions, len(sorted_records)]:
        group = sorted_records[start:end]
        if group:
            groups.append(group)
        start = end
    return groups if len(groups) <= 6 else [records]


def _is_vertical_record(
    record: dict[str, Any], *, assume_vertical: bool = False
) -> bool:
    orientation = str(record.get("orientation") or "").lower()
    if orientation in {"vertical", "horizontal"}:
        return orientation == "vertical"
    if assume_vertical:
        return True
    x1, y1, x2, y2 = record["box"]
    return (y2 - y1) >= (x2 - x1) * 1.35


def _cluster_vertical_columns(
    records: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group vertical boxes while tolerating scan skew and detector overlap."""
    if not records:
        return []
    widths = [max(record["box"][2] - record["box"][0], 1.0) for record in records]
    centers = [_center(record)[0] for record in records]
    page_span = max(centers) - min(centers) if len(records) > 1 else 0.0
    median_width = _median(widths)
    center_tolerance = max(median_width * 0.80, page_span * 0.008, 12.0)
    overlap_center_tolerance = max(median_width * 0.90, page_span * 0.010, 16.0)

    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    def x_interval_relation(
        left: dict[str, Any], right: dict[str, Any]
    ) -> tuple[float, float]:
        left_x1, _, left_x2, _ = left["box"]
        right_x1, _, right_x2, _ = right["box"]
        overlap = min(left_x2, right_x2) - max(left_x1, right_x1)
        if overlap > 0:
            min_width = max(
                min(left_x2 - left_x1, right_x2 - right_x1),
                1.0,
            )
            return 0.0, overlap / min_width
        if left_x2 < right_x1:
            return right_x1 - left_x2, 0.0
        if right_x2 < left_x1:
            return left_x1 - right_x2, 0.0
        return 0.0, 0.0

    for left_index, left in enumerate(records):
        left_center = _center(left)[0]
        for right_index in range(left_index + 1, len(records)):
            right = records[right_index]
            center_gap = abs(left_center - _center(right)[0])
            _, overlap_ratio = x_interval_relation(left, right)
            interval_close = (
                overlap_ratio >= 0.25
                and center_gap <= overlap_center_tolerance
            )
            if center_gap <= center_tolerance or interval_close:
                union(left_index, right_index)

    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        grouped.setdefault(find(index), []).append(record)

    columns = list(grouped.values())
    columns.sort(
        key=lambda column: -_median([_center(item)[0] for item in column])
    )
    return columns


def _vertical_sort_key(record: dict[str, Any]) -> tuple[float, float]:
    stored_order = _number(record.get("stored_order"))
    return (
        _center(record)[1],
        stored_order if stored_order is not None else float("inf"),
    )


def order_text_boxes(
    records: list[dict[str, Any]], direction: str
) -> tuple[str, int, str]:
    if not records:
        return "", 0, "no_boxes"
    vertical = direction.startswith("vertical")
    if vertical:
        vertical_records = [
            record for record in records if _is_vertical_record(record, assume_vertical=True)
        ]
        horizontal_records = [
            record
            for record in records
            if str(record.get("orientation") or "").lower() == "horizontal"
        ]
        columns = _cluster_vertical_columns(vertical_records)
        ordered = [
            item
            for column in columns
            for item in sorted(column, key=_vertical_sort_key)
        ]
        # Headings, figure labels, and page numbers can be horizontal on an
        # otherwise vertical page; place them by their x band without allowing
        # them to reorder the vertical text inside a column.
        for record in sorted(horizontal_records, key=lambda item: (-_center(item)[0], _center(item)[1])):
            insert_at = len(ordered)
            for index, existing in enumerate(ordered):
                if _center(record)[0] > _center(existing)[0]:
                    insert_at = index
                    break
            ordered.insert(insert_at, record)
        column_count = len(columns)
    else:
        columns = _cluster_columns(records)
        columns.sort(key=lambda column: _median([_center(item)[0] for item in column]))
        ordered = [
            item
            for column in columns
            for item in sorted(column, key=lambda record: (_center(record)[1], _center(record)[0]))
        ]
        column_count = len(columns)
    text = "\n".join(item["text"] for item in ordered if item["text"])
    status = "ordered" if column_count > 1 or len(records) > 1 else "single_column"
    return text, column_count, status


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def reorder_database(
    database: Path, output: Path, limit: int | None = None, resume: bool = True
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    completed: set[str] = set()
    if resume and output.exists():
        with output.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    completed.add(str(json.loads(line)["page_id"]))
                except (json.JSONDecodeError, KeyError):
                    continue
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT page_id, book_id, physical_page, pdf_page_label, text,
               reading_direction, average_confidence, payload_json
        FROM pages ORDER BY page_id
        """
    )
    processed = skipped = no_boxes = 0
    mode = "a" if resume else "w"
    with output.open(mode, encoding="utf-8", newline="\n") as stream:
        for row in rows:
            page_id = str(row["page_id"])
            if page_id in completed:
                skipped += 1
                continue
            if limit is not None and processed >= limit:
                break
            records = extract_text_boxes(row["payload_json"])
            ordered_text, column_count, layout_status = order_text_boxes(
                records, str(row["reading_direction"] or "horizontal-ltr")
            )
            original_text = str(row["text"] or "")
            if not records:
                ordered_text = original_text
                no_boxes += 1
            item = {
                "page_id": page_id,
                "book_id": row["book_id"],
                "physical_page": row["physical_page"],
                "pdf_page_label": row["pdf_page_label"],
                "reading_direction": row["reading_direction"],
                "average_confidence": row["average_confidence"],
                "original_text_sha256": _sha256_text(original_text),
                "ordered_text": ordered_text,
                "column_count": column_count,
                "layout_status": layout_status,
                "source_data_modified": False,
            }
            stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            processed += 1
    connection.close()
    report = {
        "database": str(database),
        "output": str(output),
        "processed": processed,
        "skipped_existing": skipped,
        "no_boxes": no_boxes,
        "source_data_modified": False,
    }
    output.with_name(output.stem + "_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reorder ancient OCR text by page layout")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(
        json.dumps(
            reorder_database(args.database, args.output, args.limit, not args.no_resume),
            ensure_ascii=False,
            indent=2,
        )
    )
