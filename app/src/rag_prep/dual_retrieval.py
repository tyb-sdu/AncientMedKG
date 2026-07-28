from __future__ import annotations

from typing import Any

from .ancient_qwen_retrieval import (
    ancient_qwen_doctor,
    query_ancient_qwen_reranked_hybrid,
    query_ancient_qwen_vector,
)
from .ancient_retrieval import ancient_doctor, query_ancient_keyword, source_ancient_page
from .search import _diversify_results, query_modern_retrieval, rrf_fuse, source_page


def query_any_corpus(
    cfg: dict[str, Any],
    question: str,
    retrieval: str,
    top_k: int,
    *,
    mode: str = "modern",
) -> list[dict[str, Any]]:
    if mode == "modern":
        return query_modern_retrieval(cfg, question, retrieval, top_k)
    if mode == "ancient":
        if retrieval == "keyword":
            return _diversify_results(cfg, query_ancient_keyword(cfg, question, top_k), top_k)
        if retrieval == "qwen-vector":
            return _diversify_results(cfg, query_ancient_qwen_vector(cfg, question, top_k), top_k)
        if retrieval == "qwen-reranked-hybrid":
            return query_ancient_qwen_reranked_hybrid(cfg, question, top_k)
        raise ValueError("古籍当前仅支持 keyword / qwen-vector / qwen-reranked-hybrid")
    if mode == "dual":
        candidate_count = min(
            max(top_k * 4, top_k),
            int(cfg.get("search", {}).get("max_top_k", 100)),
        )
        modern_rows = query_modern_retrieval(cfg, question, retrieval, candidate_count)
        if retrieval == "keyword":
            ancient_rows = query_ancient_keyword(cfg, question, candidate_count)
        elif retrieval == "qwen-vector":
            ancient_rows = query_ancient_qwen_vector(cfg, question, candidate_count)
        elif retrieval == "qwen-reranked-hybrid":
            ancient_rows = query_ancient_qwen_reranked_hybrid(cfg, question, candidate_count)
        else:
            raise ValueError("dual 模式当前仅支持 keyword / qwen-vector / qwen-reranked-hybrid")
        fused = rrf_fuse(
            modern_rows,
            ancient_rows,
            top_k=candidate_count * 2,
            rrf_k=int(cfg.get("search", {}).get("dual_rrf_k", 60)),
            keyword_weight=float(cfg.get("search", {}).get("dual_modern_weight", 1.0)),
            vector_weight=float(cfg.get("search", {}).get("dual_ancient_weight", 1.0)),
        )
        results = _diversify_results(cfg, fused, top_k)
        if ancient_rows and not any(row.get("corpus") == "ancient" for row in results):
            fallback = dict(ancient_rows[0])
            results = results[: max(top_k - 1, 0)] + [fallback]
            for rank, row in enumerate(results, 1):
                row["fusion_rank"] = rank
        return results
    raise ValueError(f"未知语料模式: {mode}")


def source_any_page(
    cfg: dict[str, Any],
    doc_id: str,
    page: int,
    *,
    mode: str = "auto",
) -> dict[str, Any] | None:
    if mode not in {"auto", "modern", "ancient"}:
        raise ValueError(f"未知 source 模式: {mode}")
    if mode in {"auto", "ancient"} and doc_id.startswith("ancient:"):
        return source_ancient_page(cfg, doc_id, page)
    row = source_page(cfg, doc_id, page)
    if not row:
        return None
    row["corpus"] = "modern"
    row["record_type"] = "chunk_page"
    row["page_label"] = row.get("page_label") or str(row["pdf_page"])
    return row


def doctor_any_corpus(modern_checks: dict[str, Any], cfg: dict[str, Any], *, deep: bool) -> dict[str, Any]:
    if deep and "ancient_database" in cfg.get("paths", {}):
        modern_checks["ancient_corpus"] = ancient_doctor(cfg)
        if "ancient_qwen_vector_dir" in cfg.get("paths", {}):
            modern_checks["ancient_qwen_vector"] = ancient_qwen_doctor(cfg)
    return modern_checks
