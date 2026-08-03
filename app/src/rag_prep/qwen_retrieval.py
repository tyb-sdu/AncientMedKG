"""High-quality Qwen3 8B embedding and reranking sidecar."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np

from .gpu_retrieval import _diversify, _model_fingerprint, _rows_for_chunks
from .io_utils import read_jsonl, write_jsonl_atomic
from .search import _snippet, query_index, rrf_fuse
from .vector import (
    _has_out_of_corpus_identifier,
    _read_faiss_index,
    _sha256,
    _write_faiss_index,
    _write_json_atomic,
)


_EMBED_RUNTIME: dict[str, Any] = {}
_RERANK_RUNTIME: dict[str, Any] = {}


def _dependencies() -> tuple[Any, Any, Any]:
    try:
        import faiss
        import torch
        from sentence_transformers import CrossEncoder, SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Qwen3 检索需要 PyTorch、sentence-transformers 与 FAISS"
        ) from exc
    return faiss, torch, (SentenceTransformer, CrossEncoder)


def _paths(cfg: dict[str, Any]) -> dict[str, Path]:
    paths = cfg["paths"]
    return {
        "dir": Path(paths["qwen_vector_dir"]),
        "index": Path(paths["qwen_faiss_index"]),
        "manifest": Path(paths["qwen_vector_manifest"]),
        "mapping": Path(paths["qwen_vector_map"]),
        "embeddings": Path(paths["qwen_embeddings_checkpoint"]),
        "progress": Path(paths["qwen_embedding_progress"]),
        "model": Path(paths["qwen_embedding_model_dir"]),
        "reranker": Path(paths["qwen_reranker_model_dir"]),
    }


def _query_prompt(cfg: dict[str, Any]) -> str:
    return str(
        cfg.get("qwen", {}).get(
            "query_prompt",
            "Instruct: Retrieve evidence from Chinese and English biomedical "
            "literature for this query.\nQuery: ",
        )
    )


def _embedder(cfg: dict[str, Any]) -> Any:
    _, torch, (SentenceTransformer, _) = _dependencies()
    qcfg = cfg.get("qwen", {})
    model_path = _paths(cfg)["model"]
    device = str(qcfg.get("embedding_device", "cuda:0"))
    key = "|".join((str(model_path), device, str(qcfg.get("max_length", 512))))
    if key in _EMBED_RUNTIME:
        return _EMBED_RUNTIME[key]
    model = SentenceTransformer(
        str(model_path),
        device=device,
        model_kwargs={
            "torch_dtype": torch.bfloat16,
            "attn_implementation": "sdpa",
        },
        tokenizer_kwargs={"padding_side": "left"},
    )
    model.max_seq_length = int(qcfg.get("max_length", 512))
    _EMBED_RUNTIME.clear()
    _EMBED_RUNTIME[key] = model
    return model


def _reranker(cfg: dict[str, Any]) -> Any:
    _, torch, (_, CrossEncoder) = _dependencies()
    qcfg = cfg.get("qwen", {})
    model_path = _paths(cfg)["reranker"]
    device = str(qcfg.get("reranker_device", "cuda:1"))
    key = "|".join(
        (str(model_path), device, str(qcfg.get("reranker_max_length", 512)))
    )
    if key in _RERANK_RUNTIME:
        return _RERANK_RUNTIME[key]
    model = CrossEncoder(
        str(model_path),
        device=device,
        max_length=int(qcfg.get("reranker_max_length", 512)),
        model_kwargs={
            "torch_dtype": torch.bfloat16,
            "attn_implementation": "sdpa",
        },
        tokenizer_kwargs={"padding_side": "left"},
    )
    _RERANK_RUNTIME.clear()
    _RERANK_RUNTIME[key] = model
    return model


def build_qwen_vector_index(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a resumable 4096-dimensional Qwen3-Embedding-8B FAISS index."""
    if doc_id:
        raise ValueError("embed-qwen 必须基于完整 chunk_id 集合，不能使用 --doc-id")
    faiss, _, _ = _dependencies()
    paths = _paths(cfg)
    qcfg = cfg.get("qwen", {})
    chunks_path = Path(cfg["paths"]["chunks_jsonl"])
    chunks = read_jsonl(chunks_path)
    if limit:
        chunks = chunks[:limit]
    total = len(chunks)
    dimensions = int(qcfg.get("dimensions", 4096))
    batch_size = int(qcfg.get("embedding_batch_size", 2))
    checkpoint_every = int(qcfg.get("checkpoint_every_batches", 10))
    paths["dir"].mkdir(parents=True, exist_ok=True)
    chunks_sha = _sha256(chunks_path)
    model_sha = _model_fingerprint(paths["model"])
    signature = hashlib.sha256(
        json.dumps(
            {
                "model_id": qcfg.get(
                    "embedding_model_id", "Qwen/Qwen3-Embedding-8B"
                ),
                "dimensions": dimensions,
                "max_length": int(qcfg.get("max_length", 512)),
                "normalized": True,
                "query_prompt": _query_prompt(cfg),
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
            manifest.get("chunks_sha256") == chunks_sha
            and manifest.get("model_fingerprint") == model_sha
            and manifest.get("embedding_signature") == signature
            and int(manifest.get("chunk_count") or 0) == total
        ):
            logger.info("Qwen3 FAISS 数据未变化，跳过: chunks=%s", total)
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
            raise RuntimeError(
                "Qwen3 checkpoint 对应其他语料或模型版本，请使用 --force"
            )
        start_at = int(progress.get("next_index") or 0)
        embeddings = np.lib.format.open_memmap(paths["embeddings"], mode="r+")
        if embeddings.shape != (total, dimensions):
            raise RuntimeError("Qwen3 checkpoint 形状与当前 chunks 不一致")
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
            for row in chunks[start:end]
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
            raise RuntimeError(f"Qwen3 向量维度异常: {dense.shape}")
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
            logger.info("Qwen3 embed 进度: %s/%s", end, total)

    matrix = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(matrix).all() or (norms < 1e-8).any():
        raise RuntimeError("Qwen3 向量存在 NaN、Inf 或零向量")
    index = faiss.IndexFlatIP(dimensions)
    index.add(matrix)
    _write_faiss_index(index, paths["index"])
    write_jsonl_atomic(
        paths["mapping"],
        [
            {"faiss_id": number, "chunk_id": row["chunk_id"]}
            for number, row in enumerate(chunks)
        ],
    )
    manifest = {
        "schema_version": 1,
        "model_id": qcfg.get(
            "embedding_model_id", "Qwen/Qwen3-Embedding-8B"
        ),
        "model_fingerprint": model_sha,
        "chunks_sha256": chunks_sha,
        "embedding_signature": signature,
        "chunk_count": total,
        "dimensions": dimensions,
        "index_type": "IndexFlatIP",
        "normalized": True,
        "query_prompt": _query_prompt(cfg),
        "chunk_id_reused": True,
        "rag_db_modified": False,
        "elapsed_seconds": round(time.time() - started, 3),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json_atomic(paths["manifest"], manifest)
    return {**manifest, "resumed": False}


def _load_runtime(cfg: dict[str, Any]) -> dict[str, Any]:
    paths = _paths(cfg)
    key = "|".join((str(paths["index"]), str(paths["index"].stat().st_mtime_ns)))
    runtime = _EMBED_RUNTIME.get("index:" + key)
    if runtime is None:
        runtime = {
            "index": _read_faiss_index(paths["index"]),
            "mapping": read_jsonl(paths["mapping"]),
            "manifest": json.loads(
                paths["manifest"].read_text(encoding="utf-8")
            ),
            "embedder": _embedder(cfg),
        }
        _EMBED_RUNTIME["index:" + key] = runtime
    return runtime


def query_qwen_vector(
    cfg: dict[str, Any],
    question: str,
    top_k: int,
    *,
    candidate_k: int | None = None,
) -> list[dict[str, Any]]:
    if not question.strip():
        raise ValueError("查询不能为空")
    if _has_out_of_corpus_identifier(cfg, question):
        return []
    runtime = _load_runtime(cfg)
    qcfg = cfg.get("qwen", {})
    candidate_k = candidate_k or max(
        top_k, int(qcfg.get("vector_candidates", 100))
    )
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
    chunk_ids = [mapping[vector_id]["chunk_id"] for _, vector_id, _ in ranked]
    rows = _rows_for_chunks(cfg, chunk_ids)
    results = []
    for rank, vector_id, score in ranked:
        chunk_id = mapping[vector_id]["chunk_id"]
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
                "qwen_model_id": runtime["manifest"]["model_id"],
            }
        )
    return results[:top_k]


def query_qwen_reranked_hybrid(
    cfg: dict[str, Any], question: str, top_k: int
) -> list[dict[str, Any]]:
    qcfg = cfg.get("qwen", {})
    candidate_count = int(qcfg.get("reranker_candidates", 100))
    keyword = query_index(cfg, question, candidate_count)
    vector = query_qwen_vector(
        cfg, question, candidate_count, candidate_k=candidate_count
    )
    fused = rrf_fuse(
        keyword,
        vector,
        top_k=candidate_count,
        rrf_k=int(cfg.get("search", {}).get("rrf_k", 60)),
        keyword_weight=float(
            cfg.get("search", {}).get("rrf_keyword_weight", 1.5)
        ),
        vector_weight=float(qcfg.get("rrf_vector_weight", 1.0)),
    )
    candidates = _diversify(cfg, fused, candidate_count)
    if not candidates:
        return []
    texts = _rows_for_chunks(cfg, [row["chunk_id"] for row in candidates])
    pairs = [
        (question, f"{row['title']}\n{texts[row['chunk_id']]['text']}")
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
    return _diversify(cfg, ranked, top_k)


def qwen_doctor(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        paths = _paths(cfg)
        if not all(
            paths[key].is_file() for key in ("index", "manifest", "mapping")
        ):
            return {"present": False, "healthy": False}
        index = _read_faiss_index(paths["index"])
        mapping = read_jsonl(paths["mapping"])
        with sqlite3.connect(cfg["paths"]["database"]) as connection:
            db_ids = {
                row[0] for row in connection.execute("SELECT chunk_id FROM chunks")
            }
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
            "chunks_sha256_matches": manifest.get("chunks_sha256")
            == _sha256(cfg["paths"]["chunks_jsonl"]),
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
