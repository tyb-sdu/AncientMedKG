from __future__ import annotations

from pathlib import Path
from typing import Any

from .text_utils import clean_page_text, detect_language, is_garbled


class PdfExtractError(Exception):
    def __init__(self, message: str, needs_ocr: bool = False, encrypted: bool = False):
        super().__init__(message)
        self.needs_ocr = needs_ocr
        self.encrypted = encrypted


def _extract_pymupdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import fitz

    try:
        doc = fitz.open(path)
    except Exception as e:  # noqa: BLE001
        raise PdfExtractError(f"pymupdf_open_failed: {e}") from e

    try:
        if doc.is_encrypted:
            # 尝试空密码
            auth = doc.authenticate("")
            if not auth and doc.needs_pass:
                raise PdfExtractError("pdf_encrypted", encrypted=True)

        pages: list[dict[str, Any]] = []
        for i in range(doc.page_count):
            page = doc.load_page(i)
            try:
                raw = page.get_text("text") or ""
                method = "pymupdf"
            except Exception as e:  # noqa: BLE001
                raw = ""
                method = f"pymupdf_error:{e}"
            label = ""
            try:
                label = str(page.get_label() or "")
            except Exception:  # noqa: BLE001
                label = ""
            pages.append(
                {
                    "pdf_page": i + 1,
                    "page_label": label,
                    "raw_text": raw,
                    "extraction_method": method,
                }
            )
        meta = {
            "page_count": doc.page_count,
            "engine": "pymupdf",
            "is_encrypted": bool(doc.is_encrypted),
        }
        return pages, meta
    finally:
        doc.close()


def _extract_pypdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as e:  # noqa: BLE001
        raise PdfExtractError(f"pypdf_open_failed: {e}") from e

    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")
            if not ok:
                raise PdfExtractError("pdf_encrypted", encrypted=True)
        except Exception as e:  # noqa: BLE001
            raise PdfExtractError("pdf_encrypted", encrypted=True) from e

    pages: list[dict[str, Any]] = []
    for i, page in enumerate(reader.pages):
        try:
            raw = page.extract_text() or ""
            method = "pypdf"
        except Exception as e:  # noqa: BLE001
            raw = ""
            method = f"pypdf_error:{e}"
        pages.append(
            {
                "pdf_page": i + 1,
                "page_label": "",
                "raw_text": raw,
                "extraction_method": method,
            }
        )
    return pages, {"page_count": len(reader.pages), "engine": "pypdf", "is_encrypted": False}


def _extract_pdfplumber(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import pdfplumber

    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:  # noqa: BLE001
        raise PdfExtractError(f"pdfplumber_open_failed: {e}") from e

    try:
        pages: list[dict[str, Any]] = []
        for i, page in enumerate(pdf.pages):
            try:
                raw = page.extract_text() or ""
                method = "pdfplumber"
            except Exception as e:  # noqa: BLE001
                raw = ""
                method = f"pdfplumber_error:{e}"
            pages.append(
                {
                    "pdf_page": i + 1,
                    "page_label": "",
                    "raw_text": raw,
                    "extraction_method": method,
                }
            )
        return pages, {
            "page_count": len(pdf.pages),
            "engine": "pdfplumber",
            "is_encrypted": False,
        }
    finally:
        pdf.close()


def extract_pdf_pages(
    pdf_path: str | Path,
    preferred_engine: str = "pymupdf",
    empty_threshold: int = 30,
) -> dict[str, Any]:
    path = Path(pdf_path)
    engines = []
    preferred = (preferred_engine or "pymupdf").lower()
    for name in (preferred, "pymupdf", "pypdf", "pdfplumber"):
        if name not in engines:
            engines.append(name)

    last_err: Exception | None = None
    pages_raw: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {}

    for engine in engines:
        try:
            if engine == "pymupdf":
                pages_raw, meta = _extract_pymupdf(path)
            elif engine == "pypdf":
                pages_raw, meta = _extract_pypdf(path)
            elif engine == "pdfplumber":
                pages_raw, meta = _extract_pdfplumber(path)
            else:
                continue
            break
        except PdfExtractError:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    if pages_raw is None:
        raise PdfExtractError(f"all_engines_failed: {last_err}")

    pages: list[dict[str, Any]] = []
    total_chars = 0
    empty_count = 0
    quality_flags_doc: list[str] = []

    for p in pages_raw:
        cleaned = clean_page_text(p.get("raw_text") or "")
        char_count = len(cleaned)
        is_empty = char_count < empty_threshold
        flags: list[str] = []
        if is_empty:
            empty_count += 1
            flags.append("empty_or_near_empty")
        if is_garbled(cleaned):
            flags.append("garbled_text")
        lang = detect_language(cleaned)
        total_chars += char_count
        pages.append(
            {
                "pdf_page": p["pdf_page"],
                "page_label": p.get("page_label") or "",
                "text": cleaned,
                "extraction_method": p.get("extraction_method") or meta.get("engine"),
                "text_char_count": char_count,
                "is_empty": is_empty,
                "quality_flags": flags,
                "language": lang,
            }
        )

    page_count = meta.get("page_count") or len(pages)
    empty_ratio = (empty_count / page_count) if page_count else 1.0
    has_text_layer = total_chars > 0 and empty_ratio < 0.95
    status = "ok"
    error = ""
    if page_count == 0:
        status = "failed"
        error = "zero_pages"
    elif total_chars == 0:
        status = "needs_ocr"
        error = "no_text_layer"
        quality_flags_doc.append("needs_ocr")
        has_text_layer = False
    elif empty_ratio >= 0.80:
        status = "needs_ocr"
        error = "high_empty_page_ratio"
        quality_flags_doc.append("needs_ocr")

    return {
        "pages": pages,
        "page_count": page_count,
        "total_text_chars": total_chars,
        "empty_page_count": empty_count,
        "has_text_layer": has_text_layer,
        "extraction_status": status,
        "extraction_error": error,
        "engine": meta.get("engine"),
        "quality_flags": quality_flags_doc,
        "language": detect_language("\n".join(p["text"] for p in pages[:5])),
    }
