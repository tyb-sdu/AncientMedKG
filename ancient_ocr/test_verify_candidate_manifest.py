import csv
import hashlib

from verify_candidate_manifest import EMPTY_TEXT_SHA256, verify_manifest


def make_row(source_sha256: str, physical_page: int = 7) -> dict[str, str]:
    book_id = f"ancient:{source_sha256[:20]}"
    page_name = f"page_{physical_page:06d}"
    return {
        "book_id": book_id,
        "physical_page": str(physical_page),
        "source_sha256": source_sha256,
        "original_text_sha256": "b" * 64,
        "candidate_text_sha256": "c" * 64,
        "candidate_path": f"candidates/{book_id.replace(':', '_')}/{page_name}.json",
        "image_path": f"rendered/{book_id.replace(':', '_')}/{page_name}.png",
        "review_flags": "",
    }


def write_manifest(path, rows) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_valid_manifest_is_accepted(tmp_path) -> None:
    path = tmp_path / "manifest.csv"
    write_manifest(path, [make_row("a" * 64)])

    report = verify_manifest(path)

    assert report["valid"] is True
    assert report["rows"] == 1
    assert report["issues"] == []


def test_invalid_page_and_path_are_reported(tmp_path) -> None:
    path = tmp_path / "manifest.csv"
    row = make_row("a" * 64, physical_page=2)
    row["physical_page"] = "0"
    row["candidate_path"] = "candidates/wrong.json"
    write_manifest(path, [row])

    report = verify_manifest(path)

    assert report["valid"] is False
    assert any("physical_page must be positive" in issue for issue in report["issues"])
    assert any("candidate_path" in issue for issue in report["issues"])


def test_duplicate_empty_candidates_require_empty_flag(tmp_path) -> None:
    path = tmp_path / "manifest.csv"
    rows = [make_row("a" * 64, physical_page=1), make_row("a" * 64, physical_page=2)]
    for row in rows:
        row["candidate_text_sha256"] = EMPTY_TEXT_SHA256
        row["review_flags"] = "empty_candidate"
    write_manifest(path, rows)

    report = verify_manifest(path)

    assert report["valid"] is True
    assert report["duplicate_candidate_text_hash_groups"] == [
        {
            "candidate_text_sha256": hashlib.sha256(b"").hexdigest(),
            "rows": 2,
            "all_empty_flagged": True,
        }
    ]


def test_empty_or_incomplete_manifest_is_rejected(tmp_path) -> None:
    path = tmp_path / "manifest.csv"
    path.write_text("book_id,physical_page\n", encoding="utf-8")

    report = verify_manifest(path)

    assert report["valid"] is False
    assert any("missing required columns" in issue for issue in report["issues"])
    assert "manifest contains no candidate rows" in report["issues"]
