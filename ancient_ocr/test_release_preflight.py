import subprocess
from pathlib import Path

from release_preflight import FORBIDDEN_PREFIXES, FORBIDDEN_SUFFIXES, forbidden_reason, preflight


def test_private_and_generated_artifacts_are_rejected() -> None:
    cases = {
        "corpus/modern_pdf/article.pdf": "forbidden extension",
        "ancient_ocr/data/ancient_rag.db": "forbidden extension",
        "ancient_ocr/output/candidates/page.json": "private/generated directory",
        "app/models/reranker/config.json": "private/generated directory",
        "app/logs/doctor-deep.json": "private/generated directory",
        "ancient_ocr/logs/promotion.txt": "private/generated directory",
        "setup/doctor-deep.json": "private/generated directory",
        "app/__pycache__/config.cpython-312.pyc": "forbidden extension",
        ".env": "environment file",
        ".env.staging": "environment file",
        ".conda/bin/python": "private/generated directory",
        "keys/server.pem": "forbidden extension",
        "weights/model.onnx": "forbidden extension",
        "indexes/pages.faiss": "forbidden extension",
        "exports/pages.parquet": "forbidden extension",
    }
    for path, expected in cases.items():
        assert expected in str(forbidden_reason(path))


def test_public_code_tests_and_docs_are_allowed() -> None:
    allowed = (
        "README.md",
        "PROJECT_STATUS.md",
        "app/scripts/evaluate_ancient_retrieval.py",
        "app/evaluation/ancient_questions_v1.json",
        "ancient_ocr/test_release_preflight.py",
        "ancient_ocr/verify_candidate_manifest.py",
    )
    assert all(forbidden_reason(path) is None for path in allowed)


def test_preflight_rejects_a_tracked_runtime_log_directory(tmp_path) -> None:
    runtime_log = tmp_path / "app" / "logs" / "doctor-deep.json"
    runtime_log.parent.mkdir(parents=True)
    runtime_log.write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "app/logs/doctor-deep.json"],
        check=True,
    )

    report = preflight(tmp_path)

    assert report["valid"] is False
    assert report["violations"] == [
        {
            "path": "app/logs/doctor-deep.json",
            "reason": "private/generated directory: app/logs",
        }
    ]


def test_preflight_accepts_tracked_public_code(tmp_path) -> None:
    script = tmp_path / "app" / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Public package\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "README.md", "app/scripts/check.py"],
        check=True,
    )

    report = preflight(tmp_path)

    assert report["valid"] is True
    assert report["violations"] == []


def test_gitignore_covers_release_preflight_artifact_rules() -> None:
    ignored = set(
        (Path(__file__).resolve().parent.parent / ".gitignore")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert {f"*{suffix}" for suffix in FORBIDDEN_SUFFIXES} <= ignored
    assert {f"/{prefix}" for prefix in FORBIDDEN_PREFIXES} <= ignored
    assert {".env", ".env.*"} <= ignored
