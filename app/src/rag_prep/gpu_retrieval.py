"""GPU BGE-M3 retrieval and cross-encoder reranking.

This module is deliberately a sidecar to the frozen CPU E5 index: it reuses
existing chunk IDs and SQLite evidence, but never writes to ``rag.db``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .io_utils import read_jsonl, write_jsonl_atomic
from .search import _snippet, query_index, rrf_fuse
from .vector import (
    _has_out_of_corpus_identifier,
    _read_faiss_index,
    _sha256,
    _write_faiss_index,
    _write_json_atomic,
)


_RUNTIME: dict[str, Any] = {}
_RERANKER_RUNTIME: dict[str, Any] = {}


def _require_dependencies() -> tuple[Any, Any]:
    try:
        import faiss
        from FlagEmbedding import BGEM3FlagModel, FlagReranker
    except ImportError as exc:
        raise RuntimeError(
            "BGE GPU 检索需要服务器环境中的 FlagEmbedding、PyTorch 与 FAISS"
        ) from exc
    return faiss, (BGEM3FlagModel, FlagReranker)


def _model_fingerprint(model_dir: Path) -> str:
    if not model_dir.is_dir():
        raise FileNotFoundError(f"BGE 模型目录不存在: {model_dir}")
    digest = hashlib.sha256()
    files = sorted(path for path in model_dir.rglob("*") if path.is_file())
    if not files:
        raise FileNotFoundError(f"BGE 模型目录为空: {model_dir}")
    for path in files:
        digest.update(path.relative_to(model_dir).as_posix().encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    return digest.hexdigest()


def _vector_paths(cfg: dict[str, Any]) -> dict[str, Path]:
    paths = cfg["paths"]
    return {
        "dir": Path(paths["bge_vector_dir"]),
        "index": Path(paths["bge_faiss_index"]),
        "manifest": Path(paths["bge_vector_manifest"]),
        "mapping": Path(paths["bge_vector_map"]),
        "embeddings": Path(paths["bge_embeddings_checkpoint"]),
        "progress": Path(paths["bge_embedding_progress"]),
        "model": Path(paths["bge_embedding_model_dir"]),
        "reranker": Path(paths["bge_reranker_model_dir"]),
    }


def _embedder(cfg: dict[str, Any]) -> Any:
    _, (BGEM3FlagModel, _) = _require_dependencies()
    gcfg = cfg.get("bge", {})
    return BGEM3FlagModel(
        str(_vector_paths(cfg)["model"]),
        normalize_embeddings=True,
        use_fp16=bool(gcfg.get("use_fp16", True)),
        devices=str(gcfg.get("device", "cuda:0")),
        batch_size=int(gcfg.get("embedding_batch_size", 16)),
        query_max_length=int(gcfg.get("query_max_length", 512)),
        passage_max_length=int(gcfg.get("passage_max_length", 512)),
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )


def _reranker(cfg: dict[str, Any]) -> Any:
    _, (_, FlagReranker) = _require_dependencies()
    gcfg = cfg.get("bge", {})
    model_path = _vector_paths(cfg)["reranker"]
    key = "|".join(
        (
            str(model_path),
            str(gcfg.get("device", "cuda:0")),
            str(bool(gcfg.get("use_fp16", True))),
            str(int(gcfg.get("reranker_max_length", 512))),
        )
    )
    if key in _RERANKER_RUNTIME:
        return _RERANKER_RUNTIME[key]
    model = FlagReranker(
        str(model_path),
        use_fp16=bool(gcfg.get("use_fp16", True)),
        devices=str(gcfg.get("device", "cuda:0")),
        batch_size=int(gcfg.get("reranker_batch_size", 32)),
        max_length=int(gcfg.get("reranker_max_length", 512)),
        normalize=True,
    )
    _RERANKER_RUNTIME.clear()
    _RERANKER_RUNTIME[key] = model
    return model


def build_bge_vector_index(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a resumable GPU vector sidecar using the existing chunk IDs."""
    if doc_id:
        raise ValueError("embed-bge 必须基于完整 chunk_id 集合，不能使用 --doc-id")
    faiss, _ = _require_dependencies()
    paths = _vector_paths(cfg)
    gcfg = cfg.get("bge", {})
    chunks_path = Path(cfg["paths"]["chunks_jsonl"])
    chunks = read_jsonl(chunks_path)
    if limit:
        chunks = chunks[:limit]
    total = len(chunks)
    dimensions = int(gcfg.get("dimensions", 1024))
    batch_size = int(gcfg.get("embedding_batch_size", 16))
    checkpoint_every = int(gcfg.get("checkpoint_every_batches", 10))
    paths["dir"].mkdir(parents=True, exist_ok=True)
    chunks_sha = _sha256(chunks_path)
    model_sha = _model_fingerprint(paths["model"])
    signature = hashlib.sha256(
        json.dumps(
            {
                "model_id": gcfg.get("embedding_model_id", "BAAI/bge-m3"),
                "dimensions": dimensions,
                "max_length": int(gcfg.get("passage_max_length", 512)),
                "normalized": True,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    if resume and not force and not limit and all(
        paths[key].is_file() for key in ("index", "manifest", "mapping")
    ):
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        if (
            manifest.get("chunks_sha256") == chunks_sha
            and manifest.get("model_fingerprint") == model_sha
            and manifest.get("embedding_signature") == signature
            and int(manifest.get("chunk_count") or 0) == total
        ):
            logger.info("BGE-M3 FAISS 数据未变化，跳过: chunks=%s", total)
            return {**manifest, "resumed": True}

    if force:
        for key in ("index", "manifest", "mapping", "embeddings", "progress"):
            paths[key].unlink(missing_ok=True)

    start_at = 0
    if resume and paths["embeddings"].is_file() and paths["progress"].is_file():
        progress = json.loads(paths["progress"].read_text(encoding="utf-8"))
        compatible = all(
            [
                progress.get("chunks_sha256") == chunks_sha,
                progress.get("model_fingerprint") == model_sha,
                progress.get("embedding_signature") == signature,
                int(progress.get("chunk_count") or 0) == total,
                int(progress.get("dimensions") or 0) == dimensions,
            ]
        )
        if not compatible:
            raise RuntimeError("BGE checkpoint 对应其他语料或模型版本，请使用 --force")
        start_at = int(progress.get("next_index") or 0)
        embeddings = np.lib.format.open_memmap(paths["embeddings"], mode="r+")
        if embeddings.shape != (total, dimensions):
            raise RuntimeError("BGE checkpoint 形状与当前 chunks 不一致")
    else:
        embeddings = np.lib.format.open_memmap(
            paths["embeddings"], mode="w+", dtype=np.float32, shape=(total, dimensions)
        )

    model = _embedder(cfg)
    started = time.time()
    batches = 0
    for start in range(start_at, total, batch_size):
        end = min(start + batch_size, total)
        texts = [
            f"{row.get('title') or ''}\n{row.get('text') or ''}".strip()
            for row in chunks[start:end]
        ]
        dense = model.encode(
            texts,
            batch_size=batch_size,
            max_length=int(gcfg.get("passage_max_length", 512)),
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )["dense_vecs"]
        dense = np.asarray(dense, dtype=np.float32)
        if dense.shape != (end - start, dimensions):
            raise RuntimeError(f"BGE 向量维度异常: {dense.shape}")
        embeddings[start:end] = dense
        batches += 1
        if batches % checkpoint_every == 0 or end == total:
            embeddings.flush()
            _write_json_atomic(
                paths["progress"],
                {
                    "chunks_sha256": chunks_sha,
                    "model_fingerprint": model_sha,
                    "embedding_signature": signature,
                    "chunk_count": total,
                    "dimensions": dimensions,
                    "next_index": end,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            logger.info("BGE embed 进度: %s/%s", end, total)

    matrix = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(matrix).all() or (norms < 1e-8).any():
        raise RuntimeError("BGE 向量存在 NaN、Inf 或零向量")
    index = faiss.IndexFlatIP(dimensions)
    index.add(matrix)
    _write_faiss_index(index, paths["index"])
    write_jsonl_atomic(
        paths["mapping"],
        [{"faiss_id": number, "chunk_id": row["chunk_id"]} for number, row in enumerate(chunks)],
    )
    manifest = {
        "schema_version": 1,
        "model_id": gcfg.get("embedding_model_id", "BAAI/bge-m3"),
        "model_fingerprint": model_sha,
        "chunks_sha256": chunks_sha,
        "embedding_signature": signature,
        "chunk_count": total,
        "dimensions": dimensions,
        "index_type": "IndexFlatIP",
        "normalized": True,
        "chunk_id_reused": True,
        "rag_db_modified": False,
        "elapsed_seconds": round(time.time() - started, 3),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json_atomic(paths["manifest"], manifest)
    return {**manifest, "resumed": False}


def _load_runtime(cfg: dict[str, Any]) -> dict[str, Any]:
    paths = _vector_paths(cfg)
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


def _rows_for_chunks(cfg: dict[str, Any], chunk_ids: list[str]) -> dict[str, sqlite3.Row]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    connection = sqlite3.connect(
        f"file:{Path(cfg['paths']['database']).as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"""
        SELECT c.chunk_id, c.doc_id, c.pdf_page, c.text, d.title, d.year,
               d.doi, d.source_filename, d.sha256
        FROM chunks c JOIN documents d ON d.doc_id=c.doc_id
        WHERE c.chunk_id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    connection.close()
    return {row["chunk_id"]: row for row in rows}


def query_bge_vector(
    cfg: dict[str, Any], question: str, top_k: int, *, candidate_k: int | None = None
) -> list[dict[str, Any]]:
    if not question.strip():
        raise ValueError("查询不能为空")
    if _has_out_of_corpus_identifier(cfg, question):
        return []
    runtime = _load_runtime(cfg)
    gcfg = cfg.get("bge", {})
    index = runtime["index"]
    candidate_k = candidate_k or max(top_k, int(gcfg.get("vector_candidates", 100)))
    query = runtime["embedder"].encode(
        [question],
        batch_size=1,
        max_length=int(gcfg.get("query_max_length", 512)),
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )["dense_vecs"]
    scores, ids = index.search(np.asarray(query, dtype=np.float32), min(candidate_k, index.ntotal))
    ranked = [
        (rank, int(vector_id), float(score))
        for rank, (vector_id, score) in enumerate(zip(ids[0], scores[0]), 1)
        if vector_id >= 0
    ]
    mappings = runtime["mapping"]
    chunk_ids = [mappings[vector_id]["chunk_id"] for _, vector_id, _ in ranked]
    rows = _rows_for_chunks(cfg, chunk_ids)
    results = []
    for rank, vector_id, score in ranked:
        chunk_id = mappings[vector_id]["chunk_id"]
        row = rows[chunk_id]
        results.append(
            {
                "chunk_id": chunk_id,
                "doc_id": row["doc_id"],
                "title": row["title"],
                "year": row["year"],
                "doi": row["doi"],
                "pdf_page": row["pdf_page"],
                "source_filename": row["source_filename"],
                "sha256": row["sha256"],
                "snippet": _snippet(row["text"], question, int(cfg.get("search", {}).get("snippet_chars", 360))),
                "keyword_score": None,
                "vector_score": round(score, 6),
                "keyword_rank": None,
                "vector_rank": rank,
                "fusion_score": None,
                "fusion_rank": rank,
                "bge_model_id": runtime["manifest"]["model_id"],
            }
        )
    return results[:top_k]


def _diversify(cfg: dict[str, Any], rows: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    cap = int(cfg.get("search", {}).get("max_chunks_per_document", 3))
    counts: Counter[str] = Counter()
    selected = []
    for row in rows:
        if counts[row["doc_id"]] >= cap:
            continue
        counts[row["doc_id"]] += 1
        selected.append(row)
        if len(selected) >= top_k:
            break
    for rank, row in enumerate(selected, 1):
        row["fusion_rank"] = rank
    return selected


def query_reranked_hybrid(
    cfg: dict[str, Any], question: str, top_k: int
) -> list[dict[str, Any]]:
    """Fuse FTS5 and BGE dense candidates, then GPU-rerank their full chunks."""
    gcfg = cfg.get("bge", {})
    candidate_count = int(gcfg.get("reranker_candidates", 100))
    keyword = query_index(cfg, question, candidate_count)
    vector = query_bge_vector(cfg, question, candidate_count, candidate_k=candidate_count)
    fused = rrf_fuse(
        keyword,
        vector,
        top_k=candidate_count,
        rrf_k=int(cfg.get("search", {}).get("rrf_k", 60)),
        keyword_weight=float(cfg.get("search", {}).get("rrf_keyword_weight", 1.5)),
        vector_weight=float(gcfg.get("rrf_vector_weight", 1.0)),
    )
    candidates = _diversify(cfg, fused, candidate_count)
    if not candidates:
        return []
    texts = _rows_for_chunks(cfg, [row["chunk_id"] for row in candidates])
    reranker = _reranker(cfg)
    pairs = [
        (question, f"{row['title']}\n{texts[row['chunk_id']]['text']}")
        for row in candidates
    ]
    scores = reranker.compute_score(
        pairs,
        batch_size=int(gcfg.get("reranker_batch_size", 32)),
        max_length=int(gcfg.get("reranker_max_length", 512)),
        normalize=True,
    )
    if isinstance(scores, (float, int)):
        scores = [scores]
    for row, score in zip(candidates, scores):
        row["reranker_score"] = round(float(score), 6)
    ranked = sorted(
        candidates,
        key=lambda row: (-float(row["reranker_score"]), -float(row.get("fusion_score") or 0.0), row["chunk_id"]),
    )
    return _diversify(cfg, ranked, top_k)


def bge_doctor(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        paths = _vector_paths(cfg)
        if not all(paths[key].is_file() for key in ("index", "manifest", "mapping")):
            return {"present": False, "healthy": False}
        index = _read_faiss_index(paths["index"])
        mapping = read_jsonl(paths["mapping"])
        with sqlite3.connect(cfg["paths"]["database"]) as connection:
            db_ids = {row[0] for row in connection.execute("SELECT chunk_id FROM chunks")}
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        mapped_ids = {row["chunk_id"] for row in mapping}
        checks = {
            "present": True,
            "index_type": type(index).__name__,
            "index_entries": int(index.ntotal),
            "index_dimensions": int(index.d),
            "mapping_rows": len(mapping),
            "mapping_unique_chunk_ids": len(mapped_ids),
            "missing_db_chunk_ids": len(db_ids - mapped_ids),
            "orphan_vector_chunk_ids": len(mapped_ids - db_ids),
            "chunks_sha256_matches": manifest.get("chunks_sha256") == _sha256(cfg["paths"]["chunks_jsonl"]),
        }
        checks["healthy"] = all(
            [
                checks["index_entries"] == len(db_ids),
                checks["mapping_rows"] == len(db_ids),
                checks["mapping_unique_chunk_ids"] == len(db_ids),
                checks["missing_db_chunk_ids"] == 0,
                checks["orphan_vector_chunk_ids"] == 0,
                checks["chunks_sha256_matches"],
            ]
        )
        return checks
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "healthy": False, "error": str(exc)}
