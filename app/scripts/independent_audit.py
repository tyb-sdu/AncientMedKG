#!/usr/bin/env python
from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_prep.config import load_config
from rag_prep.ids import is_valid_doi, make_chunk_id, make_doc_id, sha256_file
from rag_prep.io_utils import read_jsonl
from rag_prep.search import normalize_search_text, source_page
from rag_prep.text_utils import clean_page_text, detect_document_language, is_garbled


SEED = 20260727
SAMPLE_SIZE = 30


def _sample_pages(page_count: int) -> list[int]:
    if page_count <= 0:
        return []
    return sorted({1, (page_count + 1) // 2, page_count})


def _select_documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    selected: dict[str, dict[str, Any]] = {}

    def add(rows: list[dict[str, Any]], count: int) -> None:
        candidates = [row for row in rows if row["doc_id"] not in selected]
        rng.shuffle(candidates)
        for row in candidates[:count]:
            selected[row["doc_id"]] = row

    add([d for d in docs if d.get("language") == "zh"], 1)
    add([d for d in docs if d.get("doi_status") == "corrected_from_pdf_text"], 6)
    add([d for d in docs if not d.get("doi")], 4)
    add(
        [
            d
            for d in docs
            if int(d.get("relevance_score") or 0) <= 25
            or "off_topic" in (d.get("topic_tags") or [])
        ],
        5,
    )
    add([d for d in docs if int(d.get("page_count") or 0) >= 30], 4)
    add([d for d in docs if 0 < int(d.get("page_count") or 0) <= 5], 4)
    add([d for d in docs if len(str(d.get("title") or "")) < 5], 1)
    add([d for d in docs if int(d.get("relevance_score") or 0) >= 75], 5)
    add(docs, SAMPLE_SIZE - len(selected))
    return list(selected.values())[:SAMPLE_SIZE]


def _normalized_core_is_in_page(chunk: dict[str, Any], page_text: str) -> bool:
    start = int(chunk.get("char_start") or 0)
    end = int(chunk.get("char_end") or 0)
    if start < 0 or end < start or end > len(page_text):
        return False
    core = normalize_search_text(page_text[start:end])
    body = normalize_search_text(chunk.get("text") or "")
    return bool(core) and core in body


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    docs = read_jsonl(cfg["paths"]["documents_jsonl"])
    pages = read_jsonl(cfg["paths"]["pages_jsonl"])
    chunks = read_jsonl(cfg["paths"]["chunks_jsonl"])
    pages_by_doc: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    chunks_by_doc_page: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        pages_by_doc[page["doc_id"]][int(page["pdf_page"])] = page
    for chunk in chunks:
        chunks_by_doc_page[(chunk["doc_id"], int(chunk["pdf_page"]))].append(chunk)

    selected = _select_documents(docs)
    records: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()

    for doc in selected:
        doc_id = doc["doc_id"]
        source = Path(doc["source_path"])
        issues: list[str] = []
        page_checks: list[dict[str, Any]] = []

        if not source.is_file():
            issues.append("source_pdf_missing")
            direct_page_count = 0
        else:
            if sha256_file(str(source)) != doc["sha256"]:
                issues.append("source_sha256_mismatch")
            pdf = fitz.open(source)
            direct_page_count = pdf.page_count
            if direct_page_count != int(doc.get("page_count") or 0):
                issues.append("pdf_page_count_mismatch")

            for pdf_page in _sample_pages(direct_page_count):
                stored = pages_by_doc[doc_id].get(pdf_page)
                fresh = clean_page_text(pdf.load_page(pdf_page - 1).get_text("text") or "")
                check: dict[str, Any] = {
                    "pdf_page": pdf_page,
                    "fresh_chars": len(fresh),
                    "stored_chars": len(stored.get("text") or "") if stored else None,
                    "chunk_count": 0,
                    "issues": [],
                }
                if not stored:
                    check["issues"].append("stored_page_missing")
                else:
                    if fresh != (stored.get("text") or ""):
                        check["issues"].append("fresh_text_mismatch")
                    db_page = source_page(cfg, doc_id, pdf_page)
                    if not db_page or db_page.get("text") != stored.get("text"):
                        check["issues"].append("source_command_mismatch")

                    page_chunks = sorted(
                        chunks_by_doc_page[(doc_id, pdf_page)],
                        key=lambda row: int(row.get("chunk_index") or 0),
                    )
                    check["chunk_count"] = len(page_chunks)
                    if fresh.strip() and not page_chunks:
                        check["issues"].append("chunks_missing_for_text_page")
                    for index, chunk in enumerate(page_chunks):
                        if chunk["doc_id"] != doc_id or int(chunk["pdf_page"]) != pdf_page:
                            check["issues"].append("chunk_cross_page")
                        expected_id = make_chunk_id(doc_id, pdf_page, index)
                        if chunk["chunk_id"] != expected_id:
                            check["issues"].append("chunk_id_unstable")
                        if not _normalized_core_is_in_page(chunk, stored["text"]):
                            check["issues"].append("chunk_not_locatable_in_page")
                check["issues"] = sorted(set(check["issues"]))
                issues.extend(check["issues"])
                page_checks.append(check)
            pdf.close()

        expected_doc_id = make_doc_id(doc.get("doi") or "", doc["sha256"])
        if doc_id != expected_doc_id:
            issues.append("doc_id_unstable")
        if doc.get("doi") and not is_valid_doi(doc["doi"]):
            issues.append("doi_invalid")
        doc_pages = [
            pages_by_doc[doc_id][page]
            for page in sorted(pages_by_doc[doc_id])
        ]
        detected = detect_document_language(
            [page.get("text") or "" for page in doc_pages],
            doc.get("title") or "",
        )
        if detected != (doc.get("language") or ""):
            issues.append("document_language_mismatch")
        if any(is_garbled(page.get("text") or "") for page in doc_pages):
            issues.append("garbled_text")
        if len(str(doc.get("title") or "").strip()) < 5:
            issues.append("title_abnormal")

        issues = sorted(set(issues))
        issue_counts.update(issues)
        records.append(
            {
                "doc_id": doc_id,
                "title": doc.get("title") or "",
                "year": doc.get("year") or "",
                "doi": doc.get("doi") or "",
                "doi_status": doc.get("doi_status") or "",
                "source_filename": doc.get("source_filename") or "",
                "sha256": doc.get("sha256") or "",
                "language": doc.get("language") or "",
                "page_count": int(doc.get("page_count") or 0),
                "direct_pdf_page_count": direct_page_count,
                "relevance_score": int(doc.get("relevance_score") or 0),
                "topic_tags": doc.get("topic_tags") or [],
                "sampled_pages": page_checks,
                "issues": issues,
                "passed": not issues,
            }
        )

    report = {
        "audit_version": 1,
        "seed": SEED,
        "sample_size": len(records),
        "sampled_page_count": sum(len(r["sampled_pages"]) for r in records),
        "passed_documents": sum(r["passed"] for r in records),
        "failed_documents": sum(not r["passed"] for r in records),
        "issue_counts": dict(sorted(issue_counts.items())),
        "records": records,
    }
    out_json = ROOT / "data" / "independent_audit_30.json"
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    out_md = ROOT / "data" / "independent_audit_30.md"
    lines = [
        "# 现代文献独立抽检（30 篇）",
        "",
        f"- 固定随机种子：`{SEED}`",
        f"- 抽检文献：{report['sample_size']}",
        f"- 抽检物理页：{report['sampled_page_count']}",
        f"- 通过/失败：{report['passed_documents']} / {report['failed_documents']}",
        f"- 问题计数：`{json.dumps(report['issue_counts'], ensure_ascii=False)}`",
        "",
    ]
    for index, record in enumerate(records, 1):
        lines.extend(
            [
                f"## {index}. {record['title'] or record['source_filename']}",
                f"- doc_id: `{record['doc_id']}`",
                f"- 文件: `{record['source_filename']}`",
                f"- DOI/语言/相关分: `{record['doi'] or '(无)'}` / `{record['language']}` / `{record['relevance_score']}`",
                f"- 原 PDF 页数/记录页数: {record['direct_pdf_page_count']} / {record['page_count']}",
                f"- 结果: {'通过' if record['passed'] else '失败：' + ', '.join(record['issues'])}",
            ]
        )
        for page in record["sampled_pages"]:
            lines.append(
                f"- p{page['pdf_page']}: fresh/stored={page['fresh_chars']}/{page['stored_chars']}, "
                f"chunks={page['chunk_count']}, issues={page['issues']}"
            )
        lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "records"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(out_json)
    print(out_md)
    return 0 if not issue_counts else 1


if __name__ == "__main__":
    raise SystemExit(main())
