from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .ids import (
    doi_validation_reason,
    is_valid_doi,
    make_doc_id,
    normalize_doi,
)
from .inventory import load_mapping
from .io_utils import read_jsonl, write_csv, write_jsonl
from .text_utils import detect_document_language, detect_language


DOI_CANDIDATE_RE = re.compile(
    r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE
)


def _doi_candidates(text: str) -> list[str]:
    found: list[str] = []
    for match in DOI_CANDIDATE_RE.findall(text or ""):
        candidate = normalize_doi(match.rstrip(".,;:)"))
        if re.search(r"\.(?:g|t|s|fig|table)\d+$", candidate):
            continue
        if candidate not in found:
            found.append(candidate)
    return found


def repair_metadata(
    cfg: dict[str, Any],
    logger,
    *,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """修复 DOI、题名、稳定 ID 和语言字段，不改写原始 PDF。"""
    paths = cfg["paths"]
    docs = read_jsonl(paths["documents_jsonl"])
    pages = read_jsonl(paths["pages_jsonl"])
    overrides = {
        sha.lower(): normalize_doi(doi)
        for sha, doi in (cfg.get("doi_overrides") or {}).items()
    }
    title_overrides = {
        sha.lower(): str(title).strip()
        for sha, title in (cfg.get("title_overrides") or {}).items()
        if str(title).strip()
    }
    mapping = load_mapping(paths["mapping_csv"])

    pages_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        pages_by_sha[str(page.get("sha256") or "").lower()].append(page)

    selected = docs
    if doc_id:
        selected = [d for d in docs if d.get("doc_id") == doc_id]
    if limit:
        selected = selected[:limit]
    selected_shas = {str(d.get("sha256") or "").lower() for d in selected}

    audit: list[dict[str, Any]] = []
    proposed: dict[str, str] = {}
    status_by_sha: dict[str, str] = {}
    original_by_sha: dict[str, str] = {}

    for doc in docs:
        sha = str(doc.get("sha256") or "").lower()
        current_doi = normalize_doi(doc.get("doi") or "")
        meta = mapping.get(doc.get("source_filename") or "") or {}
        mapping_doi = normalize_doi(meta.get("doi") or "")
        preserved = normalize_doi(doc.get("doi_original") or "")
        if preserved and preserved != current_doi and not is_valid_doi(preserved):
            original = preserved
        elif mapping_doi and mapping_doi != current_doi and not is_valid_doi(mapping_doi):
            original = mapping_doi
        elif preserved and preserved != current_doi:
            original = preserved
        else:
            original = mapping_doi or preserved or current_doi
        original_by_sha[sha] = original
        sample = "\n".join(
            p.get("text") or ""
            for p in sorted(
                pages_by_sha.get(sha, []), key=lambda p: int(p["pdf_page"])
            )[:4]
        )
        candidates = _doi_candidates(sample)
        override = overrides.get(sha, "")
        if override:
            if not is_valid_doi(override):
                raise ValueError(f"配置的 DOI 修正仍不完整: {sha} -> {override}")
            final = override
            # overrides 仅用于截断 DOI；即使原文字段已被覆盖，也保持修正溯源。
            if (not original or original == final or is_valid_doi(original)) and mapping_doi and mapping_doi != final:
                original = mapping_doi
            original_by_sha[sha] = original or final
            status = "corrected_from_pdf_text"
        elif current_doi and is_valid_doi(current_doi):
            final = current_doi
            status = "valid"
        elif current_doi:
            final = ""
            status = "incomplete_cleared"
        else:
            final = ""
            status = "missing"
        proposed[sha] = final
        status_by_sha[sha] = status
        audit.append(
            {
                "source_filename": doc.get("source_filename") or "",
                "sha256": sha,
                "title": doc.get("title") or "",
                "original_doi": original_by_sha[sha],
                "final_doi": final,
                "status": status,
                "validation_reason": doi_validation_reason(original_by_sha[sha]),
                "pdf_text_candidates": candidates,
            }
        )

    # 同一完整 DOI 若映射到不同 SHA/标题，所有冲突文献均回退 SHA doc_id。
    by_doi: dict[str, list[dict[str, Any]]] = defaultdict(list)
    docs_by_sha = {str(d.get("sha256") or "").lower(): d for d in docs}
    for sha, doi in proposed.items():
        if doi:
            by_doi[doi].append(docs_by_sha[sha])
    conflict_dois: dict[str, list[str]] = {}
    for doi, members in by_doi.items():
        shas = {str(d.get("sha256") or "").lower() for d in members}
        titles = {str(d.get("title") or "").strip().casefold() for d in members}
        if len(shas) > 1 or len(titles) > 1:
            conflict_dois[doi] = sorted(shas)
            for sha in shas:
                status_by_sha[sha] = "conflict_sha_doc_id"

    old_to_new: dict[str, str] = {}
    changed_ids = 0
    changed_dois = 0
    changed_languages = 0
    changed_titles = 0
    repaired_docs: list[dict[str, Any]] = []

    for doc in docs:
        sha = str(doc.get("sha256") or "").lower()
        if selected_shas and sha not in selected_shas:
            repaired_docs.append(doc)
            continue
        old_id = doc["doc_id"]
        old_title = str(doc.get("title") or "").strip()
        new_title = title_overrides.get(sha, old_title)
        if new_title != old_title:
            changed_titles += 1
        final_doi = proposed[sha]
        use_doi = final_doi if final_doi not in conflict_dois else ""
        new_id = make_doc_id(use_doi, sha)
        old_to_new[old_id] = new_id
        if new_id != old_id:
            changed_ids += 1
        if normalize_doi(doc.get("doi") or "") != final_doi:
            changed_dois += 1

        doc_pages = sorted(
            pages_by_sha.get(sha, []), key=lambda p: int(p["pdf_page"])
        )
        new_language = detect_document_language(
            [p.get("text") or "" for p in doc_pages], new_title
        )
        if new_language != (doc.get("language") or ""):
            changed_languages += 1

        notes = [
            n
            for n in (doc.get("field_notes") or [])
            if not str(n).startswith(("doi_", "language_", "title_"))
        ]
        status = status_by_sha[sha]
        if status != "valid":
            notes.append(f"doi_{status}")
        if new_title != old_title:
            notes.append("title_corrected_from_pdf_first_page")
        doc.update(
            {
                "doc_id": new_id,
                "doc_id_preferred": make_doc_id(final_doi, sha)
                if final_doi
                else new_id,
                "doi_original": original_by_sha[sha],
                "doi": final_doi,
                "doi_status": status,
                "doi_evidence": "pdf_text_first_4_pages"
                if status == "corrected_from_pdf_text"
                else "",
                "title_original": old_title if new_title != old_title else doc.get("title_original", ""),
                "title": new_title,
                "title_status": "corrected_from_pdf_first_page"
                if new_title != old_title
                else doc.get("title_status", "valid"),
                "language": new_language,
                "field_notes": notes,
            }
        )
        repaired_docs.append(doc)

    docs_by_sha = {str(d.get("sha256") or "").lower(): d for d in repaired_docs}
    repaired_pages: list[dict[str, Any]] = []
    for page in pages:
        sha = str(page.get("sha256") or "").lower()
        doc = docs_by_sha.get(sha)
        if not doc:
            raise ValueError(f"页面引用未知 SHA-256: {sha}")
        page.update(
            {
                "doc_id": doc["doc_id"],
                "title": doc.get("title") or "",
                "year": doc.get("year") or "",
                "doi": doc.get("doi") or "",
                "language": detect_language(page.get("text") or ""),
            }
        )
        repaired_pages.append(page)

    # 只有全量修复才覆盖全量输出，避免单篇迁移破坏引用关系。
    if doc_id or limit:
        logger.warning("repair 的 --doc-id/--limit 仅审计；全量文件未覆盖")
        return {
            "audited": len(selected),
            "changed_ids": changed_ids,
            "changed_dois": changed_dois,
            "changed_languages": changed_languages,
            "changed_titles": changed_titles,
            "conflict_dois": conflict_dois,
            "written": False,
        }

    fields = [
        "doc_id",
        "title",
        "year",
        "doi",
        "doi_original",
        "doi_status",
        "doi_evidence",
        "source_filename",
        "sha256",
        "page_count",
        "language",
        "has_text_layer",
        "total_text_chars",
        "empty_page_count",
        "extraction_status",
        "extraction_error",
        "relevance_score",
        "topic_tags",
        "relevance_evidence",
        "field_notes",
        "source_path",
    ]
    write_jsonl(paths["documents_jsonl"], repaired_docs)
    write_csv(paths["documents_csv"], repaired_docs, fields)
    write_jsonl(paths["pages_jsonl"], repaired_pages)
    Path(paths["doi_audit"]).write_text(
        json.dumps(
            {
                "documents_checked": len(audit),
                "corrected": sum(
                    a["status"] == "corrected_from_pdf_text" for a in audit
                ),
                "incomplete_cleared": sum(
                    a["status"] == "incomplete_cleared" for a in audit
                ),
                "conflicts": conflict_dois,
                "records": audit,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    state_dir = Path(paths["state_dir"])
    write_jsonl(
        state_dir / "inventory_done.jsonl",
        [{"doc_id": d["doc_id"], "stage": "inventory"} for d in repaired_docs],
    )
    write_jsonl(
        state_dir / "extract_done.jsonl",
        [
            {
                "doc_id": d["doc_id"],
                "status": d.get("extraction_status"),
                "pages": d.get("page_count"),
            }
            for d in repaired_docs
        ],
    )
    logger.info(
        "质量修复完成: DOI修正=%s ID迁移=%s 语言变化=%s 题名修正=%s DOI冲突=%s",
        changed_dois,
        changed_ids,
        changed_languages,
        changed_titles,
        len(conflict_dois),
    )
    return {
        "audited": len(audit),
        "changed_ids": changed_ids,
        "changed_dois": changed_dois,
        "changed_languages": changed_languages,
        "changed_titles": changed_titles,
        "conflict_dois": conflict_dois,
        "written": True,
    }
