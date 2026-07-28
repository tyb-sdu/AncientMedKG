from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from .ids import is_valid_doi, make_doc_id, normalize_doi, sha256_file
from .topics import score_topics


YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def load_mapping(mapping_csv: str | Path) -> dict[str, dict[str, str]]:
    """按 new_filename / original_filename 建立元数据索引。"""
    path = Path(mapping_csv)
    by_name: dict[str, dict[str, str]] = {}
    if not path.exists():
        return by_name

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            meta = {
                "title": (row.get("title") or "").strip(),
                "year": (row.get("year") or "").strip(),
                "doi": normalize_doi(row.get("doi") or ""),
                "year_source": (row.get("year_source") or "").strip(),
                "title_source": (row.get("title_source") or "").strip(),
                "original_filename": (row.get("original_filename") or "").strip(),
                "new_filename": (row.get("new_filename") or "").strip(),
            }
            for key in ("new_filename", "original_filename"):
                name = meta.get(key) or ""
                if name:
                    by_name[name] = meta
                    by_name[Path(name).name] = meta
    return by_name


def list_pdfs(pdf_dir: str | Path) -> list[Path]:
    root = Path(pdf_dir)
    files = sorted(root.glob("*.pdf")) + sorted(root.glob("*.PDF"))
    # 去重（大小写）
    seen: set[str] = set()
    out: list[Path] = []
    for p in files:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def parse_year_from_filename(name: str) -> str:
    m = re.match(r"^((?:19|20)\d{2})[_-]", name)
    return m.group(1) if m else ""


def build_document_record(
    pdf_path: Path,
    mapping: dict[str, dict[str, str]],
    sha256: str | None = None,
) -> dict[str, Any]:
    filename = pdf_path.name
    meta = mapping.get(filename) or {}
    digest = sha256 or sha256_file(pdf_path)

    title = meta.get("title") or ""
    year = meta.get("year") or ""
    doi = normalize_doi(meta.get("doi") or "")
    field_notes: list[str] = []

    if not title:
        # 不编造：仅从文件名去掉扩展名作为候补标记为空，并记录原因
        title = ""
        field_notes.append("title_missing_in_mapping")
    if not year:
        y = parse_year_from_filename(filename)
        if y:
            year = y
            field_notes.append("year_from_filename")
        else:
            field_notes.append("year_missing")
    elif not YEAR_RE.match(str(year)):
        field_notes.append("year_format_invalid")
        year = ""

    if doi and not is_valid_doi(doi):
        field_notes.append("doi_format_invalid")
        doi_for_id = ""
    else:
        doi_for_id = doi
    if not doi:
        field_notes.append("doi_missing")

    doc_id = make_doc_id(doi_for_id, digest)
    topic = score_topics(title or filename, title=title or "")

    return {
        "doc_id": doc_id,
        "doc_id_preferred": doc_id,
        "title": title,
        "year": year,
        "doi": doi if is_valid_doi(doi) else "",
        "source_filename": filename,
        "source_path": str(pdf_path.resolve()),
        "sha256": digest,
        "page_count": "",
        "language": "",
        "has_text_layer": "",
        "total_text_chars": "",
        "empty_page_count": "",
        "extraction_status": "pending",
        "extraction_error": "",
        "relevance_score": topic["relevance_score"],
        "topic_tags": topic["topic_tags"],
        "relevance_evidence": topic["relevance_evidence"],
        "field_notes": field_notes,
        "mapping_title_source": meta.get("title_source") or "",
        "mapping_year_source": meta.get("year_source") or "",
    }
