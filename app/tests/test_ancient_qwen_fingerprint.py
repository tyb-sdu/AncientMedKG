from copy import deepcopy

from rag_prep.ancient_qwen_retrieval import (
    _apply_lexical_recall_guard,
    _corpus_text_sha256,
)


def test_corpus_text_sha256_tracks_retrieval_corpus_fields() -> None:
    pages = [
        {"page_id": "book-a:p0001", "title": "书甲", "text": "原文甲"},
        {"page_id": "book-a:p0002", "title": "书甲", "text": "原文乙"},
    ]

    baseline = _corpus_text_sha256(pages)
    assert baseline == _corpus_text_sha256(deepcopy(pages))

    for field, value in (
        ("page_id", "book-a:p9999"),
        ("title", "书乙"),
        ("text", "修订正文"),
    ):
        changed = deepcopy(pages)
        changed[0][field] = value
        assert _corpus_text_sha256(changed) != baseline


def test_lexical_recall_guard_preserves_exact_keyword_top_k_set() -> None:
    cfg = {"search": {"max_chunks_per_document": 2}}
    keyword = [
        {"chunk_id": "a1", "doc_id": "a", "corpus": "ancient"},
        {"chunk_id": "a2", "doc_id": "a", "corpus": "ancient"},
        {"chunk_id": "a3", "doc_id": "a", "corpus": "ancient"},
        {"chunk_id": "b1", "doc_id": "b", "corpus": "ancient"},
    ]
    ranked = [
        {"chunk_id": "v1", "doc_id": "v", "reranker_score": 10.0},
        {"chunk_id": "b1", "doc_id": "b", "reranker_score": 0.7},
        {"chunk_id": "a2", "doc_id": "a", "reranker_score": 0.6},
        {"chunk_id": "a1", "doc_id": "a", "reranker_score": 0.5},
        {"chunk_id": "a3", "doc_id": "a", "reranker_score": 0.4},
    ]
    result = _apply_lexical_recall_guard(cfg, keyword, ranked, top_k=3)
    assert {row["chunk_id"] for row in result} == {"a1", "a2", "a3"}
    assert all(row["lexical_recall_guard"] for row in result)
