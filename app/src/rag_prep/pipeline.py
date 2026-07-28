from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .chunk import chunk_page_text
from .extract import PdfExtractError, extract_pdf_pages
from .ids import is_valid_doi, make_doc_id, normalize_doi, sha256_file
from .inventory import build_document_record, list_pdfs, load_mapping
from .io_utils import (
    append_jsonl,
    iter_jsonl,
    load_done_ids,
    mark_done,
    read_jsonl,
    remove_state_ids,
    rewrite_jsonl_excluding,
    write_csv,
    write_jsonl,
    write_jsonl_atomic,
)
from .logging_utils import progress_line
from .text_utils import is_garbled
from .topics import score_topics


DOC_FIELDS = [
    "doc_id",
    "title",
    "year",
    "doi",
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


def _state_path(cfg: dict[str, Any], stage: str) -> Path:
    return Path(cfg["paths"]["state_dir"]) / f"{stage}_done.jsonl"


def compute_source_checksums(
    cfg: dict[str, Any],
    out_path: str | Path,
    logger,
) -> list[dict[str, Any]]:
    pdfs = list_pdfs(cfg["paths"]["modern_pdf_dir"])
    rows: list[dict[str, Any]] = []
    total = len(pdfs)
    for i, p in enumerate(pdfs, 1):
        rows.append(
            {
                "source_filename": p.name,
                "source_path": str(p.resolve()),
                "sha256": sha256_file(p),
                "size_bytes": p.stat().st_size,
            }
        )
        if i % 50 == 0 or i == total:
            logger.info(progress_line("checksum", i, total, extra=p.name[:60]))
    write_jsonl(out_path, rows, append=False)
    return rows


def verify_source_unchanged(cfg: dict[str, Any], logger) -> dict[str, Any]:
    before_path = Path(cfg["paths"]["source_checksums_before"])
    after_path = Path(cfg["paths"]["source_checksums_after"])
    if not before_path.exists():
        raise FileNotFoundError("缺少处理前校验文件，请先运行 inventory")
    before = {r["source_filename"]: r["sha256"] for r in read_jsonl(before_path)}
    after_rows = compute_source_checksums(cfg, after_path, logger)
    after = {r["source_filename"]: r["sha256"] for r in after_rows}
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = sorted(n for n in set(before) & set(after) if before[n] != after[n])
    result = {
        "before_count": len(before),
        "after_count": len(after),
        "missing": missing,
        "added": added,
        "changed": changed,
        "unchanged": len(before) == len(after)
        and not missing
        and not added
        and not changed,
    }
    with (Path(cfg["paths"]["data_dir"]) / "source_integrity.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def run_inventory(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    paths = cfg["paths"]
    pdfs = list_pdfs(paths["modern_pdf_dir"])
    mapping = load_mapping(paths["mapping_csv"])
    logger.info(f"扫描到 PDF: {len(pdfs)}")

    before_path = Path(paths["source_checksums_before"])
    if force or not before_path.exists():
        logger.info("计算处理前 SHA-256 ...")
        checksum_rows = compute_source_checksums(cfg, before_path, logger)
    else:
        logger.info("复用已有处理前 SHA-256 校验文件")
        checksum_rows = read_jsonl(before_path)
    sha_by_name = {r["source_filename"]: r["sha256"] for r in checksum_rows}

    state = _state_path(cfg, "inventory")
    docs_path = Path(paths["documents_jsonl"])
    existing = {r["doc_id"]: r for r in read_jsonl(docs_path)} if docs_path.exists() else {}
    by_filename = {r.get("source_filename"): r for r in existing.values()}
    done = load_done_ids(state) if resume and not force else set()

    if force and not doc_id:
        existing = {}
        by_filename = {}
        done = set()
        if docs_path.exists():
            docs_path.unlink()
        if state.exists():
            state.unlink()
    elif force and doc_id:
        # 单篇强制：按 doc_id 删除后重做
        rewrite_jsonl_excluding(docs_path, {doc_id})
        remove_state_ids(state, {doc_id})
        existing = {r["doc_id"]: r for r in read_jsonl(docs_path)}
        by_filename = {r.get("source_filename"): r for r in existing.values()}
        done.discard(doc_id)

    failed = 0
    built = 0
    processed_targets = 0
    total_plan = len(pdfs)
    if limit:
        total_plan = min(total_plan, limit)

    for p in pdfs:
        prev = by_filename.get(p.name)
        if resume and not force and prev and prev.get("sha256") and prev.get("doc_id") in done:
            if not doc_id or prev.get("doc_id") == doc_id:
                continue

        try:
            digest = None
            if prev and prev.get("sha256") and not force:
                digest = prev["sha256"]
            elif p.name in sha_by_name:
                digest = sha_by_name[p.name]
            rec = build_document_record(p, mapping, sha256=digest)
            override = normalize_doi(
                (cfg.get("doi_overrides") or {}).get(rec["sha256"], "")
            )
            if override:
                if not is_valid_doi(override):
                    raise ValueError(f"配置 DOI 修正不完整: {override}")
                rec["doi_original"] = rec.get("doi") or ""
                rec["doi"] = override
                rec["doi_status"] = "corrected_from_pdf_text"
                rec["doc_id"] = make_doc_id(override, rec["sha256"])
            if doc_id and rec["doc_id"] != doc_id:
                continue
            if resume and not force and rec["doc_id"] in done and rec["doc_id"] in existing:
                continue

            # 保留 extract 阶段已写入字段
            if prev and prev.get("doc_id") == rec["doc_id"] and not force:
                for key in (
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
                ):
                    if prev.get(key) not in ("", None):
                        rec[key] = prev[key]

            # DOI 冲突：不同文件落到同一 doc_id 时，冲突方回退为 sha256 doc_id
            collision = existing.get(rec["doc_id"])
            if (
                collision
                and collision.get("sha256")
                and collision.get("sha256") != rec["sha256"]
            ):
                colliding_doi = rec.get("doi") or collision.get("doi") or ""
                old_collision_id = collision["doc_id"]
                first_sha_id = make_doc_id("", collision["sha256"])
                collision["doc_id"] = first_sha_id
                collision["doi_status"] = "conflict_sha_doc_id"
                collision["field_notes"] = list(collision.get("field_notes") or []) + [
                    f"doi_conflict:{colliding_doi}"
                ]
                existing.pop(old_collision_id, None)
                existing[first_sha_id] = collision
                note = f"doi_conflict:{colliding_doi}"
                rec["field_notes"] = list(rec.get("field_notes") or []) + [note]
                rec["doc_id"] = make_doc_id("", rec["sha256"])
                rec["doi_status"] = "conflict_sha_doc_id"
                logger.warning(
                    "DOI 冲突，双方回退 SHA: %s vs %s",
                    collision.get("source_filename"),
                    p.name,
                )

            existing[rec["doc_id"]] = rec
            by_filename[p.name] = rec
            mark_done(state, rec["doc_id"], {"stage": "inventory", "file": p.name})
            built += 1
            processed_targets += 1
            if processed_targets % int(cfg.get("runtime", {}).get("progress_every", 10)) == 0:
                logger.info(progress_line("inventory", processed_targets, total_plan, failed))
            if limit and processed_targets >= limit:
                break
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.error(f"inventory 失败: {p.name}: {e}")

    all_rows = sorted(existing.values(), key=lambda x: x.get("source_filename") or "")
    write_jsonl(docs_path, all_rows, append=False)
    write_csv(paths["documents_csv"], all_rows, DOC_FIELDS)
    logger.info(
        progress_line(
            "inventory",
            len(all_rows),
            len(pdfs),
            failed,
            extra=f"本轮写入={built}",
        )
    )
    return {
        "pdf_count": len(pdfs),
        "document_count": len(all_rows),
        "new_count": built,
        "failed": failed,
    }


def _load_documents(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    rows = read_jsonl(cfg["paths"]["documents_jsonl"])
    if not rows:
        raise RuntimeError("documents.jsonl 为空，请先运行 inventory")
    return rows


def run_extract(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    paths = cfg["paths"]
    docs = _load_documents(cfg)
    state = _state_path(cfg, "extract")
    pages_path = Path(paths["pages_jsonl"])
    done = load_done_ids(state) if resume and not force else set()

    if force and not doc_id:
        if pages_path.exists():
            pages_path.unlink()
        if state.exists():
            state.unlink()
        done = set()
        for d in docs:
            if d.get("extraction_status") not in (None, ""):
                d["extraction_status"] = "pending"
                d["extraction_error"] = ""
    elif force and doc_id:
        rewrite_jsonl_excluding(pages_path, {doc_id})
        remove_state_ids(state, {doc_id})
        done.discard(doc_id)

    if not pages_path.exists():
        pages_path.parent.mkdir(parents=True, exist_ok=True)
        pages_path.write_text("", encoding="utf-8")

    targets = docs
    if doc_id:
        targets = [d for d in docs if d["doc_id"] == doc_id]
        if not targets:
            raise ValueError(f"未找到 doc_id: {doc_id}")
    if resume and not force:
        targets = [d for d in targets if d["doc_id"] not in done]
    if limit:
        targets = targets[:limit]

    total = len(targets)
    failed = 0
    ok = 0
    needs_ocr = 0
    empty_threshold = int(cfg.get("extraction", {}).get("empty_page_char_threshold", 30))
    preferred = cfg.get("extraction", {}).get("preferred_engine", "pymupdf")
    doc_index = {d["doc_id"]: d for d in docs}

    for i, doc in enumerate(targets, 1):
        pdf_path = Path(doc.get("source_path") or "")
        if not pdf_path.exists():
            pdf_path = Path(paths["modern_pdf_dir"]) / doc["source_filename"]
        try:
            result = extract_pdf_pages(
                pdf_path,
                preferred_engine=preferred,
                empty_threshold=empty_threshold,
            )
            full_text_parts = []
            for p in result["pages"]:
                full_text_parts.append(p["text"])
                append_jsonl(
                    pages_path,
                    {
                        "doc_id": doc["doc_id"],
                        "pdf_page": p["pdf_page"],
                        "page_label": p["page_label"],
                        "text": p["text"],
                        "source_filename": doc["source_filename"],
                        "title": doc.get("title") or "",
                        "year": doc.get("year") or "",
                        "doi": doc.get("doi") or "",
                        "sha256": doc.get("sha256") or "",
                        "language": p.get("language") or result.get("language") or "",
                        "extraction_method": p.get("extraction_method"),
                        "text_char_count": p.get("text_char_count"),
                        "is_empty": p.get("is_empty"),
                        "quality_flags": p.get("quality_flags") or [],
                    },
                )

            sample = (doc.get("title") or "") + "\n" + "\n".join(full_text_parts[:3])
            topic = score_topics(sample, title=doc.get("title") or "")
            d = doc_index[doc["doc_id"]]
            d["page_count"] = result["page_count"]
            d["language"] = result.get("language") or ""
            d["has_text_layer"] = result["has_text_layer"]
            d["total_text_chars"] = result["total_text_chars"]
            d["empty_page_count"] = result["empty_page_count"]
            d["extraction_status"] = result["extraction_status"]
            d["extraction_error"] = result["extraction_error"]
            d["relevance_score"] = topic["relevance_score"]
            d["topic_tags"] = topic["topic_tags"]
            d["relevance_evidence"] = topic["relevance_evidence"]

            if result["extraction_status"] == "needs_ocr":
                needs_ocr += 1
            if result["extraction_status"] == "failed":
                failed += 1
            else:
                ok += 1
            mark_done(
                state,
                doc["doc_id"],
                {"status": result["extraction_status"], "pages": result["page_count"]},
            )
        except PdfExtractError as e:
            failed += 1
            d = doc_index[doc["doc_id"]]
            d["extraction_status"] = "failed"
            d["extraction_error"] = "pdf_encrypted" if e.encrypted else str(e)
            d["has_text_layer"] = False
            mark_done(state, doc["doc_id"], {"status": "failed", "error": str(e)})
            logger.error(f"extract 失败 {doc['source_filename']}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            d = doc_index[doc["doc_id"]]
            d["extraction_status"] = "failed"
            d["extraction_error"] = f"unexpected: {e}"
            mark_done(state, doc["doc_id"], {"status": "failed", "error": str(e)})
            logger.error(f"extract 异常 {doc['source_filename']}: {e}")

        if i % int(cfg.get("runtime", {}).get("progress_every", 10)) == 0 or i == total:
            logger.info(
                progress_line("extract", i, total, failed, extra=f"ok={ok} ocr={needs_ocr}")
            )

    all_docs = list(doc_index.values())
    write_jsonl(paths["documents_jsonl"], all_docs, append=False)
    write_csv(paths["documents_csv"], all_docs, DOC_FIELDS)
    return {"total": total, "ok": ok, "failed": failed, "needs_ocr": needs_ocr}


def run_chunk(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    paths = cfg["paths"]
    docs = {d["doc_id"]: d for d in _load_documents(cfg)}
    state = _state_path(cfg, "chunk")
    chunks_path = Path(paths["chunks_jsonl"])
    pages_path = Path(paths["pages_jsonl"])
    if not pages_path.exists():
        raise RuntimeError("pages.jsonl 不存在，请先运行 extract")

    done = load_done_ids(state) if resume and not force else set()
    if force and not doc_id:
        if state.exists():
            state.unlink()
        done = set()
    elif force and doc_id:
        rewrite_jsonl_excluding(chunks_path, {doc_id})
        remove_state_ids(state, {doc_id})
        done.discard(doc_id)

    output_chunks = (
        read_jsonl(chunks_path)
        if chunks_path.exists() and not (force and not doc_id)
        else []
    )

    pages_by_doc: dict[str, list[dict[str, Any]]] = {}
    for row in iter_jsonl(pages_path):
        did = row["doc_id"]
        if doc_id and did != doc_id:
            continue
        pages_by_doc.setdefault(did, []).append(row)

    targets = sorted(pages_by_doc.keys())
    if resume and not force:
        targets = [d for d in targets if d not in done]
    if limit:
        targets = targets[:limit]

    total = len(targets)
    failed = 0
    chunk_count = 0

    for i, did in enumerate(targets, 1):
        doc = docs.get(did, {})
        try:
            pages = sorted(pages_by_doc[did], key=lambda x: int(x["pdf_page"]))
            n = 0
            doc_chunks: list[dict[str, Any]] = []
            for page in pages:
                if page.get("is_empty") or not (page.get("text") or "").strip():
                    continue
                chunks = chunk_page_text(
                    page.get("text") or "",
                    doc_id=did,
                    pdf_page=int(page["pdf_page"]),
                    language=page.get("language") or doc.get("language") or "",
                    title=doc.get("title") or page.get("title") or "",
                    year=doc.get("year") or page.get("year") or "",
                    doi=doc.get("doi") or page.get("doi") or "",
                    source_filename=doc.get("source_filename")
                    or page.get("source_filename")
                    or "",
                    sha256=doc.get("sha256") or page.get("sha256") or "",
                    cfg=cfg,
                )
                for ch in chunks:
                    doc_chunks.append(ch)
                    n += 1
            output_chunks.extend(doc_chunks)
            chunk_count += n
            mark_done(state, did, {"chunks": n})
        except Exception as e:  # noqa: BLE001
            failed += 1
            mark_done(state, did, {"status": "failed", "error": str(e)})
            logger.error(f"chunk 失败 {did}: {e}")

        if i % int(cfg.get("runtime", {}).get("progress_every", 10)) == 0 or i == total:
            logger.info(
                progress_line("chunk", i, total, failed, extra=f"chunks={chunk_count}")
            )

    write_jsonl_atomic(chunks_path, output_chunks)
    return {"docs": total, "chunks": chunk_count, "failed": failed}


def run_validate(cfg: dict[str, Any], logger) -> dict[str, Any]:
    paths = cfg["paths"]
    docs = read_jsonl(paths["documents_jsonl"])
    pages = list(iter_jsonl(paths["pages_jsonl"]))
    chunks = list(iter_jsonl(paths["chunks_jsonl"]))
    qcfg = cfg.get("quality", {})
    low_thr = int(cfg.get("topics", {}).get("low_relevance_threshold", 25))
    issues: list[dict[str, Any]] = []

    def add(issue_type: str, severity: str, doc_id: str = "", detail: str = "", **extra):
        row = {
            "issue_type": issue_type,
            "severity": severity,
            "doc_id": doc_id,
            "detail": detail,
            "pdf_page": extra.get("pdf_page", ""),
        }
        issues.append(row)

    sha_counter = Counter(d.get("sha256") for d in docs if d.get("sha256"))
    doi_counter = Counter(d.get("doi") for d in docs if d.get("doi"))
    for sha, n in sha_counter.items():
        if n > 1:
            add("sha256_duplicate", "error", detail=f"{sha} count={n}")
    for doi, n in doi_counter.items():
        if n > 1:
            add("doi_duplicate", "error", detail=f"{doi} count={n}")

    for d in docs:
        did = d.get("doc_id") or ""
        status = d.get("extraction_status") or ""
        err = str(d.get("extraction_error") or "")
        if status == "failed":
            if "encrypted" in err:
                add("pdf_encrypted", "error", did, err)
            elif "open" in err:
                add("pdf_open_failed", "error", did, err)
            else:
                add("extraction_failed", "error", did, err)
        if status == "needs_ocr" or err in {"no_text_layer", "high_empty_page_ratio"}:
            add("no_text_layer_or_needs_ocr", "warn", did, err or status)
        if not d.get("title"):
            add("title_missing", "warn", did, "title empty")
        elif len(str(d.get("title"))) < 5:
            add("title_abnormal", "warn", did, f"title too short: {d.get('title')}")
        if not d.get("year"):
            add("year_missing", "warn", did, "year empty")
        doi = d.get("doi") or ""
        if not doi:
            add("doi_missing", "info", did, "doi empty")
        elif not is_valid_doi(doi):
            add("doi_invalid", "warn", did, doi)

        try:
            pc_i = int(d.get("page_count"))
            if pc_i <= 0:
                add("page_count_abnormal", "error", did, str(pc_i))
            elif pc_i > int(qcfg.get("abnormal_page_count_max", 500)):
                add("page_count_abnormal", "warn", did, str(pc_i))
        except Exception:  # noqa: BLE001
            if status not in {"pending", ""}:
                add("page_count_abnormal", "warn", did, str(d.get("page_count")))

        try:
            empty = int(d.get("empty_page_count") or 0)
            pages_n = int(d.get("page_count") or 0)
            if pages_n and empty / pages_n >= float(qcfg.get("high_empty_page_ratio", 0.5)):
                add("high_empty_page_ratio", "warn", did, f"{empty}/{pages_n}")
        except Exception:  # noqa: BLE001
            pass

        try:
            chars = int(d.get("total_text_chars") or 0)
            if status == "ok" and chars < int(
                cfg.get("extraction", {}).get("short_doc_char_threshold", 200)
            ):
                add("abnormally_short_text", "warn", did, str(chars))
        except Exception:  # noqa: BLE001
            pass

        score = int(d.get("relevance_score") or 0)
        tags = d.get("topic_tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:  # noqa: BLE001
                tags = [tags]
        if score <= low_thr or "off_topic" in tags:
            add("low_relevance", "info", did, f"score={score}; tags={tags}")

    for p in pages:
        flags = p.get("quality_flags") or []
        if "garbled_text" in flags or is_garbled(
            p.get("text") or "", float(qcfg.get("garbled_ratio_threshold", 0.15))
        ):
            add(
                "garbled_text",
                "warn",
                p.get("doc_id") or "",
                f"page={p.get('pdf_page')}",
                pdf_page=p.get("pdf_page"),
            )

    short_c = int(qcfg.get("short_chunk_chars", 50))
    long_c = int(qcfg.get("long_chunk_chars", 5000))
    chunk_pages: dict[str, set[int]] = {}
    for c in chunks:
        t = c.get("text") or ""
        if len(t) < short_c:
            add("chunk_too_short", "info", c.get("doc_id") or "", c.get("chunk_id") or "")
        if len(t) > long_c:
            add("chunk_too_long", "warn", c.get("doc_id") or "", c.get("chunk_id") or "")
        try:
            pg = int(c.get("pdf_page"))
            if pg < 1:
                add("chunk_invalid_page", "error", c.get("doc_id") or "", str(pg))
            chunk_pages.setdefault(c["chunk_id"], set()).add(pg)
        except Exception:  # noqa: BLE001
            add("chunk_invalid_page", "error", c.get("doc_id") or "", str(c.get("pdf_page")))

    for cid, pset in chunk_pages.items():
        if len(pset) > 1:
            add("chunk_cross_page", "error", detail=f"{cid} pages={sorted(pset)}")

    write_csv(
        paths["quality_issues_csv"],
        issues,
        ["issue_type", "severity", "doc_id", "detail", "pdf_page"],
    )
    summary = Counter(i["issue_type"] for i in issues)
    logger.info(f"质量问题总数: {len(issues)}")
    for k, v in summary.most_common():
        logger.info(f"  {k}: {v}")

    integrity = verify_source_unchanged(cfg, logger)
    logger.info(f"源 PDF 未改变: {integrity.get('unchanged')}")

    manifest = {
        "documents": len(docs),
        "pages": len(pages),
        "chunks": len(chunks),
        "issues": len(issues),
        "issue_counts": dict(summary),
        "source_integrity": integrity,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with Path(paths["run_manifest"]).open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def run_status(cfg: dict[str, Any], logger) -> dict[str, Any]:
    paths = cfg["paths"]
    pdfs = list_pdfs(paths["modern_pdf_dir"])
    docs = read_jsonl(paths["documents_jsonl"]) if Path(paths["documents_jsonl"]).exists() else []
    pages_n = (
        sum(1 for _ in iter_jsonl(paths["pages_jsonl"]))
        if Path(paths["pages_jsonl"]).exists()
        else 0
    )
    chunks_n = (
        sum(1 for _ in iter_jsonl(paths["chunks_jsonl"]))
        if Path(paths["chunks_jsonl"]).exists()
        else 0
    )
    status_counter = Counter((d.get("extraction_status") or "pending") for d in docs)
    low_thr = int(cfg.get("topics", {}).get("low_relevance_threshold", 25))
    low_rel = sum(1 for d in docs if int(d.get("relevance_score") or 0) <= low_thr)
    info = {
        "pdf_files": len(pdfs),
        "documents": len(docs),
        "pages": pages_n,
        "chunks": chunks_n,
        "extraction_ok": status_counter.get("ok", 0),
        "extraction_failed": status_counter.get("failed", 0),
        "needs_ocr": status_counter.get("needs_ocr", 0),
        "low_relevance_docs": low_rel,
        "state_inventory_done": len(load_done_ids(_state_path(cfg, "inventory"))),
        "state_extract_done": len(load_done_ids(_state_path(cfg, "extract"))),
        "state_chunk_done": len(load_done_ids(_state_path(cfg, "chunk"))),
        "status_breakdown": dict(status_counter),
    }
    db_path = Path(paths.get("database", ""))
    if db_path.is_file():
        try:
            from .search import _connect, database_counts

            with _connect(db_path, readonly=True) as conn:
                info["database"] = database_counts(conn)
                info["database_size_bytes"] = db_path.stat().st_size
        except Exception as exc:  # noqa: BLE001
            info["database_error"] = str(exc)
    else:
        info["database"] = "not_built"
    for k, v in info.items():
        logger.info(f"{k}: {v}")
    return info
