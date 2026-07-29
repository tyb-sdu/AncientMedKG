import pytest

from export_review_overrides import reviewed_action, text_sha256


def test_reviewed_action_requires_corrected_text() -> None:
    page = {
        "page_id": "ancient:book:p000001",
        "book_id": "ancient:book",
        "physical_page": 1,
        "pdf_page_label": "1",
        "text": "原始文字",
        "filename": "book.pdf",
        "source_sha256": "a" * 64,
    }
    row = {
        "filename": "book.pdf",
        "physical_page": "1",
        "source_sha256": "a" * 64,
        "review_status": "corrected",
        "corrected_text": "修订文字",
        "review_note": "人工核对",
    }
    action = reviewed_action(row, page)
    assert action["corrected_text"] == "修订文字"
    assert action["original_text_sha256"] == text_sha256("原始文字")


def test_reviewed_action_rejects_unapproved_status() -> None:
    with pytest.raises(ValueError, match="无效审核状态"):
        reviewed_action({"review_status": "unreviewed"}, {})
