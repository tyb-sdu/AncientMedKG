#!/usr/bin/env python
"""Generate review-only PaddleOCR-VL candidates for high-risk ancient pages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import sqlite3
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output" / "paddleocr_vl_candidates_v1"
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ASCII_NOISE_RE = re.compile(r"[A-Za-z]")
KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
IMAGE_BLOCK_LABELS = {
    "image",
    "header_image",
    "footer_image",
    "aside_image",
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
    "pipeline_version",
    "render_dpi",
    "render_backend",
    "render_warning",
    "reading_direction",
    "raw_vl_block_count",
    "candidate_block_count",
    "candidate_ordered_block_count",
    "candidate_non_text_block_count",
    "original_visible_character_count",
    "candidate_visible_character_count",
    "original_cjk_character_ratio",
    "candidate_cjk_character_ratio",
    "candidate_ascii_noise_count",
    "candidate_kana_character_count",
    "candidate_max_repeated_4gram_count",
    "review_flags",
    "recommendation",
    "image_path",
    "candidate_path",
    "original_preview",
    "candidate_preview",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compact_preview(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def candidate_path(output_dir: Path, book_id: str, physical_page: int) -> Path:
    safe_book_id = book_id.replace(":", "_")
    return output_dir / "candidates" / safe_book_id / f"page_{physical_page:06d}.json"


def image_path(output_dir: Path, book_id: str, physical_page: int) -> Path:
    safe_book_id = book_id.replace(":", "_")
    return output_dir / "rendered" / safe_book_id / f"page_{physical_page:06d}.png"


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
        unique.setdefault(
            (row.get("book_id") or "", int(row.get("physical_page") or 0)), row
        )
    result = list(unique.values())
    return result if limit is None else result[:limit]


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


def render_page(
    source_pdf: Path, physical_page: int, dpi: int, output: Path
) -> tuple[str, str | None]:
    renderer = shutil.which("pdftoppm")
    if renderer is None:
        raise RuntimeError(
            "pdftoppm is required for deterministic PaddleOCR-VL page rendering"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_prefix = output.with_suffix(".render_tmp")
    generated = temporary_prefix.with_suffix(".render_tmp.png")
    completed = subprocess.run(
        [
            renderer,
            "-f",
            str(physical_page),
            "-l",
            str(physical_page),
            "-r",
            str(dpi),
            "-png",
            "-singlefile",
            str(source_pdf),
            str(temporary_prefix),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode == 0 and generated.is_file():
        generated.replace(output)
        return "poppler_pdftoppm", None

    poppler_error = (completed.stderr or "pdftoppm failed").strip()[:2000]
    try:
        import fitz

        document = fitz.open(source_pdf)
        try:
            page = document.load_page(physical_page - 1)
            zoom = dpi / 72
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            pixmap.save(output)
        finally:
            document.close()
    except Exception as error:
        raise RuntimeError(
            f"Poppler failed: {poppler_error}; PyMuPDF fallback failed: {error}"
        ) from error
    return "pymupdf_fallback", poppler_error


def text_quality(text: str) -> dict[str, Any]:
    visible = [character for character in text if not character.isspace()]
    compact = "".join(visible)
    four_grams = Counter(
        compact[index : index + 4] for index in range(max(len(compact) - 3, 0))
    )
    return {
        "visible_character_count": len(visible),
        "cjk_character_ratio": round(
            len(CJK_RE.findall(text)) / max(len(visible), 1), 6
        ),
        "ascii_noise_count": len(ASCII_NOISE_RE.findall(text)),
        "kana_character_count": len(KANA_RE.findall(text)),
        "max_repeated_4gram_count": max(four_grams.values(), default=0),
    }


def clean_block_content(label: str, content: str) -> str:
    if label in IMAGE_BLOCK_LABELS or "<img" in content.lower():
        return ""
    return html.unescape(HTML_TAG_RE.sub("", content)).strip()


def non_text_block_count(payload: dict[str, Any]) -> int:
    blocks = payload.get("parsing_res_list") or []
    if not isinstance(blocks, list):
        return 0
    return sum(
        1
        for raw in blocks
        if isinstance(raw, dict)
        and (
            str(raw.get("block_label") or "") in IMAGE_BLOCK_LABELS
            or "<img" in str(raw.get("block_content") or "").lower()
        )
    )


def ordered_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = payload.get("parsing_res_list") or []
    if not isinstance(blocks, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("block_label") or "")
        content = clean_block_content(label, str(raw.get("block_content") or ""))
        if not content:
            continue
        result.append(
            {
                "block_label": label,
                "block_content": content,
                "block_bbox": raw.get("block_bbox") or [],
                "block_id": raw.get("block_id"),
                "block_order": raw.get("block_order"),
                "group_id": raw.get("group_id"),
            }
        )
    result.sort(
        key=lambda block: (
            block["block_order"] is None,
            int(block["block_order"] or 0),
            int(block["block_id"] or 0),
        )
    )
    return result


def candidate_text(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    blocks = ordered_blocks(payload)
    return "\n".join(block["block_content"] for block in blocks), blocks


def result_payload(result: Any) -> dict[str, Any]:
    value = getattr(result, "json", None)
    if callable(value):
        value = value()
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, dict):
        return value.get("res", value)
    raise RuntimeError("PaddleOCR-VL result did not expose JSON data")


def candidate_recommendation(
    original: dict[str, Any],
    candidate: dict[str, Any],
    candidate_non_text_blocks: int = 0,
) -> str:
    # This is intentionally not an automatic correction decision.
    return (
        "vl_candidate_ready_for_review"
        if not candidate_review_flags(original, candidate, candidate_non_text_blocks)
        else "manual_compare_required"
    )


def candidate_review_flags(
    original: dict[str, Any],
    candidate: dict[str, Any],
    candidate_non_text_blocks: int = 0,
) -> list[str]:
    flags: list[str] = []
    original_count = int(original.get("visible_character_count") or 0)
    candidate_count = int(candidate.get("visible_character_count") or 0)
    if candidate_count == 0:
        flags.append("empty_candidate")
    elif original_count and candidate_count < original_count * 0.75:
        flags.append("candidate_too_short")
    if original_count >= 20 and candidate_count > original_count * 1.35:
        flags.append("candidate_too_long")
    minimum_cjk_ratio = max(
        0.80, float(original.get("cjk_character_ratio") or 0.0) - 0.02
    )
    if float(candidate.get("cjk_character_ratio") or 0.0) < minimum_cjk_ratio:
        flags.append("low_cjk_ratio")
    if int(candidate.get("ascii_noise_count") or 0) > max(8, candidate_count * 0.05):
        flags.append("ascii_noise")
    if int(candidate.get("kana_character_count") or 0) > max(2, candidate_count * 0.01):
        flags.append("kana_noise")
    if int(candidate.get("max_repeated_4gram_count") or 0) >= 12:
        flags.append("repeated_text")
    if candidate_non_text_blocks:
        flags.append("contains_non_text_blocks")
    return flags


def pipeline_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "name": "PaddleOCR-VL-1.6",
        "pipeline_version": args.pipeline_version,
        "render_dpi": args.render_dpi,
        "renderer": "poppler_pdftoppm",
        "device": args.device,
        "use_doc_orientation_classify": args.use_doc_orientation_classify,
        "use_doc_unwarping": args.use_doc_unwarping,
        "use_layout_detection": None,
        "format_block_content": True,
        "max_new_tokens": args.max_new_tokens,
    }


def create_pipeline(args: argparse.Namespace) -> Any:
    from paddleocr import PaddleOCRVL

    options: dict[str, Any] = {
        "pipeline_version": args.pipeline_version,
        "device": args.device,
        "format_block_content": True,
    }
    if args.use_doc_orientation_classify is not None:
        options["use_doc_orientation_classify"] = args.use_doc_orientation_classify
    if args.use_doc_unwarping is not None:
        options["use_doc_unwarping"] = args.use_doc_unwarping
    return PaddleOCRVL(**options)


def current_candidate(path: Path, source_sha256: str, config: dict[str, Any]) -> bool:
    if not path.is_file():
        return False
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return item.get("source_sha256") == source_sha256 and item.get("pipeline_config") == config


def candidate_analysis(
    payload: dict[str, Any],
    original_text: str,
    render_backend: str = "poppler_pdftoppm",
) -> dict[str, Any]:
    text, blocks = candidate_text(payload)
    original_quality = text_quality(original_text)
    vl_quality = text_quality(text)
    raw_blocks = payload.get("parsing_res_list") or []
    raw_block_count = len(raw_blocks) if isinstance(raw_blocks, list) else 0
    ignored_blocks = non_text_block_count(payload)
    review_flags = candidate_review_flags(
        original_quality, vl_quality, ignored_blocks
    )
    if render_backend != "poppler_pdftoppm":
        review_flags.append("render_fallback")
    return {
        "schema_version": 2,
        "candidate_text": text,
        "candidate_text_sha256": sha256_text(text),
        "original_quality": original_quality,
        "candidate_quality": vl_quality,
        "raw_vl_block_count": raw_block_count,
        "candidate_block_count": len(blocks),
        "candidate_ordered_block_count": sum(
            block["block_order"] is not None for block in blocks
        ),
        "candidate_non_text_block_count": ignored_blocks,
        "review_flags": review_flags,
        "recommendation": (
            "manual_compare_required"
            if review_flags
            else "vl_candidate_ready_for_review"
        ),
        "blocks": blocks,
    }


def refresh_candidate_analysis(item: dict[str, Any]) -> bool:
    payload = item.get("raw_vl_result")
    if not isinstance(payload, dict):
        return False
    analysis = candidate_analysis(
        payload,
        str(item.get("original_text") or ""),
        str(item.get("render_backend") or "poppler_pdftoppm"),
    )
    changed = any(item.get(key) != value for key, value in analysis.items())
    item.update(analysis)
    return changed


def build_candidate(
    pipeline: Any,
    page: dict[str, Any],
    audit_row: dict[str, str],
    rendered: Path,
    config: dict[str, Any],
    render_backend: str = "poppler_pdftoppm",
    render_warning: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    # PaddleOCR-VL exposes generation length on predict(), rather than its
    # constructor. This mirrors the package's official CLI execution path.
    results = list(
        pipeline.predict(
            str(rendered),
            max_new_tokens=config["max_new_tokens"],
        )
    )
    elapsed = time.perf_counter() - started
    if len(results) != 1:
        raise RuntimeError(f"Expected one PaddleOCR-VL result, got {len(results)}")
    payload = result_payload(results[0])
    original_text = str(page.get("text") or "")
    item = {
        "candidate_kind": "paddleocr_vl_full_page",
        "page_id": page["page_id"],
        "book_id": page["book_id"],
        "filename": page["filename"],
        "physical_page": page["physical_page"],
        "pdf_page_label": page["pdf_page_label"],
        "source_path": page["source_path"],
        "source_sha256": page["source_sha256"],
        "priority": audit_row.get("priority"),
        "priority_score": int(audit_row.get("priority_score") or 0),
        "original_text": original_text,
        "original_text_sha256": sha256_text(original_text),
        "pipeline_config": config,
        "render_backend": render_backend,
        "render_warning": render_warning,
        "reading_direction": page["reading_direction"],
        "raw_vl_result": payload,
        "elapsed_seconds": round(elapsed, 4),
        "source_data_modified": False,
        "database_modified": False,
        "vector_index_modified": False,
    }
    item.update(candidate_analysis(payload, original_text, render_backend))
    return item


def manifest_row(item: dict[str, Any], output_dir: Path, candidate: Path, rendered: Path) -> dict[str, Any]:
    original_quality = item["original_quality"]
    candidate_quality = item["candidate_quality"]
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
        "pipeline_version": item["pipeline_config"]["pipeline_version"],
        "render_dpi": item["pipeline_config"]["render_dpi"],
        "render_backend": item.get("render_backend", "poppler_pdftoppm"),
        "render_warning": item.get("render_warning") or "",
        "reading_direction": item["reading_direction"],
        "raw_vl_block_count": item["raw_vl_block_count"],
        "candidate_block_count": item["candidate_block_count"],
        "candidate_ordered_block_count": item["candidate_ordered_block_count"],
        "candidate_non_text_block_count": item["candidate_non_text_block_count"],
        "original_visible_character_count": original_quality["visible_character_count"],
        "candidate_visible_character_count": candidate_quality["visible_character_count"],
        "original_cjk_character_ratio": original_quality["cjk_character_ratio"],
        "candidate_cjk_character_ratio": candidate_quality["cjk_character_ratio"],
        "candidate_ascii_noise_count": candidate_quality["ascii_noise_count"],
        "candidate_kana_character_count": candidate_quality["kana_character_count"],
        "candidate_max_repeated_4gram_count": candidate_quality[
            "max_repeated_4gram_count"
        ],
        "review_flags": "|".join(item["review_flags"]),
        "recommendation": item["recommendation"],
        "image_path": str(rendered.relative_to(output_dir)),
        "candidate_path": str(candidate.relative_to(output_dir)),
        "original_preview": compact_preview(item["original_text"]),
        "candidate_preview": compact_preview(item["candidate_text"]),
    }


def write_manifests(output_dir: Path) -> int:
    candidates_dir = output_dir / "candidates"
    items: list[tuple[dict[str, Any], Path, Path]] = []
    if candidates_dir.is_dir():
        for candidate in sorted(candidates_dir.rglob("*.json")):
            try:
                item = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if refresh_candidate_analysis(item):
                atomic_json(candidate, item)
            rendered = image_path(
                output_dir, str(item["book_id"]), int(item["physical_page"])
            )
            items.append((item, candidate, rendered))
    rows = [manifest_row(item, output_dir, candidate, rendered) for item, candidate, rendered in items]
    rows.sort(key=lambda row: (-int(row["priority_score"]), row["filename"], int(row["physical_page"])))
    jsonl = output_dir / "vl_candidate_manifest.jsonl"
    with jsonl.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    csv_path = output_dir / "vl_candidate_manifest.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    config = pipeline_config(args)
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
    failures: list[dict[str, Any]] = []
    for audit_row in targets:
        key = (audit_row.get("book_id") or "", int(audit_row.get("physical_page") or 0))
        page = pages.get(key)
        if page is None:
            raise ValueError(f"Page not found in database: {key}")
        if audit_row.get("source_sha256") != page["source_sha256"]:
            raise ValueError(f"Source SHA-256 mismatch: {key}")
        candidate = candidate_path(output_dir, page["book_id"], int(page["physical_page"]))
        if not args.force and current_candidate(candidate, page["source_sha256"], config):
            skipped += 1
            continue
        rendered = image_path(output_dir, page["book_id"], int(page["physical_page"]))
        try:
            render_backend, render_warning = render_page(
                Path(page["source_path"]),
                int(page["physical_page"]),
                args.render_dpi,
                rendered,
            )
            if pipeline is None:
                pipeline = create_pipeline(args)
            atomic_json(
                candidate,
                build_candidate(
                    pipeline,
                    page,
                    audit_row,
                    rendered,
                    config,
                    render_backend,
                    render_warning,
                ),
            )
            completed += 1
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "book_id": page["book_id"],
                        "physical_page": page["physical_page"],
                        "render_backend": render_backend,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as error:
            failure = {
                "book_id": page["book_id"],
                "filename": page["filename"],
                "physical_page": page["physical_page"],
                "source_sha256": page["source_sha256"],
                "error_type": type(error).__name__,
                "error": str(error),
            }
            failures.append(failure)
            print(
                json.dumps({"status": "failed", **failure}, ensure_ascii=False),
                flush=True,
            )
    manifest_rows = write_manifests(output_dir)
    atomic_json(
        output_dir / "paddleocr_vl_failures.json",
        {
            "failures": failures,
            "source_data_modified": False,
            "database_modified": False,
            "vector_index_modified": False,
        },
    )
    report = {
        "audit": str(args.audit.resolve()),
        "database": str(data_dir / "ancient_rag.db"),
        "output_dir": str(output_dir),
        "priority": args.priority,
        "book_id": args.book_id,
        "physical_page": args.physical_page,
        "requested_limit": args.limit,
        "pipeline_config": config,
        "targets": len(targets),
        "completed": completed,
        "skipped_current": skipped,
        "failed": len(failures),
        "manifest_rows": manifest_rows,
        "source_data_modified": False,
        "database_modified": False,
        "vector_index_modified": False,
    }
    atomic_json(output_dir / "paddleocr_vl_candidate_report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate PaddleOCR-VL review candidates")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--audit", type=Path, default=DEFAULT_DATA_DIR / "low_confidence_audit_v1.csv")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--priority", choices=("P1", "P2", "all"), default="P1")
    parser.add_argument("--book-id")
    parser.add_argument("--physical-page", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pipeline-version", choices=("v1", "v1.5", "v1.6"), default="v1.6")
    parser.add_argument("--render-dpi", type=int, default=360)
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--max-new-tokens", type=int, default=4096)
    orientation_group = parser.add_mutually_exclusive_group()
    orientation_group.add_argument(
        "--use-doc-orientation-classify",
        dest="use_doc_orientation_classify",
        action="store_true",
    )
    orientation_group.add_argument(
        "--no-doc-orientation-classify",
        dest="use_doc_orientation_classify",
        action="store_false",
    )
    unwarping_group = parser.add_mutually_exclusive_group()
    unwarping_group.add_argument(
        "--use-doc-unwarping", dest="use_doc_unwarping", action="store_true"
    )
    unwarping_group.add_argument(
        "--no-doc-unwarping", dest="use_doc_unwarping", action="store_false"
    )
    parser.set_defaults(
        use_doc_orientation_classify=None,
        use_doc_unwarping=None,
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.render_dpi <= 0 or args.max_new_tokens <= 0:
        raise ValueError("render-dpi and max-new-tokens must be positive")
    print(json.dumps(run(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
