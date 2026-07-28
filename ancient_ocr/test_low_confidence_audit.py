from generate_low_confidence_audit import priority


def test_priority_prefers_domain_pages_with_poor_ocr() -> None:
    score, level, reason = priority(
        {
            "average_confidence": 0.48,
            "text": "汤火伤忍冬生肌",
            "payload_json": (
                '{"quality":{"visible_character_count":240,"cjk_character_ratio":0.22}}'
            ),
        }
    )
    assert score >= 60
    assert level == "P0"
    assert "核心项目词" in reason
