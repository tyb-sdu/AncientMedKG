from paddleocr_vl_candidates import (
    build_candidate,
    candidate_analysis,
    candidate_recommendation,
    candidate_review_flags,
    candidate_text,
    ordered_blocks,
    text_quality,
)


def test_blocks_follow_vl_order_and_skip_empty_content() -> None:
    payload = {
        "parsing_res_list": [
            {"block_id": 3, "block_order": 2, "block_content": "second"},
            {"block_id": 1, "block_order": 1, "block_content": "first"},
            {"block_id": 2, "block_order": None, "block_content": "last"},
            {"block_id": 0, "block_order": 0, "block_content": ""},
        ]
    }
    text, blocks = candidate_text(payload)
    assert text == "first\nsecond\nlast"
    assert [block["block_id"] for block in blocks] == [1, 3, 2]


def test_text_quality_tracks_cjk_and_ascii_noise() -> None:
    quality = text_quality("abc\n金银花")
    assert quality["visible_character_count"] == 6
    assert quality["ascii_noise_count"] == 3
    assert quality["cjk_character_ratio"] == 0.5
    assert quality["kana_character_count"] == 0


def test_image_markup_is_removed_but_figure_title_is_kept() -> None:
    payload = {
        "parsing_res_list": [
            {
                "block_id": 1,
                "block_order": None,
                "block_label": "image",
                "block_content": '<img src="page.jpg" alt="Image" />',
            },
            {
                "block_id": 2,
                "block_order": None,
                "block_label": "figure_title",
                "block_content": '<div style="text-align:center">人身背面全图</div>',
            },
        ]
    }
    text, blocks = candidate_text(payload)
    assert text == "人身背面全图"
    assert [block["block_label"] for block in blocks] == ["figure_title"]


def test_repetition_and_kana_are_flagged() -> None:
    original = text_quality("金银花清热解毒" * 20)
    candidate = text_quality("とは、その" * 30)
    flags = candidate_review_flags(original, candidate)
    assert "low_cjk_ratio" in flags
    assert "kana_noise" in flags
    assert "repeated_text" in flags


def test_non_text_page_stays_manual() -> None:
    original = text_quality("金银花清热解毒" * 20)
    candidate = text_quality("金银花清热解毒" * 20)
    assert candidate_recommendation(original, candidate, 1) == "manual_compare_required"


def test_fallback_render_is_always_flagged_for_manual_review() -> None:
    payload = {
        "parsing_res_list": [
            {"block_id": 1, "block_order": 1, "block_content": "金银花清热解毒"}
        ]
    }
    analysis = candidate_analysis(payload, "金银花清热解毒", "pymupdf_fallback")
    assert "render_fallback" in analysis["review_flags"]
    assert analysis["recommendation"] == "manual_compare_required"


def test_vl_candidate_requires_review_even_when_viable() -> None:
    original = {"visible_character_count": 100, "cjk_character_ratio": 0.95}
    candidate = {"visible_character_count": 90, "cjk_character_ratio": 0.96}
    assert candidate_recommendation(original, candidate) == "vl_candidate_ready_for_review"


def test_vl_generation_limit_is_passed_to_predict(tmp_path) -> None:
    class Result:
        def json(self):
            return {"parsing_res_list": [{"block_id": 1, "block_order": 1, "block_content": "金银花"}]}

    class Pipeline:
        def __init__(self) -> None:
            self.kwargs = None

        def predict(self, _path, **kwargs):
            self.kwargs = kwargs
            return [Result()]

    pipeline = Pipeline()
    page = {
        "page_id": "page-1",
        "book_id": "ancient:test",
        "filename": "test.pdf",
        "physical_page": 1,
        "pdf_page_label": "1",
        "source_path": "test.pdf",
        "source_sha256": "a" * 64,
        "text": "金银花",
        "reading_direction": "vertical_rtl",
    }
    audit = {"priority": "P1", "priority_score": "10"}
    config = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_layout_detection": None,
        "format_block_content": True,
        "max_new_tokens": 4096,
    }
    build_candidate(pipeline, page, audit, tmp_path / "page.png", config)
    assert pipeline.kwargs["max_new_tokens"] == 4096
    assert set(pipeline.kwargs) == {"max_new_tokens"}
