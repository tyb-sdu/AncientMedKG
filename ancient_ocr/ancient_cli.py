from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import fitz
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CORPUS_DIR = PROJECT_ROOT / "corpus" / "ancient_pdf" / "raw_flat"
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_MODEL_HOME = PROJECT_ROOT / "models" / "ocr"
SCHEMA_VERSION = 1
NATIVE_TEXT_NORMALIZER_VERSION = 2
MODEL_NAME = "PP-OCRv6_medium"
MODEL_CONFIG = {
    "text_detection_model_name": "PP-OCRv6_medium_det",
    "text_recognition_model_name": "PP-OCRv6_medium_rec",
    "dpi": 240,
    "use_doc_orientation_classify": False,
    "use_doc_unwarping": False,
    "use_textline_orientation": False,
}
NATIVE_TEXT_FILENAMES = {"07_太平圣惠方_文本检索副本_含卷九十一.pdf"}
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True)
class Paths:
    corpus_dir: Path
    data_dir: Path
    output_dir: Path
    model_home: Path

    @property
    def books_manifest(self) -> Path:
        return self.data_dir / "books.jsonl"

    @property
    def page_output_dir(self) -> Path:
        return self.output_dir / "pages"

    @property
    def qa_output_dir(self) -> Path:
        return self.output_dir / "qa"

    @property
    def database(self) -> Path:
        return self.data_dir / "ancient_rag.db"

    @property
    def pages_jsonl(self) -> Path:
        return self.data_dir / "pages.jsonl"

    @property
    def state_dir(self) -> Path:
        return SCRIPT_DIR / "state"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_config_sha256() -> str:
    encoded = json.dumps(MODEL_CONFIG, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_book_id(source_sha256: str) -> str:
    return f"ancient:{source_sha256[:20]}"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def normalize_native_text(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\x", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    compact: list[str] = []
    for line in lines:
        if not line and (not compact or not compact[-1]):
            continue
        compact.append(line)
    return "\n".join(compact).strip()


def page_label(page: fitz.Page, physical_page: int) -> str:
    getter = getattr(page, "get_label", None)
    if getter is None:
        return str(physical_page)
    return getter() or str(physical_page)


def polygon_to_box(polygon: Any) -> list[float]:
    points = np.asarray(polygon, dtype=float)
    return [
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    ]


def order_segments(
    segments: list[dict[str, Any]], page_width: float
) -> tuple[list[dict[str, Any]], str]:
    if not segments:
        return [], "empty"

    for segment in segments:
        x1, y1, x2, y2 = segment["box"]
        segment["_cx"] = (x1 + x2) / 2
        segment["_cy"] = (y1 + y2) / 2
        segment["_width"] = max(x2 - x1, 1.0)
        segment["_height"] = max(y2 - y1, 1.0)
        segment["_vertical"] = segment["_height"] >= segment["_width"] * 1.35

    vertical_ratio = sum(bool(item["_vertical"]) for item in segments) / len(segments)
    if vertical_ratio < 0.55:
        ordered = sorted(segments, key=lambda item: (item["_cy"], item["_cx"]))
        direction = "horizontal-ltr"
    else:
        vertical = [item for item in segments if item["_vertical"]]
        horizontal = [item for item in segments if not item["_vertical"]]
        median_width = statistics.median(item["_width"] for item in vertical)
        tolerance = max(median_width * 0.65, page_width * 0.008)
        columns: list[dict[str, Any]] = []

        for segment in sorted(vertical, key=lambda item: -item["_cx"]):
            matching = [
                column
                for column in columns
                if abs(segment["_cx"] - column["center_x"]) <= tolerance
            ]
            if matching:
                column = min(
                    matching, key=lambda item: abs(segment["_cx"] - item["center_x"])
                )
                column["segments"].append(segment)
                column["center_x"] = statistics.mean(
                    item["_cx"] for item in column["segments"]
                )
            else:
                columns.append({"center_x": segment["_cx"], "segments": [segment]})

        columns.sort(key=lambda item: -item["center_x"])
        ordered = []
        for column in columns:
            ordered.extend(sorted(column["segments"], key=lambda item: item["_cy"]))

        for segment in sorted(horizontal, key=lambda item: (-item["_cx"], item["_cy"])):
            insert_at = len(ordered)
            for index, existing in enumerate(ordered):
                if segment["_cx"] > existing["_cx"]:
                    insert_at = index
                    break
            ordered.insert(insert_at, segment)
        direction = "vertical-rtl"

    clean: list[dict[str, Any]] = []
    for index, segment in enumerate(ordered):
        clean.append(
            {
                "order": index,
                "text": segment["text"],
                "confidence": round(float(segment["confidence"]), 6),
                "polygon": segment["polygon"],
                "box": [round(float(value), 2) for value in segment["box"]],
                "orientation": "vertical" if segment["_vertical"] else "horizontal",
            }
        )
    return clean, direction


def result_to_payload(result: Any, scratch_dir: Path) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)

    value = getattr(result, "json", None)
    if value is not None:
        value = value() if callable(value) else value
        if isinstance(value, str):
            return json.loads(value)
        if isinstance(value, dict):
            return value.get("res", value)

    scratch_dir.mkdir(parents=True, exist_ok=True)
    result.save_to_json(str(scratch_dir))
    candidates = sorted(scratch_dir.glob("*_res.json"), key=lambda item: item.stat().st_mtime)
    if not candidates:
        raise RuntimeError("PaddleOCR result did not expose JSON data")
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def build_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    texts = payload.get("rec_texts", [])
    scores = payload.get("rec_scores", [])
    polygons = payload.get("rec_polys", payload.get("dt_polys", []))
    segments: list[dict[str, Any]] = []
    for text, score, polygon in zip(texts, scores, polygons):
        text = str(text).strip()
        if not text:
            continue
        polygon_list = np.asarray(polygon).astype(float).tolist()
        segments.append(
            {
                "text": text,
                "confidence": float(score),
                "polygon": polygon_list,
                "box": polygon_to_box(polygon_list),
            }
        )
    return segments


def image_ink_ratio(image: np.ndarray) -> float:
    if image.ndim == 3:
        gray = image.mean(axis=2)
    else:
        gray = image
    return float(np.mean(gray < 220))


def page_quality(text: str, confidences: list[float], ink_ratio: float) -> dict[str, Any]:
    visible = [character for character in text if not character.isspace()]
    cjk_count = len(CJK_PATTERN.findall(text))
    cjk_ratio = cjk_count / max(len(visible), 1)
    average_confidence = statistics.mean(confidences) if confidences else 0.0
    blank_likely = ink_ratio < 0.002
    low_confidence = not blank_likely and (
        len(visible) < 20 or average_confidence < 0.82 or cjk_ratio < 0.45
    )
    return {
        "visible_character_count": len(visible),
        "cjk_character_ratio": round(cjk_ratio, 4),
        "average_confidence": round(average_confidence, 6),
        "ink_ratio": round(ink_ratio, 6),
        "blank_likely": blank_likely,
        "low_confidence": low_confidence,
    }


def inventory(paths: Paths) -> int:
    pdf_paths = sorted(paths.corpus_dir.glob("*.pdf"), key=lambda item: item.name.casefold())
    rows: list[dict[str, Any]] = []
    for index, pdf_path in enumerate(pdf_paths, start=1):
        print(f"[{index}/{len(pdf_paths)}] inventory {pdf_path.name}", flush=True)
        source_sha256 = file_sha256(pdf_path)
        document = fitz.open(pdf_path)
        sampled_pages = sorted(
            {
                0,
                min(9, document.page_count - 1),
                document.page_count // 2,
                document.page_count - 1,
            }
        )
        sampled_counts = [
            len("".join(document.load_page(page).get_text("text").split()))
            for page in sampled_pages
        ]
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "book_id": stable_book_id(source_sha256),
                "title": pdf_path.stem,
                "filename": pdf_path.name,
                "source_path": str(pdf_path.resolve()),
                "source_sha256": source_sha256,
                "size_bytes": pdf_path.stat().st_size,
                "page_count": document.page_count,
                "sampled_physical_pages": [page + 1 for page in sampled_pages],
                "sampled_native_text_chars": sampled_counts,
                "processing_mode": (
                    "native_text"
                    if pdf_path.name in NATIVE_TEXT_FILENAMES
                    else "ocr_v6_medium"
                ),
            }
        )
        document.close()
    write_jsonl(paths.books_manifest, rows)
    print(
        json.dumps(
            {
                "book_count": len(rows),
                "page_count": sum(row["page_count"] for row in rows),
                "ocr_page_count": sum(
                    row["page_count"]
                    for row in rows
                    if row["processing_mode"] == "ocr_v6_medium"
                ),
                "native_page_count": sum(
                    row["page_count"]
                    for row in rows
                    if row["processing_mode"] == "native_text"
                ),
                "manifest": str(paths.books_manifest),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def page_output_path(paths: Paths, book_id: str, physical_page: int) -> Path:
    safe_book_id = book_id.replace(":", "_")
    return paths.page_output_dir / safe_book_id / f"page_{physical_page:06d}.json"


def page_is_current(path: Path, source_sha256: str, mode: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("source_sha256") == source_sha256
        and payload.get("processing_mode") == mode
        and (
            (
                mode == "native_text"
                and payload.get("text_normalizer_version")
                == NATIVE_TEXT_NORMALIZER_VERSION
            )
            or (
                mode != "native_text"
                and payload.get("model_config_sha256") == model_config_sha256()
            )
        )
    )


def extract_native_page(
    document: fitz.Document, book: dict[str, Any], page_index: int
) -> dict[str, Any]:
    page = document.load_page(page_index)
    text = normalize_native_text(page.get_text("text"))
    quality = {
        "visible_character_count": len([char for char in text if not char.isspace()]),
        "cjk_character_ratio": round(
            len(CJK_PATTERN.findall(text))
            / max(len([char for char in text if not char.isspace()]), 1),
            4,
        ),
        "average_confidence": None,
        "ink_ratio": None,
        "blank_likely": not bool(text),
        "low_confidence": not bool(text),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "book_id": book["book_id"],
        "title": book["title"],
        "filename": book["filename"],
        "source_path": book["source_path"],
        "source_sha256": book["source_sha256"],
        "physical_page": page_index + 1,
        "pdf_page_label": page_label(page, page_index + 1),
        "processing_mode": "native_text",
        "text_normalizer_version": NATIVE_TEXT_NORMALIZER_VERSION,
        "model": None,
        "model_config_sha256": None,
        "reading_direction": "native",
        "text": text,
        "segments": [],
        "quality": quality,
    }


def render_page(page: fitz.Page, dpi: int) -> tuple[np.ndarray, float]:
    zoom = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    if pixmap.n == 4:
        image = image[:, :, :3]
    return image.copy(), image_ink_ratio(image)


def extract_ocr_page(
    pipeline: Any,
    document: fitz.Document,
    book: dict[str, Any],
    page_index: int,
    paths: Paths,
    save_low_confidence_images: bool,
) -> dict[str, Any]:
    page = document.load_page(page_index)
    image, ink_ratio = render_page(page, MODEL_CONFIG["dpi"])
    started = time.perf_counter()
    results = list(pipeline.predict(image))
    elapsed = time.perf_counter() - started
    if len(results) != 1:
        raise RuntimeError(f"Expected one OCR result, got {len(results)}")
    scratch_dir = paths.state_dir / "scratch" / str(os.getpid())
    payload = result_to_payload(results[0], scratch_dir)
    raw_segments = build_segments(payload)
    ordered_segments, direction = order_segments(raw_segments, image.shape[1])
    text = "\n".join(segment["text"] for segment in ordered_segments)
    quality = page_quality(
        text,
        [segment["confidence"] for segment in ordered_segments],
        ink_ratio,
    )

    if save_low_confidence_images and quality["low_confidence"]:
        qa_dir = paths.qa_output_dir / book["book_id"].replace(":", "_")
        qa_dir.mkdir(parents=True, exist_ok=True)
        fitz.Pixmap(
            fitz.csRGB,
            image.shape[1],
            image.shape[0],
            image.tobytes(),
            False,
        ).save(qa_dir / f"page_{page_index + 1:06d}.png")

    return {
        "schema_version": SCHEMA_VERSION,
        "book_id": book["book_id"],
        "title": book["title"],
        "filename": book["filename"],
        "source_path": book["source_path"],
        "source_sha256": book["source_sha256"],
        "physical_page": page_index + 1,
        "pdf_page_label": page_label(page, page_index + 1),
        "processing_mode": "ocr_v6_medium",
        "model": MODEL_NAME,
        "model_config": MODEL_CONFIG,
        "model_config_sha256": model_config_sha256(),
        "reading_direction": direction,
        "rendered_width": image.shape[1],
        "rendered_height": image.shape[0],
        "ocr_elapsed_seconds": round(elapsed, 4),
        "text": text,
        "segments": ordered_segments,
        "quality": quality,
    }


def create_pipeline(device: str, paths: Paths) -> Any:
    os.environ["HOME"] = str(paths.model_home)
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "1")
    from paddleocr import PaddleOCR

    return PaddleOCR(
        device=device,
        use_doc_orientation_classify=MODEL_CONFIG["use_doc_orientation_classify"],
        use_doc_unwarping=MODEL_CONFIG["use_doc_unwarping"],
        use_textline_orientation=MODEL_CONFIG["use_textline_orientation"],
        text_detection_model_name=MODEL_CONFIG["text_detection_model_name"],
        text_recognition_model_name=MODEL_CONFIG["text_recognition_model_name"],
    )


def build_tasks(books: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int]]:
    return [
        (book, page_index)
        for book in books
        for page_index in range(int(book["page_count"]))
    ]


def run_worker(
    paths: Paths,
    device: str,
    shard_index: int,
    shard_count: int,
    limit: int | None,
    save_low_confidence_images: bool,
) -> int:
    books = read_jsonl(paths.books_manifest)
    tasks = [
        task
        for global_index, task in enumerate(build_tasks(books))
        if global_index % shard_count == shard_index
    ]
    if limit is not None:
        tasks = tasks[:limit]

    pipeline = None
    open_documents: dict[str, fitz.Document] = {}
    completed = skipped = failed = 0
    error_rows: list[dict[str, Any]] = []
    started = time.perf_counter()

    try:
        for task_index, (book, page_index) in enumerate(tasks, start=1):
            output_path = page_output_path(paths, book["book_id"], page_index + 1)
            if page_is_current(
                output_path, book["source_sha256"], book["processing_mode"]
            ):
                skipped += 1
                continue

            document = open_documents.get(book["book_id"])
            if document is None:
                document = fitz.open(book["source_path"])
                open_documents[book["book_id"]] = document
            try:
                if book["processing_mode"] == "native_text":
                    payload = extract_native_page(document, book, page_index)
                else:
                    if pipeline is None:
                        pipeline = create_pipeline(device, paths)
                    payload = extract_ocr_page(
                        pipeline,
                        document,
                        book,
                        page_index,
                        paths,
                        save_low_confidence_images,
                    )
                atomic_write_json(output_path, payload)
                completed += 1
            except Exception as exc:
                failed += 1
                error_rows.append(
                    {
                        "book_id": book["book_id"],
                        "filename": book["filename"],
                        "physical_page": page_index + 1,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if task_index % 10 == 0 or task_index == len(tasks):
                print(
                    f"worker={shard_index}/{shard_count} "
                    f"task={task_index}/{len(tasks)} completed={completed} "
                    f"skipped={skipped} failed={failed}",
                    flush=True,
                )
    finally:
        for document in open_documents.values():
            document.close()

    summary = {
        "worker": shard_index,
        "shard_count": shard_count,
        "device": device,
        "task_count": len(tasks),
        "completed": completed,
        "skipped": skipped,
        "failed": failed,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "errors": error_rows,
    }
    atomic_write_json(paths.state_dir / f"worker_{shard_index}.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def expected_page_paths(paths: Paths, books: list[dict[str, Any]]) -> Iterable[Path]:
    for book in books:
        for physical_page in range(1, int(book["page_count"]) + 1):
            yield page_output_path(paths, book["book_id"], physical_page)


def finalize(paths: Paths) -> int:
    books = read_jsonl(paths.books_manifest)
    missing = [str(path) for path in expected_page_paths(paths, books) if not path.exists()]
    if missing:
        print(json.dumps({"missing_count": len(missing), "sample": missing[:20]}, indent=2))
        return 1

    pages: list[dict[str, Any]] = []
    for path in expected_page_paths(paths, books):
        pages.append(json.loads(path.read_text(encoding="utf-8")))
    write_jsonl(paths.pages_jsonl, pages)

    temporary_db = paths.database.with_suffix(".db.tmp")
    if temporary_db.exists():
        temporary_db.unlink()
    connection = sqlite3.connect(temporary_db)
    connection.executescript(
        """
        PRAGMA journal_mode = DELETE;
        CREATE TABLE books (
            book_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            processing_mode TEXT NOT NULL
        );
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            physical_page INTEGER NOT NULL,
            pdf_page_label TEXT,
            text TEXT NOT NULL,
            reading_direction TEXT NOT NULL,
            average_confidence REAL,
            low_confidence INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(book_id, physical_page),
            FOREIGN KEY(book_id) REFERENCES books(book_id)
        );
        CREATE VIRTUAL TABLE pages_fts USING fts5(
            page_id UNINDEXED,
            book_id UNINDEXED,
            title,
            text,
            tokenize='unicode61'
        );
        """
    )
    for book in books:
        connection.execute(
            "INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                book["book_id"],
                book["title"],
                book["filename"],
                book["source_path"],
                book["source_sha256"],
                book["page_count"],
                book["processing_mode"],
            ),
        )
    for page in pages:
        page_id = f"{page['book_id']}:p{page['physical_page']:06d}"
        confidence = page["quality"].get("average_confidence")
        connection.execute(
            "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                page_id,
                page["book_id"],
                page["physical_page"],
                page.get("pdf_page_label"),
                page["text"],
                page["reading_direction"],
                confidence,
                int(page["quality"]["low_confidence"]),
                json.dumps(page, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO pages_fts VALUES (?, ?, ?, ?)",
            (page_id, page["book_id"], page["title"], page["text"]),
        )
    connection.commit()
    quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    connection.close()
    temporary_db.replace(paths.database)

    summary = {
        "book_count": len(books),
        "page_count": len(pages),
        "low_confidence_page_count": sum(
            page["quality"]["low_confidence"] for page in pages
        ),
        "pages_jsonl": str(paths.pages_jsonl),
        "database": str(paths.database),
        "database_quick_check": quick_check,
    }
    atomic_write_json(paths.data_dir / "finalize_report.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def doctor(paths: Paths, deep: bool) -> int:
    books = read_jsonl(paths.books_manifest)
    source_issues: list[dict[str, Any]] = []
    if deep:
        for book in books:
            path = Path(book["source_path"])
            actual_sha256 = file_sha256(path) if path.exists() else None
            if actual_sha256 != book["source_sha256"]:
                source_issues.append(
                    {
                        "book_id": book["book_id"],
                        "expected": book["source_sha256"],
                        "actual": actual_sha256,
                    }
                )
    expected = list(expected_page_paths(paths, books))
    existing = [path for path in expected if path.exists()]
    low_confidence = 0
    invalid_pages: list[str] = []
    for path in existing:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            low_confidence += int(payload["quality"]["low_confidence"])
        except Exception:
            invalid_pages.append(str(path))

    db_counts = None
    db_quick_check = None
    if paths.database.exists():
        connection = sqlite3.connect(paths.database)
        db_counts = {
            "books": connection.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "pages": connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
            "fts_rows": connection.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0],
        }
        db_quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        connection.close()

    report = {
        "book_count": len(books),
        "expected_page_count": len(expected),
        "completed_page_count": len(existing),
        "missing_page_count": len(expected) - len(existing),
        "invalid_page_count": len(invalid_pages),
        "low_confidence_page_count": low_confidence,
        "source_sha256_issues": source_issues,
        "database_counts": db_counts,
        "database_quick_check": db_quick_check,
    }
    report["healthy"] = (
        not source_issues
        and not invalid_pages
        and len(existing) == len(expected)
        and (
            db_counts is None
            or (
                db_counts["books"] == len(books)
                and db_counts["pages"] == len(expected)
                and db_counts["fts_rows"] == len(expected)
                and db_quick_check == "ok"
            )
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 1


def source(paths: Paths, book_id: str, physical_page: int) -> int:
    if paths.database.exists():
        connection = sqlite3.connect(paths.database)
        row = connection.execute(
            """
            SELECT b.title, b.filename, p.physical_page, p.pdf_page_label,
                   p.text, p.reading_direction, p.average_confidence
            FROM pages p JOIN books b USING(book_id)
            WHERE p.book_id = ? AND p.physical_page = ?
            """,
            (book_id, physical_page),
        ).fetchone()
        connection.close()
        if row:
            print(
                json.dumps(
                    {
                        "book_id": book_id,
                        "title": row[0],
                        "filename": row[1],
                        "physical_page": row[2],
                        "pdf_page_label": row[3],
                        "reading_direction": row[5],
                        "average_confidence": row[6],
                        "text": row[4],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    output_path = page_output_path(paths, book_id, physical_page)
    if not output_path.exists():
        print(f"Page not found: {book_id} physical page {physical_page}", file=sys.stderr)
        return 1
    print(output_path.read_text(encoding="utf-8"))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ancient Chinese medicine OCR pipeline")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model-home", type=Path, default=DEFAULT_MODEL_HOME)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inventory")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--device", default="gpu:0")
    run_parser.add_argument("--shard-index", type=int, required=True)
    run_parser.add_argument("--shard-count", type=int, required=True)
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--save-low-confidence-images", action="store_true")
    subparsers.add_parser("finalize")
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--deep", action="store_true")
    source_parser = subparsers.add_parser("source")
    source_parser.add_argument("--book-id", required=True)
    source_parser.add_argument("--page", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = Paths(
        corpus_dir=args.corpus_dir.resolve(),
        data_dir=args.data_dir.resolve(),
        output_dir=args.output_dir.resolve(),
        model_home=args.model_home.resolve(),
    )
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "inventory":
        return inventory(paths)
    if args.command == "run":
        if args.shard_count <= 0 or not 0 <= args.shard_index < args.shard_count:
            raise ValueError("Invalid shard index/count")
        return run_worker(
            paths,
            args.device,
            args.shard_index,
            args.shard_count,
            args.limit,
            args.save_low_confidence_images,
        )
    if args.command == "finalize":
        return finalize(paths)
    if args.command == "doctor":
        return doctor(paths, args.deep)
    if args.command == "source":
        return source(paths, args.book_id, args.page)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
