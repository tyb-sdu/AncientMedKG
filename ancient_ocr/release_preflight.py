#!/usr/bin/env python
"""Fail a public release when Git tracks private or generated project artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


FORBIDDEN_SUFFIXES = {
    ".arrow",
    ".bin",
    ".ckpt",
    ".db",
    ".faiss",
    ".gguf",
    ".h5",
    ".hdf5",
    ".index",
    ".joblib",
    ".key",
    ".log",
    ".npy",
    ".npz",
    ".onnx",
    ".parquet",
    ".pdf",
    ".pem",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
    ".pyc",
    ".safetensors",
    ".sqlite",
    ".sqlite3",
}
FORBIDDEN_NAMES = {".env"}
FORBIDDEN_PREFIXES = (
    ".conda/",
    ".conda-ancient-ocr/",
    ".venv/",
    ".venv-ocr/",
    "venv/",
    "corpus/",
    "models/",
    "runtime/",
    "app/data/",
    "app/logs/",
    "app/models/",
    "app/state/",
    "ancient_ocr/data/",
    "ancient_ocr/logs/",
    "ancient_ocr/output/",
    "ancient_ocr/ready_corpus/",
    "ancient_ocr/ready_data/",
    "ancient_ocr/state/",
    "ancient_ocr/test_data/",
    "ancient_ocr/test_output/",
    "setup/",
    "research_pipeline/output/",
    "discovery_pipeline/output/",
    "knowledge_graph/output/",
)
TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
NONPORTABLE_TEXT_PATTERNS = (
    (
        "absolute Windows user path",
        re.compile(r"(?i)\b[A-Z]:[/\\]Users[/\\][^/\\\s]+"),
    ),
    (
        "absolute data-volume user path",
        re.compile(r"(?i)(?<![\w.])/data\d*/[^/\s]+/"),
    ),
    (
        "SSH connection command",
        re.compile(r"(?im)^\s*(?:ssh|scp|sftp)\b[^\n]*(?:@|\s-p\s+\d+)"),
    ),
    ("IP address with port", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")),
)


def forbidden_reason(raw_path: str) -> str | None:
    normalized = raw_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    lowered = normalized.lower()
    if path.name.lower() in FORBIDDEN_NAMES or path.name.lower().startswith(".env."):
        return "environment file"
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return f"forbidden extension: {path.suffix.lower()}"
    for prefix in FORBIDDEN_PREFIXES:
        if lowered.startswith(prefix):
            return f"private/generated directory: {prefix.rstrip('/')}"
    if "__pycache__" in {part.lower() for part in path.parts}:
        return "Python cache"
    return None


def tracked_files(repository: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def preflight(repository: Path) -> dict[str, Any]:
    files = tracked_files(repository)
    violations = [
        {"path": path, "reason": reason}
        for path in files
        if (reason := forbidden_reason(path)) is not None
    ]
    for relative in files:
        path = repository / relative
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for reason, pattern in NONPORTABLE_TEXT_PATTERNS:
            if pattern.search(text):
                violations.append({"path": relative, "reason": reason})
    return {
        "repository": str(repository.resolve()),
        "tracked_files": len(files),
        "valid": not violations,
        "violations": violations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check tracked files before a public release")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = preflight(args.repository)
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"cannot inspect tracked files; run inside the actual Git repository: {message}"
        ) from error
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
