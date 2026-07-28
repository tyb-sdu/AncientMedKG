from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np

from .io_utils import read_jsonl, write_jsonl_atomic
from .search import CONCEPTS, TOKEN_RE, _snippet, normalize_search_text, query_index


_QUERY_RUNTIME: dict[str, Any] = {}


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


def _write_faiss_index(index: Any, path: Path) -> None:
    """绕过 FAISS Windows 文件接口不支持 Unicode 路径的问题。"""
    import faiss

    payload = faiss.serialize_index(index)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload.tobytes())
    os.replace(tmp, path)


def _read_faiss_index(path: str | Path) -> Any:
    import faiss

    payload = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    return faiss.deserialize_index(payload)


class OnnxE5:
    def __init__(self, cfg: dict[str, Any]):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "缺少本地向量依赖，请安装 requirements.txt"
            ) from exc

        paths = cfg["paths"]
        ecfg = cfg.get("embedding", {})
        self.model_dir = Path(paths["embedding_model_dir"])
        self.model_path = self.model_dir / ecfg.get(
            "model_filename", "model_qint8_avx512_vnni.onnx"
        )
        self.tokenizer_path = self.model_dir / "tokenizer.json"
        if not self.model_path.is_file() or not self.tokenizer_path.is_file():
            raise FileNotFoundError(
                f"本地嵌入模型不完整: {self.model_dir}"
            )
        self.max_length = int(ecfg.get("max_length", 512))
        self.query_prefix = str(ecfg.get("query_prefix", "query: "))
        self.passage_prefix = str(ecfg.get("passage_prefix", "passage: "))
        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self.tokenizer.enable_truncation(max_length=self.max_length)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = int(ecfg.get("intra_op_threads", 8))
        session_options.inter_op_num_threads = int(ecfg.get("inter_op_threads", 1))
        session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(self.model_path),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )

    def encode(self, texts: list[str], *, query: bool) -> np.ndarray:
        if not texts:
            return np.empty((0, 384), dtype=np.float32)
        prefix = self.query_prefix if query else self.passage_prefix
        encoded = self.tokenizer.encode_batch([prefix + text for text in texts])
        input_ids = np.asarray([row.ids for row in encoded], dtype=np.int64)
        attention_mask = np.asarray(
            [row.attention_mask for row in encoded], dtype=np.int64
        )
        token_type_ids = np.asarray(
            [row.type_ids for row in encoded], dtype=np.int64
        )
        hidden = self.session.run(
            ["last_hidden_state"],
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0]
        mask = attention_mask[..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.clip(
            mask.sum(axis=1), 1e-9, None
        )
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-12, None)).astype(np.float32)


def expand_embedding_query(question: str) -> str:
    """补充领域内中英别名，降低跨语言实体被一般语义淹没的风险。"""
    english_aliases: list[str] = []
    for concept, aliases in CONCEPTS.items():
        if concept in question:
            english_aliases.extend(
                alias for alias in aliases if re.search(r"[A-Za-z]", alias)
            )
    if english_aliases:
        return " ; ".join(dict.fromkeys(english_aliases))
    return question.strip()


def _has_out_of_corpus_identifier(cfg: dict[str, Any], question: str) -> bool:
    tokens = [
        token
        for token in TOKEN_RE.findall(normalize_search_text(question))
        if re.search(r"[a-z]", token)
        and re.search(r"\d", token)
        and len(token) >= 6
    ]
    return bool(tokens) and not query_index(cfg, question, top_k=1)


