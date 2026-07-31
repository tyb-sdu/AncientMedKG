"""Qwen3 page-level vector retrieval for the ancient corpus."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np

from .ancient_retrieval import (
    ancient_database_path,
    ancient_layout_sidecar_path,
    ancient_text_for_row,
    query_ancient_keyword,
)
from .gpu_retrieval import _model_fingerprint
from .io_utils import read_jsonl, write_jsonl_atomic
from .qwen_retrieval import _dependencies, _embedder, _query_prompt, _reranker
from .search import _diversify_results, _snippet, rrf_fuse
from .vector import _read_faiss_index, _sha256, _write_faiss_index, _write_json_atomic


def _paths(cfg: dict[str, Any]) -> dict[str, Path]:
    paths = cfg["paths"]
    return {
        "dir": Path(paths["ancient_qwen_vector_dir"]),
        "index": Path(paths["ancient_qwen_faiss_index"]),
        "manifest": Path(paths["ancient_qwen_vector_manifest"]),
        "mapping": Path(paths["ancient_qwen_vector_map"]),
        "embeddings": Path(paths["ancient_qwen_embeddings_checkpoint"]),
        "progress": Path(paths["ancient_qwen_embedding_progress"]),
        "pages_jsonl": Path(paths["ancient_pages_jsonl"]),
        "database": ancient_database_path(cfg),
        "model": Path(paths["qwen_embedding_model_dir"]),
        "reranker": Path(paths["qwen_reranker_model_dir"]),
    }


def _page_rows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    db_path = ancient_database_path(cfg)
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label, p.text,
                   p.reading_direction, p.average_confidence, p.low_confidence,
                   b.title, b.filename, b.source_sha256
            FROM pages p
            JOIN books b USING(book_id)
            ORDER BY p.book_id, p.physical_page
            """
        ).fetchall()
    finally:
        connection.close()
    result = []
    for row in rows:
        item = dict(row)
        item["text"], item["layout_status"] = ancient_text_for_row(cfg, row)
        result.append(item)
    return result


