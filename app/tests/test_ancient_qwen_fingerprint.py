from copy import deepcopy

from rag_prep.ancient_qwen_retrieval import _corpus_text_sha256


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
