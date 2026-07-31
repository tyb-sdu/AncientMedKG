from audit_layout_quality import _risk_row


def test_flags_low_confidence_and_noise() -> None:
    row = {
        "page_id": "p1",
        "book_id": "b1",
        "physical_page": 1,
        "pdf_page_label": "1",
        "reading_direction": "vertical-rtl",
        "average_confidence": 0.7,
        "low_confidence": 1,
        "text": "乱码",
        "payload_json": '{"segments":[{"text":"abc�","box":[10,0,20,50],"confidence":0.5,"order":0}]}',
    }
    result = _risk_row(row, {})
    assert result["priority"] in {"P1", "P2"}
    assert "low_segment_confidence" in result["ocr_reason"]