def _corpus_text_sha256(pages: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in pages:
        record = {
            "page_id": str(row.get("page_id") or ""),
            "title": str(row.get("title") or ""),
            "text": str(row.get("text") or ""),
        }
        digest.update(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _rows_for_pages(cfg: dict[str, Any], page_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not page_ids:
        return {}
    placeholders = ",".join("?" for _ in page_ids)
    db_path = ancient_database_path(cfg)
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label, p.text,
                   p.reading_direction, p.average_confidence, p.low_confidence,
                   b.title, b.filename, b.source_sha256
            FROM pages p
            JOIN books b USING(book_id)
            WHERE p.page_id IN ({placeholders})
            """,
            page_ids,
        ).fetchall()
    finally:
        connection.close()
    result = {}
    for row in rows:
        item = dict(row)
        item["text"], item["layout_status"] = ancient_text_for_row(cfg, row)
        result[row["page_id"]] = item
    return result


def build_ancient_qwen_vector_index(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if doc_id:
        raise ValueError("embed-ancient-qwen 必须基于完整 page_id 集合，不能使用 --doc-id")
    faiss, _, _ = _dependencies()
    paths = _paths(cfg)
    qcfg = cfg.get("qwen", {})
    pages = _page_rows(cfg)
    if limit:
        pages = pages[:limit]
    total = len(pages)
    dimensions = int(qcfg.get("dimensions", 4096))
    batch_size = int(qcfg.get("embedding_batch_size", 2))
    checkpoint_every = int(qcfg.get("checkpoint_every_batches", 10))
    paths["dir"].mkdir(parents=True, exist_ok=True)
    pages_sha = _sha256(paths["pages_jsonl"])
    corpus_text_sha = _corpus_text_sha256(pages)
    database_sha = _sha256(paths["database"])
    layout_path = ancient_layout_sidecar_path(cfg)
    layout_sha = _sha256(layout_path) if layout_path and layout_path.is_file() else None
    model_sha = _model_fingerprint(paths["model"])
    signature = hashlib.sha256(
        json.dumps(
            {
                "model_id": qcfg.get("embedding_model_id", "Qwen/Qwen3-Embedding-8B"),
                "dimensions": dimensions,
                "max_length": int(qcfg.get("max_length", 512)),
                "normalized": True,
                "query_prompt": _query_prompt(cfg),
                "unit": "ancient_page",
                "layout_sidecar_sha256": layout_sha,
                "corpus_text_sha256": corpus_text_sha,
                "database_sha256": database_sha,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    if resume and not force and not limit and all(
        paths[key].is_file() for key in ("index", "manifest", "mapping")
    ):
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        if (
            manifest.get("pages_sha256") == pages_sha
            and manifest.get("corpus_text_sha256") == corpus_text_sha
            and manifest.get("database_sha256") == database_sha
            and manifest.get("model_fingerprint") == model_sha
            and manifest.get("embedding_signature") == signature
            and manifest.get("layout_sidecar_sha256") == layout_sha
            and int(manifest.get("page_count") or 0) == total
        ):
            logger.info("古籍 Qwen FAISS 数据未变化，跳过: pages=%s", total)
            return {**manifest, "resumed": True}

    if force:
        for key in ("index", "manifest", "mapping", "embeddings", "progress"):
            paths[key].unlink(missing_ok=True)

    start_at = 0
    if resume and paths["embeddings"].is_file() and paths["progress"].is_file():
        progress = json.loads(paths["progress"].read_text(encoding="utf-8"))
        compatible = all(
            [
                progress.get("pages_sha256") == pages_sha,
                progress.get("corpus_text_sha256") == corpus_text_sha,
                progress.get("database_sha256") == database_sha,
                progress.get("model_fingerprint") == model_sha,
                progress.get("embedding_signature") == signature,
                progress.get("layout_sidecar_sha256") == layout_sha,
                int(progress.get("page_count") or 0) == total,
                int(progress.get("dimensions") or 0) == dimensions,
            ]
        )
        if not compatible:
            raise RuntimeError("古籍 Qwen checkpoint 对应其他语料或模型版本，请使用 --force")
        start_at = int(progress.get("next_index") or 0)
        embeddings = np.lib.format.open_memmap(paths["embeddings"], mode="r+")
        if embeddings.shape != (total, dimensions):
            raise RuntimeError("古籍 Qwen checkpoint 形状与当前 pages 不一致")
    else:
        embeddings = np.lib.format.open_memmap(
            paths["embeddings"],
            mode="w+",
            dtype=np.float32,
            shape=(total, dimensions),
        )

    model = _embedder(cfg)
    started = time.time()
    batches = 0
    for start in range(start_at, total, batch_size):
        end = min(start + batch_size, total)
        texts = [
            f"{row.get('title') or ''}\n{row.get('text') or ''}".strip()
            for row in pages[start:end]
        ]
        dense = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        dense = np.asarray(dense, dtype=np.float32)
        if dense.shape != (end - start, dimensions):
            raise RuntimeError(f"古籍 Qwen 向量维度异常: {dense.shape}")
        embeddings[start:end] = dense
        batches += 1
        if batches % checkpoint_every == 0 or end == total:
            embeddings.flush()
            _write_json_atomic(
                paths["progress"],
                {
                    "pages_sha256": pages_sha,
                    "corpus_text_sha256": corpus_text_sha,
                    "database_sha256": database_sha,
                    "model_fingerprint": model_sha,
                    "embedding_signature": signature,
                    "layout_sidecar_sha256": layout_sha,
                    "page_count": total,
                    "dimensions": dimensions,
                    "next_index": end,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            logger.info("古籍 Qwen embed 进度: %s/%s", end, total)

    matrix = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(matrix).all() or (norms < 1e-8).any():
        raise RuntimeError("古籍 Qwen 向量存在 NaN、Inf 或零向量")
    index = faiss.IndexFlatIP(dimensions)
    index.add(matrix)
    _write_faiss_index(index, paths["index"])
    write_jsonl_atomic(
        paths["mapping"],
        [{"faiss_id": number, "page_id": row["page_id"]} for number, row in enumerate(pages)],
    )
    manifest = {
        "schema_version": 1,
        "model_id": qcfg.get("embedding_model_id", "Qwen/Qwen3-Embedding-8B"),
        "model_fingerprint": model_sha,
        "pages_sha256": pages_sha,
        "corpus_text_sha256": corpus_text_sha,
        "database_sha256": database_sha,
        "embedding_signature": signature,
        "layout_sidecar_sha256": layout_sha,
        "page_count": total,
        "dimensions": dimensions,
        "index_type": "IndexFlatIP",
        "normalized": True,
        "query_prompt": _query_prompt(cfg),
        "page_id_reused": True,
        "ancient_db_modified": False,
        "elapsed_seconds": round(time.time() - started, 3),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json_atomic(paths["manifest"], manifest)
    return {**manifest, "resumed": False}


_RUNTIME: dict[str, Any] = {}


def _load_runtime(cfg: dict[str, Any]) -> dict[str, Any]:
    paths = _paths(cfg)
    key = "|".join((str(paths["index"]), str(paths["index"].stat().st_mtime_ns)))
    runtime = _RUNTIME.get(key)
    if runtime is None:
        runtime = {
            "index": _read_faiss_index(paths["index"]),
            "mapping": read_jsonl(paths["mapping"]),
            "manifest": json.loads(paths["manifest"].read_text(encoding="utf-8")),
            "embedder": _embedder(cfg),
        }
        _RUNTIME.clear()
        _RUNTIME[key] = runtime
    return runtime


def query_ancient_qwen_vector(
    cfg: dict[str, Any],
    question: str,
    top_k: int,
    *,
    candidate_k: int | None = None,
) -> list[dict[str, Any]]:
    if not question.strip():
        raise ValueError("查询不能为空")
    runtime = _load_runtime(cfg)
    qcfg = cfg.get("qwen", {})
    candidate_k = candidate_k or max(top_k, int(qcfg.get("vector_candidates", 100)))
    query = runtime["embedder"].encode(
        [question],
        prompt=_query_prompt(cfg),
        batch_size=1,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    scores, ids = runtime["index"].search(
        np.asarray(query, dtype=np.float32),
        min(candidate_k, runtime["index"].ntotal),
    )
    ranked = [
        (rank, int(vector_id), float(score))
        for rank, (vector_id, score) in enumerate(zip(ids[0], scores[0]), 1)
        if vector_id >= 0
    ]
    mapping = runtime["mapping"]
    page_ids = [mapping[vector_id]["page_id"] for _, vector_id, _ in ranked]
    rows = _rows_for_pages(cfg, page_ids)
    results = []
    for rank, vector_id, score in ranked:
        page_id = mapping[vector_id]["page_id"]
        row = rows[page_id]
        results.append(
            {
                "corpus": "ancient",
                "record_type": "page",
                "chunk_id": row["page_id"],
                "doc_id": row["book_id"],
                "title": row["title"],
                "year": "",
                "doi": "",
                "pdf_page": row["physical_page"],
                "page_label": row["pdf_page_label"],
                "source_filename": row["filename"],
                "sha256": row["source_sha256"],
                "snippet": _snippet(
                    row["text"],
                    question,
                    int(cfg.get("search", {}).get("snippet_chars", 360)),
                ),
                "keyword_score": None,
                "vector_score": round(score, 6),
                "keyword_rank": None,
                "vector_rank": rank,
                "fusion_score": None,
                "fusion_rank": rank,
                "reading_direction": row["reading_direction"],
                "average_confidence": row["average_confidence"],
                "low_confidence": int(row["low_confidence"]),
                "qwen_model_id": runtime["manifest"]["model_id"],
            }
        )
    return results[:top_k]


def query_ancient_qwen_reranked_hybrid(
    cfg: dict[str, Any],
    question: str,
    top_k: int,
) -> list[dict[str, Any]]:
    qcfg = cfg.get("qwen", {})
    candidate_count = int(qcfg.get("reranker_candidates", 100))
    keyword = query_ancient_keyword(cfg, question, candidate_count)
    vector = query_ancient_qwen_vector(
        cfg, question, candidate_count, candidate_k=candidate_count
    )
    fused = rrf_fuse(
        keyword,
        vector,
        top_k=candidate_count,
        rrf_k=int(cfg.get("search", {}).get("rrf_k", 60)),
        keyword_weight=float(cfg.get("search", {}).get("rrf_keyword_weight", 1.5)),
        vector_weight=float(qcfg.get("rrf_vector_weight", 1.0)),
    )
    candidates = _diversify_results(cfg, fused, candidate_count)
    if not candidates:
        return []
    rows = _rows_for_pages(cfg, [row["chunk_id"] for row in candidates])
    pairs = [
        (question, f"{row['title']}\n{rows[row['chunk_id']]['text']}")
        for row in candidates
    ]
    scores = _reranker(cfg).predict(
        pairs,
        batch_size=int(qcfg.get("reranker_batch_size", 1)),
        show_progress_bar=False,
    )
    scores = np.asarray(scores).reshape(-1)
    for row, score in zip(candidates, scores):
        row["reranker_score"] = round(float(score), 6)
    ranked = sorted(
        candidates,
        key=lambda row: (
            -float(row["reranker_score"]),
            -float(row.get("fusion_score") or 0.0),
            row["chunk_id"],
        ),
    )
    return _diversify_results(cfg, ranked, top_k)


def ancient_qwen_doctor(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        paths = _paths(cfg)
        if not all(paths[key].is_file() for key in ("index", "manifest", "mapping")):
            return {"present": False, "healthy": False}
        index = _read_faiss_index(paths["index"])
        mapping = read_jsonl(paths["mapping"])
        page_rows = _page_rows(cfg)
        corpus_text_sha = _corpus_text_sha256(page_rows)
        db_ids = {row["page_id"] for row in page_rows}
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        mapped_ids = {row["page_id"] for row in mapping}
        checks = {
            "present": True,
            "index_type": type(index).__name__,
            "index_entries": int(index.ntotal),
            "index_dimensions": int(index.d),
            "mapping_rows": len(mapping),
            "mapping_unique_page_ids": len(mapped_ids),
            "missing_db_page_ids": len(db_ids - mapped_ids),
            "orphan_vector_page_ids": len(mapped_ids - db_ids),
            "pages_sha256_matches": manifest.get("pages_sha256") == _sha256(paths["pages_jsonl"]),
            "corpus_text_sha256_matches": manifest.get("corpus_text_sha256")
            == corpus_text_sha,
            "database_sha256_matches": manifest.get("database_sha256")
            == _sha256(paths["database"]),
            "layout_sidecar_sha256_matches": manifest.get("layout_sidecar_sha256")
            == (
                _sha256(ancient_layout_sidecar_path(cfg))
                if ancient_layout_sidecar_path(cfg)
                and ancient_layout_sidecar_path(cfg).is_file()
                else None
            ),
        }
        checks["healthy"] = all(
            [
                checks["index_entries"] == len(db_ids),
                checks["mapping_rows"] == len(db_ids),
                checks["mapping_unique_page_ids"] == len(db_ids),
                checks["missing_db_page_ids"] == 0,
                checks["orphan_vector_page_ids"] == 0,
                checks["pages_sha256_matches"],
                checks["corpus_text_sha256_matches"],
                checks["database_sha256_matches"],
                checks["layout_sidecar_sha256_matches"],
            ]
        )
        return checks
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "healthy": False, "error": str(exc)}
