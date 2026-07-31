#!/usr/bin/env python
"""Create review-only high-resolution OCR candidates for selected ancient pages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import fitz
import numpy as np

from reorder_ancient_pages import extract_text_boxes, order_text_boxes

try:
    import ancient_cli as base_ocr
except ModuleNotFoundError:  # Lets pure selection helpers run outside the OCR env.
    base_ocr = None


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output" / "second_pass_ocr_v1"
MODEL_CONFIGS = {
    "v5-server": {
        "name": "PP-OCRv5_server",
        "text_detection_model_name": "PP-OCRv5_server_det",
        "text_recognition_model_name": "PP-OCRv5_server_rec",
    },
    "v6-medium": {
        "name": "PP-OCRv6_medium",
        "text_detection_model_name": "PP-OCRv6_medium_det",
        "text_recognition_model_name": "PP-OCRv6_medium_rec",
    },
}
MANIFEST_FIELDS = (
    "book_id",
    "filename",
    "physical_page",
    "pdf_page_label",
    "priority",
    "priority_score",
    "source_sha256",
    "original_text_sha256",
    "candidate_text_sha256",
    "model",
    "render_dpi",
    "reading_direction",
    "column_count",
    "original_average_confidence",
    "candidate_average_confidence",
    "confidence_delta",
    "original_cjk_character_ratio",
    "candidate_cjk_character_ratio",
    "recommendation",
    "candidate_path",
    "original_preview",
    "candidate_preview",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ocr_module() -> Any:
    if base_ocr is None:
        raise RuntimeError("ancient_cli is required to run second-pass OCR")
    return base_ocr


def preview(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def candidate_path(output_dir: Path, book_id: str, physical_page: int) -> Path:
    safe_book_id = book_id.replace(":", "_")
    return output_dir / "candidates" / safe_book_id / f"page_{physical_page:06d}.json"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def select_audit_rows(
    audit_path: Path,
    priority: str,
    limit: int | None,
    book_id: str | None = None,
    physical_page: int | None = None,
) -> list[dict[str, str]]:
    with audit_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    selected = [
        row
        for row in rows
        if (priority == "all" or row.get("priority") == priority)
        and (book_id is None or row.get("book_id") == book_id)
        and (
            physical_page is None
            or int(row.get("physical_page") or 0) == physical_page
        )
    ]
    selected.sort(
        key=lambda row: (
            -int(row.get("priority_score") or 0),
            row.get("filename") or "",
            int(row.get("physical_page") or 0),
        )
    )
    unique: dict[tuple[str, int], dict[str, str]] = {}
    for row in selected:
        key = (row.get("book_id") or "", int(row.get("physical_page") or 0))
        unique.setdefault(key, row)
    result = list(unique.values())
    return result if limit is None else result[:limit]


def render_page(source_pdf: Path, physical_page: int, dpi: int) -> tuple[np.ndarray, float]:
    ocr = ocr_module()
    document = fitz.open(source_pdf)
    try:
        page = document.load_page(physical_page - 1)
        zoom = dpi / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    finally:
        document.close()
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    if pixmap.n == 4:
        image = image[:, :, :3]
    image = image.copy()
    return image, ocr.image_ink_ratio(image)


def candidate_config(model: str, dpi: int, textline_orientation: bool) -> dict[str, Any]:
    config = dict(MODEL_CONFIGS[model])
    config.update(
        {
            "dpi": dpi,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": textline_orientation,
        }
    )
    return config


def create_pipeline(
    model: str, dpi: int, textline_orientation: bool, device: str
) -> tuple[Any, dict[str, Any]]:
    ocr = ocr_module()
    config = candidate_config(model, dpi, textline_orientation)
    # Reuse the existing project cache setup without changing the main OCR run.
    ocr.MODEL_NAME = str(config["name"])
    ocr.MODEL_CONFIG = {
        key: value for key, value in config.items() if key != "name"
    }
    paths = ocr.Paths(
        corpus_dir=PROJECT_ROOT / "corpus" / "ancient_pdf" / "raw_flat",
        data_dir=DEFAULT_DATA_DIR,
        output_dir=DEFAULT_OUTPUT_DIR,
        model_home=PROJECT_ROOT / "models" / "ocr",
    )
    return ocr.create_pipeline(device, paths), config


def ordered_candidate_text(
    payload: dict[str, Any], page_width: int, expected_direction: str
) -> tuple[str, str, int, str, list[float]]:
    ocr = ocr_module()
    raw_segments = ocr.build_segments(payload)
    normalized_segments, detected_direction = ocr.order_segments(
        raw_segments, page_width
    )
    records = extract_text_boxes({"segments": normalized_segments})
    direction = (
        expected_direction
        if expected_direction in {"vertical-rtl", "horizontal-ltr"}
        else detected_direction
    )
    text, column_count, layout_status = order_text_boxes(records, direction)
    scores = [
        float(record["score"])
        for record in records
        if record.get("score") is not None
    ]
    return text, direction, column_count, layout_status, scores


def recommendation(
    original_confidence: float, original_cjk_ratio: float,
    candidate_confidence: float, candidate_cjk_ratio: float,
) -> str:
    if (
        candidate_confidence >= original_confidence + 0.03
        and candidate_cjk_ratio >= original_cjk_ratio - 0.01
    ):
        return "candidate_preferred_for_review"
    return "manual_compare_required"


def existing_candidate_is_current(
    path: Path, source_sha256: str, config: dict[str, Any]
) -> bool:
    if not path.is_file():
        return False
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        item.get("source_sha256") == source_sha256
        and item.get("candidate_config") == config
    )


def load_pages(database: Path) -> dict[tuple[str, int], dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label, p.text,
                   p.reading_direction, p.average_confidence, p.payload_json,
                   b.filename, b.source_path, b.source_sha256
            FROM pages p JOIN books b USING(book_id)
            """
        ).fetchall()
    finally:
        connection.close()
    return {(str(row["book_id"]), int(row["physical_page"])): dict(row) for row in rows}


