from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


BURN_TERMS = (
    "burn",
    "burns",
    "burn wound",
    "thermal injury",
    "thermal wound",
    "scald",
    "烧伤",
    "燒傷",
    "烫伤",
    "燙傷",
)
WOUND_TERMS = (
    "wound",
    "wound healing",
    "skin repair",
    "re-epithelialization",
    "reepithelialization",
    "创面",
    "創面",
    "伤口",
    "傷口",
)


def _normalized(text: object) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _pattern(term: str) -> re.Pattern[str]:
    normalized = _normalized(term)
    escaped = re.escape(normalized)
    if re.fullmatch(r"[a-z0-9][a-z0-9 .+()/_-]*", normalized):
        return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
    return re.compile(escaped)


def _patterns(terms: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(_pattern(term) for term in terms if str(term).strip())


def matched_terms(
    text: str,
    terms: Iterable[str],
) -> list[str]:
    normalized = _normalized(text)
    matched = []
    for term in terms:
        if _pattern(term).search(normalized):
            matched.append(str(term))
    return matched


def _first_match_span(
    text: str,
    patterns: Iterable[re.Pattern[str]],
) -> tuple[int, int]:
    matches = [pattern.search(text) for pattern in patterns]
    spans = [match.span() for match in matches if match is not None]
    return min(spans, default=(0, 0), key=lambda span: span[0])


def _snippet(text: str, start: int, end: int, radius: int = 220) -> str:
    left = max(0, start - radius)
    right = min(len(text), max(end, start + 1) + radius)
    return " ".join(text[left:right].split())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _topic_tags(value: object) -> tuple[list[Any], str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], "malformed"
    if not isinstance(parsed, list):
        return [], "malformed"
    return parsed, "valid"


def _open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def scan_corpus(
    database: Path,
    catalog: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    loci_path = output_dir / "compound_loci.jsonl"
    summary_path = output_dir / "compound_coverage_summary.json"
    if loci_path.exists() or summary_path.exists():
        raise FileExistsError(f"refusing to overwrite corpus scan in {output_dir}")
    if not database.is_file():
        raise FileNotFoundError(database)

    database_sha256_before = _sha256_file(database)

    candidates = [dict(value) for value in catalog.get("candidates", [])]
    candidate_patterns: dict[str, tuple[re.Pattern[str], ...]] = {}
    candidate_terms: dict[str, list[str]] = {}
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        terms = [
            str(candidate["canonical_name"]),
            str(candidate.get("name_zh", "")),
            *(str(value) for value in candidate.get("aliases", [])),
        ]
        unique_terms = list(dict.fromkeys(value for value in terms if value.strip()))
        candidate_terms[candidate_id] = unique_terms
        candidate_patterns[candidate_id] = _patterns(unique_terms)

    burn_patterns = tuple(zip(BURN_TERMS, _patterns(BURN_TERMS), strict=True))
    wound_patterns = tuple(zip(WOUND_TERMS, _patterns(WOUND_TERMS), strict=True))
    counts: dict[str, dict[str, Any]] = {
        str(candidate["candidate_id"]): {
            "chunk_count": 0,
            "document_ids": set(),
            "burn_chunk_count": 0,
            "burn_document_ids": set(),
            "wound_chunk_count": 0,
            "wound_document_ids": set(),
        }
        for candidate in candidates
    }
    records: list[dict[str, Any]] = []
    malformed_topic_tag_documents: set[str] = set()
    connection = _open_read_only(database)
    try:
        rows = connection.execute(
            """
            SELECT c.chunk_id, c.doc_id, c.pdf_page, c.text, c.normalized_text,
                   d.title, d.year, d.doi, d.source_filename, d.sha256,
                   d.relevance_score, d.topic_tags
            FROM chunks AS c
            JOIN documents AS d ON d.doc_id = c.doc_id
            ORDER BY c.doc_id, c.pdf_page, c.chunk_index
            """
        )
        for row in rows:
            (
                chunk_id,
                doc_id,
                pdf_page,
                raw_text,
                normalized_text,
                title,
                year,
                doi,
                source_filename,
                source_sha256,
                relevance_score,
                topic_tags,
            ) = row
            searchable = _normalized(normalized_text or raw_text)
            parsed_topic_tags, topic_tags_status = _topic_tags(topic_tags)
            if topic_tags_status == "malformed":
                malformed_topic_tag_documents.add(str(doc_id))
            burn_hits = [
                term for term, pattern in burn_patterns if pattern.search(searchable)
            ]
            wound_hits = [
                term for term, pattern in wound_patterns if pattern.search(searchable)
            ]
            for candidate in candidates:
                candidate_id = str(candidate["candidate_id"])
                patterns = candidate_patterns[candidate_id]
                if not any(pattern.search(searchable) for pattern in patterns):
                    continue
                hit_terms = [
                    term
                    for term, pattern in zip(
                        candidate_terms[candidate_id], patterns, strict=True
                    )
                    if pattern.search(searchable)
                ]
                start, end = _first_match_span(searchable, patterns)
                if burn_hits:
                    context_class = "burn_context"
                elif wound_hits:
                    context_class = "wound_context"
                else:
                    context_class = "compound_only"
                record = {
                    "locus_id": f"locus:{candidate_id}:{chunk_id}",
                    "candidate_id": candidate_id,
                    "matched_terms": hit_terms,
                    "context_class": context_class,
                    "context_terms": burn_hits if burn_hits else wound_hits,
                    "review_status": "pending_full_text_review",
                    "evidence_status": "retrieval_candidate_not_scientific_evidence",
                    "doc_id": doc_id,
                    "title": title,
                    "year": year,
                    "doi": doi,
                    "source_filename": source_filename,
                    "source_sha256": source_sha256,
                    "pdf_page": int(pdf_page),
                    "chunk_id": chunk_id,
                    "chunk_text_sha256": _sha256_text(str(raw_text or "")),
                    "snippet": _snippet(searchable, start, end),
                    "document_relevance_score": relevance_score,
                    "document_topic_tags": parsed_topic_tags,
                    "document_topic_tags_status": topic_tags_status,
                }
                records.append(record)
                stat = counts[candidate_id]
                stat["chunk_count"] += 1
                stat["document_ids"].add(doc_id)
                if context_class == "burn_context":
                    stat["burn_chunk_count"] += 1
                    stat["burn_document_ids"].add(doc_id)
                if context_class in {"burn_context", "wound_context"}:
                    stat["wound_chunk_count"] += 1
                    stat["wound_document_ids"].add(doc_id)
    finally:
        connection.close()

    database_sha256_after = _sha256_file(database)
    if database_sha256_before != database_sha256_after:
        raise RuntimeError("source database changed during corpus scan")

    records.sort(key=lambda value: (value["candidate_id"], value["doc_id"], value["pdf_page"], value["chunk_id"]))
    loci_payload = "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for value in records
    )
    _atomic_write(
        loci_path,
        loci_payload,
    )
    summary_records = []
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        stat = counts[candidate_id]
        summary_records.append(
            {
                "candidate_id": candidate_id,
                "canonical_name": candidate["canonical_name"],
                "herb_ids": candidate.get("herb_ids", []),
                "candidate_role": candidate.get("candidate_role", ""),
                "chunk_count": stat["chunk_count"],
                "document_count": len(stat["document_ids"]),
                "burn_chunk_count": stat["burn_chunk_count"],
                "burn_document_count": len(stat["burn_document_ids"]),
                "wound_or_burn_chunk_count": stat["wound_chunk_count"],
                "wound_or_burn_document_count": len(stat["wound_document_ids"]),
            }
        )
    summary = {
        "schema_version": 1,
        "catalog_id": catalog.get("catalog_id", ""),
        "catalog_sha256": _sha256_json(catalog),
        "candidate_count": len(candidates),
        "database": str(database),
        "database_sha256": database_sha256_before,
        "source_database_unchanged": True,
        "locus_count": len(records),
        "loci_sha256": _sha256_text(loci_payload),
        "data_quality": {
            "malformed_topic_tags_document_count": len(
                malformed_topic_tag_documents
            ),
            "malformed_topic_tags_document_ids": sorted(
                malformed_topic_tag_documents
            ),
        },
        "scientific_boundary": (
            "Counts are retrieval candidates only. C3 evidence requires full-text "
            "review, study-type grading, and approved page-level evidence."
        ),
        "candidates": summary_records,
    }
    _atomic_write(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return summary
