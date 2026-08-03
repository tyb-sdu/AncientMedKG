#!/usr/bin/env python
"""Create a portable integrity manifest for the modern RAG corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from rag_prep.config import load_config  # noqa: E402


ARTIFACT_KEYS = (
    "documents_csv",
    "documents_jsonl",
    "pages_jsonl",
    "chunks_jsonl",
    "quality_issues_csv",
    "database",
    "source_checksums_before",
    "source_checksums_after",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required corpus artifact is missing: {path}")
    return {"size_bytes": path.stat().st_size, "sha256": sha256(path)}


def build_manifest(config_path: Path) -> tuple[dict[str, Any], Path]:
    cfg = load_config(config_path)
    paths = cfg["paths"]
    artifacts = {key: _artifact(Path(paths[key])) for key in ARTIFACT_KEYS}
    artifacts["config"] = _artifact(config_path.resolve())

    database = Path(paths["database"])
    with sqlite3.connect(database) as connection:
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("documents", "pages", "chunks", "chunks_fts")
        }
        schema_rows = connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger', 'view')
            ORDER BY type, name
            """
        ).fetchall()
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]

    checksum_rows = [
        json.loads(line)
        for line in Path(paths["source_checksums_after"])
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    source_digest = hashlib.sha256()
    for row in sorted(checksum_rows, key=lambda item: item["source_filename"]):
        source_digest.update(
            f"{row['source_filename']}\t{row['sha256']}\n".encode("utf-8")
        )

    manifest = {
        "schema_version": 1,
        "data_version": "modern-corpus-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "immutable_scope": "modern PDF-derived documents, pages, chunks and rag.db",
        "source_pdfs_modified": False,
        "source_pdf_count": len(checksum_rows),
        "source_pdf_set_sha256": source_digest.hexdigest(),
        "counts": counts,
        "database_quick_check": quick_check,
        "database_metadata": metadata,
        "database_schema": [
            {"type": row[0], "name": row[1], "sql": row[2]}
            for row in schema_rows
        ],
        "artifacts": artifacts,
    }
    output = Path(paths["data_dir"]) / "freeze" / "modern_corpus_v1_manifest.json"
    return manifest, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=APP_ROOT / "config.yaml")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    manifest, default_output = build_manifest(args.config)
    output = (args.output or default_output).resolve()
    if output.exists() and not args.force:
        raise FileExistsError(f"refusing to overwrite manifest: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    print(
        json.dumps(
            {"manifest": str(output), **manifest["counts"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