def build_candidate(
    pipeline: Any,
    page: dict[str, Any],
    audit_row: dict[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    ocr = ocr_module()
    image, ink_ratio = render_page(
        Path(page["source_path"]), int(page["physical_page"]), int(config["dpi"])
    )
    started = time.perf_counter()
    results = list(pipeline.predict(image))
    elapsed = time.perf_counter() - started
    if len(results) != 1:
        raise RuntimeError(f"Expected one OCR result, got {len(results)}")
    scratch_dir = DEFAULT_OUTPUT_DIR / "scratch" / str(int(time.time() * 1000))
    payload = ocr.result_to_payload(results[0], scratch_dir)
    candidate_text, direction, column_count, layout_status, scores = ordered_candidate_text(
        payload, image.shape[1], str(page.get("reading_direction") or "")
    )
    candidate_quality = ocr.page_quality(candidate_text, scores, ink_ratio)
    original_payload = json.loads(page["payload_json"] or "{}")
    original_quality = original_payload.get("quality", {})
    original_confidence = float(page.get("average_confidence") or 0.0)
    original_cjk_ratio = float(original_quality.get("cjk_character_ratio") or 0.0)
    candidate_confidence = float(candidate_quality["average_confidence"])
    candidate_cjk_ratio = float(candidate_quality["cjk_character_ratio"])
    return {
        "schema_version": 1,
        "candidate_kind": "high_resolution_second_pass",
        "page_id": page["page_id"],
        "book_id": page["book_id"],
        "filename": page["filename"],
        "physical_page": page["physical_page"],
        "pdf_page_label": page["pdf_page_label"],
        "source_path": page["source_path"],
        "source_sha256": page["source_sha256"],
        "priority": audit_row.get("priority"),
        "priority_score": int(audit_row.get("priority_score") or 0),
        "original_text": page["text"],
        "original_text_sha256": sha256_text(str(page["text"] or "")),
        "candidate_text": candidate_text,
        "candidate_text_sha256": sha256_text(candidate_text),
        "candidate_config": config,
        "reading_direction": direction,
        "column_count": column_count,
        "layout_status": layout_status,
        "original_quality": original_quality,
        "candidate_quality": candidate_quality,
        "original_average_confidence": original_confidence,
        "candidate_average_confidence": candidate_confidence,
        "confidence_delta": round(candidate_confidence - original_confidence, 6),
        "original_cjk_character_ratio": original_cjk_ratio,
        "candidate_cjk_character_ratio": candidate_cjk_ratio,
        "recommendation": recommendation(
            original_confidence,
            original_cjk_ratio,
            candidate_confidence,
            candidate_cjk_ratio,
        ),
        "ocr_elapsed_seconds": round(elapsed, 4),
        "source_data_modified": False,
    }


def manifest_row(item: dict[str, Any], output_dir: Path, path: Path) -> dict[str, Any]:
    return {
        "book_id": item["book_id"],
        "filename": item["filename"],
        "physical_page": item["physical_page"],
        "pdf_page_label": item["pdf_page_label"],
        "priority": item["priority"],
        "priority_score": item["priority_score"],
        "source_sha256": item["source_sha256"],
        "original_text_sha256": item["original_text_sha256"],
        "candidate_text_sha256": item["candidate_text_sha256"],
        "model": item["candidate_config"]["name"],
        "render_dpi": item["candidate_config"]["dpi"],
        "reading_direction": item["reading_direction"],
        "column_count": item["column_count"],
        "original_average_confidence": item["original_average_confidence"],
        "candidate_average_confidence": item["candidate_average_confidence"],
        "confidence_delta": item["confidence_delta"],
        "original_cjk_character_ratio": item["original_cjk_character_ratio"],
        "candidate_cjk_character_ratio": item["candidate_cjk_character_ratio"],
        "recommendation": item["recommendation"],
        "candidate_path": str(path.relative_to(output_dir)),
        "original_preview": preview(str(item["original_text"])),
        "candidate_preview": preview(str(item["candidate_text"])),
    }


def write_manifests(output_dir: Path) -> int:
    candidates_dir = output_dir / "candidates"
    items: list[tuple[dict[str, Any], Path]] = []
    for path in sorted(candidates_dir.rglob("*.json")) if candidates_dir.is_dir() else []:
        try:
            items.append((json.loads(path.read_text(encoding="utf-8")), path))
        except (OSError, json.JSONDecodeError):
            continue
    rows = [manifest_row(item, output_dir, path) for item, path in items]
    rows.sort(key=lambda row: (-int(row["priority_score"]), row["filename"], int(row["physical_page"])))
    jsonl_path = output_dir / "candidate_manifest.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    csv_path = output_dir / "candidate_manifest.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    config = candidate_config(args.model, args.dpi, args.textline_orientation)
    targets = select_audit_rows(
        args.audit.resolve(),
        args.priority,
        args.limit,
        args.book_id,
        args.physical_page,
    )
    pages = load_pages(data_dir / "ancient_rag.db")
    pipeline: Any | None = None
    completed = skipped = 0
    for audit_row in targets:
        key = (audit_row.get("book_id") or "", int(audit_row.get("physical_page") or 0))
        page = pages.get(key)
        if page is None:
            raise ValueError(f"Page not found in database: {key}")
        if audit_row.get("source_sha256") != page["source_sha256"]:
            raise ValueError(f"Source SHA-256 mismatch: {key}")
        path = candidate_path(output_dir, page["book_id"], int(page["physical_page"]))
        if not args.force and existing_candidate_is_current(path, page["source_sha256"], config):
            skipped += 1
            continue
        if pipeline is None:
            pipeline, config = create_pipeline(
                args.model, args.dpi, args.textline_orientation, args.device
            )
        atomic_json(path, build_candidate(pipeline, page, audit_row, config))
        completed += 1
    manifest_rows = write_manifests(output_dir)
    report = {
        "audit": str(args.audit.resolve()),
        "database": str(data_dir / "ancient_rag.db"),
        "output_dir": str(output_dir),
        "priority": args.priority,
        "book_id": args.book_id,
        "physical_page": args.physical_page,
        "requested_limit": args.limit,
        "candidate_config": config,
        "targets": len(targets),
        "completed": completed,
        "skipped_current": skipped,
        "manifest_rows": manifest_rows,
        "source_data_modified": False,
        "database_modified": False,
        "vector_index_modified": False,
    }
    atomic_json(output_dir / "second_pass_report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate review-only second-pass OCR candidates")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--audit", type=Path, default=DEFAULT_DATA_DIR / "low_confidence_audit_v1.csv")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--priority", choices=("P1", "P2", "all"), default="P1")
    parser.add_argument("--book-id")
    parser.add_argument("--physical-page", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model", choices=sorted(MODEL_CONFIGS), default="v5-server")
    parser.add_argument("--dpi", type=int, default=360)
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--textline-orientation", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.dpi <= 0:
        raise ValueError("dpi must be positive")
    print(json.dumps(run(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
