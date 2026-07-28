from ancient_cli import normalize_native_text, order_segments, stable_book_id


def segment(text: str, box: list[float]) -> dict:
    x1, y1, x2, y2 = box
    return {
        "text": text,
        "confidence": 0.99,
        "box": box,
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    }


def test_vertical_columns_are_right_to_left_and_top_to_bottom() -> None:
    items = [
        segment("左列", [10, 10, 30, 180]),
        segment("右下", [100, 100, 120, 180]),
        segment("右上", [101, 10, 121, 90]),
    ]
    ordered, direction = order_segments(items, page_width=200)
    assert direction == "vertical-rtl"
    assert [item["text"] for item in ordered] == ["右上", "右下", "左列"]


def test_horizontal_lines_are_top_to_bottom() -> None:
    items = [
        segment("第二行", [10, 80, 180, 100]),
        segment("第一行", [10, 10, 180, 30]),
    ]
    ordered, direction = order_segments(items, page_width=200)
    assert direction == "horizontal-ltr"
    assert [item["text"] for item in ordered] == ["第一行", "第二行"]


def test_native_text_normalization_preserves_line_boundaries() -> None:
    assert normalize_native_text("甲  乙\r\n\r\n 丙\t丁 ") == "甲 乙\n\n丙 丁"


def test_book_id_is_stable_and_namespaced() -> None:
    assert stable_book_id("a" * 64) == "ancient:" + "a" * 20