def build_vector_index(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if doc_id:
        raise ValueError("embed 必须基于完整 chunk_id 集合，不能使用 --doc-id")
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("缺少 faiss-cpu，请安装 requirements.txt") from exc

    paths = cfg["paths"]
    ecfg = cfg.get("embedding", {})
    chunks_path = Path(paths["chunks_jsonl"])
    chunks = read_jsonl(chunks_path)
    if limit:
        chunks = chunks[:limit]
    total = len(chunks)
    dimensions = int(ecfg.get("dimensions", 384))
    batch_size = int(ecfg.get("batch_size", 8))
    checkpoint_every = int(ecfg.get("checkpoint_every_batches", 10))
    vector_dir = Path(paths["vector_dir"])
    vector_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = Path(paths["embeddings_checkpoint"])
    progress_path = Path(paths["embedding_progress"])
    index_path = Path(paths["faiss_index"])
    manifest_path = Path(paths["vector_manifest"])
    map_path = Path(paths["vector_map"])
    model = OnnxE5(cfg)
    chunks_sha = _sha256(chunks_path)
    model_sha = _sha256(model.model_path)
    tokenizer_sha = _sha256(model.tokenizer_path)
    embedding_signature = hashlib.sha256(
        json.dumps(
            {
                "dimensions": dimensions,
                "max_length": int(ecfg.get("max_length", 512)),
                "passage_prefix": ecfg.get("passage_prefix", "passage: "),
                "query_prefix": ecfg.get("query_prefix", "query: "),
                "pooling": "attention-mask mean pooling",
                "normalized": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    if (
        resume
        and not force
        and not limit
        and manifest_path.is_file()
        and index_path.is_file()
        and map_path.is_file()
    ):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("chunks_sha256") == chunks_sha
            and manifest.get("model_sha256") == model_sha
            and manifest.get("embedding_signature") == embedding_signature
            and int(manifest.get("chunk_count") or 0) == total
        ):
            logger.info("FAISS 数据未变化，断点续跑跳过: chunks=%s", total)
            return {**manifest, "resumed": True}

    if force:
        for path in (checkpoint_path, progress_path, index_path, manifest_path, map_path):
            if path.exists():
                path.unlink()

    next_index = 0
    if resume and checkpoint_path.is_file() and progress_path.is_file():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        compatible = all(
            [
                progress.get("chunks_sha256") == chunks_sha,
                progress.get("model_sha256") == model_sha,
                progress.get("embedding_signature") == embedding_signature,
                int(progress.get("chunk_count") or 0) == total,
                int(progress.get("dimensions") or 0) == dimensions,
            ]
        )
        if compatible:
            next_index = int(progress.get("next_index") or 0)
            embeddings = np.lib.format.open_memmap(
                checkpoint_path, mode="r+", dtype=np.float32
            )
            if embeddings.shape != (total, dimensions):
                raise RuntimeError("嵌入 checkpoint 形状与当前 chunks 不一致")
        else:
            raise RuntimeError("嵌入 checkpoint 对应其他数据版本，请使用 --force")
    else:
        embeddings = np.lib.format.open_memmap(
            checkpoint_path,
            mode="w+",
            dtype=np.float32,
            shape=(total, dimensions),
        )

    started = time.time()
    batches = 0
    for start in range(next_index, total, batch_size):
        end = min(start + batch_size, total)
        texts = [
            f"{row.get('title') or ''}\n{row.get('text') or ''}".strip()
            for row in chunks[start:end]
        ]
        embeddings[start:end] = model.encode(texts, query=False)
        batches += 1
        if batches % checkpoint_every == 0 or end == total:
            embeddings.flush()
            _write_json_atomic(
                progress_path,
                {
                    "chunks_sha256": chunks_sha,
                    "model_sha256": model_sha,
                    "embedding_signature": embedding_signature,
                    "chunk_count": total,
                    "dimensions": dimensions,
                    "next_index": end,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            logger.info(
                "embed 进度: %s/%s (%.1f%%)",
                end,
                total,
                100 * end / max(total, 1),
            )

    matrix = np.asarray(embeddings, dtype=np.float32)
    index = faiss.IndexFlatIP(dimensions)
    index.add(matrix)
    _write_faiss_index(index, index_path)
    write_jsonl_atomic(
        map_path,
        [
            {"faiss_id": index, "chunk_id": row["chunk_id"]}
            for index, row in enumerate(chunks)
        ],
    )
    manifest = {
        "schema_version": 1,
        "model_id": ecfg.get("model_id", "intfloat/multilingual-e5-small"),
        "runtime": "onnxruntime",
        "model_filename": model.model_path.name,
        "model_sha256": model_sha,
        "tokenizer_sha256": tokenizer_sha,
        "embedding_signature": embedding_signature,
        "chunks_sha256": chunks_sha,
        "chunk_count": total,
        "dimensions": dimensions,
        "index_type": "IndexFlatIP",
        "normalized": True,
        "pooling": "attention-mask mean pooling",
        "max_length": int(ecfg.get("max_length", 512)),
        "chunk_id_reused": True,
        "rag_db_modified": False,
        "elapsed_seconds": round(time.time() - started, 3),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json_atomic(manifest_path, manifest)
    return {**manifest, "resumed": False}


def query_vector(
    cfg: dict[str, Any],
    question: str,
    top_k: int,
    *,
    candidate_k: int | None = None,
) -> list[dict[str, Any]]:
    import faiss

    if _has_out_of_corpus_identifier(cfg, question):
        return []

    paths = cfg["paths"]
    runtime_key = "|".join(
        [
            str(paths["faiss_index"]),
            str(Path(paths["faiss_index"]).stat().st_mtime_ns),
            str(paths["vector_map"]),
        ]
    )
    runtime = _QUERY_RUNTIME.get(runtime_key)
    if runtime is None:
        runtime = {
            "manifest": json.loads(
                Path(paths["vector_manifest"]).read_text(encoding="utf-8")
            ),
            "mappings": read_jsonl(paths["vector_map"]),
            "index": _read_faiss_index(paths["faiss_index"]),
            "model": OnnxE5(cfg),
        }
        _QUERY_RUNTIME.clear()
        _QUERY_RUNTIME[runtime_key] = runtime
    manifest = runtime["manifest"]
    mappings = runtime["mappings"]
    index = runtime["index"]
    candidate_k = candidate_k or max(
        top_k, int(cfg.get("search", {}).get("vector_candidates", 60))
    )
    model = runtime["model"]
    expanded_question = expand_embedding_query(question)
    query_embedding = model.encode([expanded_question], query=True)
    scores, ids = index.search(query_embedding, min(candidate_k, index.ntotal))
    ranked = [
        (rank, int(fid), float(score))
        for rank, (fid, score) in enumerate(zip(ids[0], scores[0]), 1)
        if fid >= 0
    ]
    chunk_ids = [mappings[fid]["chunk_id"] for _, fid, _ in ranked]
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    connection = sqlite3.connect(
        f"file:{Path(paths['database']).as_posix()}?mode=ro", uri=True
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
    by_id = {row["chunk_id"]: row for row in rows}
    result = []
    for rank, fid, score in ranked:
        chunk_id = mappings[fid]["chunk_id"]
        row = by_id[chunk_id]
        result.append(
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
                "model_id": manifest["model_id"],
                "expanded_query": expanded_question,
            }
        )
    return result[:top_k]


def vector_doctor(cfg: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        import faiss

        paths = cfg["paths"]
        manifest_path = Path(paths["vector_manifest"])
        index_path = Path(paths["faiss_index"])
        map_path = Path(paths["vector_map"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        index = _read_faiss_index(index_path)
        mappings = read_jsonl(map_path)
        chunk_ids = [row["chunk_id"] for row in mappings]
        with sqlite3.connect(paths["database"]) as connection:
            db_ids = {row[0] for row in connection.execute("SELECT chunk_id FROM chunks")}
        checks.update(
            {
                "manifest_present": True,
                "index_type": type(index).__name__,
                "index_entries": int(index.ntotal),
                "index_dimensions": int(index.d),
                "mapping_rows": len(mappings),
                "mapping_unique_chunk_ids": len(set(chunk_ids)),
                "missing_db_chunk_ids": len(db_ids - set(chunk_ids)),
                "orphan_vector_chunk_ids": len(set(chunk_ids) - db_ids),
                "chunks_sha256_matches": manifest.get("chunks_sha256")
                == _sha256(paths["chunks_jsonl"]),
                "model_sha256_matches": manifest.get("model_sha256")
                == _sha256(
                    Path(paths["embedding_model_dir"])
                    / cfg.get("embedding", {}).get(
                        "model_filename", "model_qint8_avx512_vnni.onnx"
                    )
                ),
            }
        )
        checks["healthy"] = all(
            [
                checks["index_entries"] == len(db_ids),
                checks["index_dimensions"]
                == int(cfg.get("embedding", {}).get("dimensions", 384)),
                checks["mapping_rows"] == len(db_ids),
                checks["mapping_unique_chunk_ids"] == len(db_ids),
                checks["missing_db_chunk_ids"] == 0,
                checks["orphan_vector_chunk_ids"] == 0,
                checks["chunks_sha256_matches"],
                checks["model_sha256_matches"],
            ]
        )
    except Exception as exc:  # noqa: BLE001
        checks = {"healthy": False, "error": str(exc)}
    return checks
