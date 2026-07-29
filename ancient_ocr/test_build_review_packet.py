from build_review_packet import candidate_rows, page_image_name, preview_for_row


def test_candidate_rows_filters_and_sorts_by_priority_score() -> None:
    rows = [
        {"priority": "P1", "priority_score": "35", "filename": "b.pdf", "physical_page": "2"},
        {"priority": "P2", "priority_score": "99", "filename": "c.pdf", "physical_page": "1"},
        {"priority": "P1", "priority_score": "55", "filename": "a.pdf", "physical_page": "3"},
    ]
    selected = candidate_rows(rows, "P1", 10)
    assert [row["filename"] for row in selected] == ["a.pdf", "b.pdf"]


def test_page_image_name_keeps_page_identity() -> None:
    name = page_image_name(4, {"filename": "本草纲目 卷十八.pdf", "physical_page": "232"})
    assert name.startswith("004_")
    assert name.endswith("_p000232.png")


def test_preview_prefers_layout_sidecar_text() -> None:
    row = {"book_id": "book", "physical_page": "2", "text_preview": "混排文本"}
    layout = {("book", 2): "右栏文本 左栏文本"}
    assert preview_for_row(row, layout) == "右栏文本 左栏文本"
