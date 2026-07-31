from pathlib import Path

from second_pass_ocr import candidate_path, recommendation, select_audit_rows


def test_candidate_path_is_page_addressable(tmp_path: Path) -> None:
    path = candidate_path(tmp_path, "ancient:abc", 9)
    assert path == tmp_path / "candidates" / "ancient_abc" / "page_000009.json"


def test_recommendation_never_auto_applies_text() -> None:
    assert recommendation(0.70, 0.95, 0.74, 0.95) == "candidate_preferred_for_review"
    assert recommendation(0.70, 0.95, 0.72, 0.94) == "manual_compare_required"


def test_select_audit_rows_deduplicates_and_sorts(tmp_path: Path) -> None:
    audit = tmp_path / "audit.csv"
    audit.write_text(
        "priority,priority_score,book_id,filename,physical_page\n"
        "P1,20,ancient:a,b.pdf,4\n"
        "P1,40,ancient:b,a.pdf,2\n"
        "P1,30,ancient:a,b.pdf,4\n"
        "P2,50,ancient:c,c.pdf,1\n",
        encoding="utf-8",
    )
    rows = select_audit_rows(audit, "P1", None)
    assert [(row["book_id"], row["physical_page"]) for row in rows] == [
        ("ancient:b", "2"),
        ("ancient:a", "4"),
    ]
