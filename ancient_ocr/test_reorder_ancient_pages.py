from reorder_ancient_pages import extract_text_boxes, order_text_boxes


def test_extracts_paddle_parallel_result() -> None:
    payload = {
        "dt_polys": [[[90, 10], [100, 10], [100, 30], [90, 30]], [[10, 10], [20, 10], [20, 30], [10, 30]]],
        "rec_texts": ["右栏", "左栏"],
        "rec_scores": [0.9, 0.9],
    }
    assert [item["text"] for item in extract_text_boxes(payload)] == ["右栏", "左栏"]


def test_extracts_normalized_segments() -> None:
    payload = {
        "segments": [
            {"text": "甲", "bbox": [0, 0, 10, 10], "score": 0.95},
            {"text": "乙", "polygon": [[20, 0], [30, 0], [30, 10], [20, 10]]},
        ]
    }
    assert [item["text"] for item in extract_text_boxes(payload)] == ["甲", "乙"]


def test_horizontal_columns_are_left_to_right() -> None:
    records = [
        {"text": "右栏上", "box": (110, 0, 120, 10)},
        {"text": "左栏上", "box": (10, 0, 20, 10)},
        {"text": "右栏下", "box": (110, 20, 120, 30)},
        {"text": "左栏下", "box": (10, 20, 20, 30)},
    ]
    text, columns, status = order_text_boxes(records, "horizontal-ltr")
    assert columns == 2
    assert status == "ordered"
    assert text.splitlines() == ["左栏上", "左栏下", "右栏上", "右栏下"]


def test_vertical_columns_are_right_to_left() -> None:
    records = [
        {"text": "左栏", "box": (10, 0, 20, 10)},
        {"text": "右栏", "box": (110, 0, 120, 10)},
    ]
    text, columns, status = order_text_boxes(records, "vertical-rtl")
    assert columns == 2
    assert status == "ordered"
    assert text.splitlines() == ["右栏", "左栏"]


def test_vertical_order_does_not_sort_by_page_y_first() -> None:
    records = [
        {"text": "右栏上", "box": (110, 10, 120, 20)},
        {"text": "左栏上", "box": (10, 0, 20, 10)},
        {"text": "右栏下", "box": (110, 40, 120, 50)},
        {"text": "左栏下", "box": (10, 50, 20, 60)},
    ]
    text, _, _ = order_text_boxes(records, "vertical-rtl")
    assert text.splitlines() == ["右栏上", "右栏下", "左栏上", "左栏下"]


def test_no_boxes_is_explicit() -> None:
    text, columns, status = order_text_boxes([], "horizontal-ltr")
    assert text == ""
    assert columns == 0
    assert status == "no_boxes"
