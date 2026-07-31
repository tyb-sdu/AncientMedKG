import pytest

from extract_json_report import last_json_object


def test_extracts_final_top_level_object_after_log_lines() -> None:
    log = """2026-07-30 | INFO | command=doctor
2026-07-30 | INFO | doctor: {\"healthy\": true, \"old\": true}
{
  \"healthy\": true,
  \"ancient_corpus\": {\"healthy\": true}
}
2026-07-30 | INFO | complete
"""

    assert last_json_object(log) == {
        "healthy": True,
        "ancient_corpus": {"healthy": True},
    }


def test_rejects_log_without_json_object() -> None:
    with pytest.raises(ValueError, match="does not contain"):
        last_json_object("2026-07-30 | INFO | doctor failed")
