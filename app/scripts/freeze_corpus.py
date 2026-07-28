#!/usr/bin/env python
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    artifact_names = [
        "config.yaml",
        "data/documents.csv",
        "data/documents.jsonl",
        "data/pages.jsonl",
        "data/chunks.jsonl",
        "data/quality_issues.csv",
        "data/rag.db",
        "data/source_checksums_before.jsonl",
        "data/source_checksums_after.jsonl",
        "data/source_integrity.json",
        "data/independent_audit_30.json",
    ]
    artifacts = {}
    for name in artifact_names:
        path = ROOT / name
        artifacts[name] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }

    db_path = DATA / "rag.db"
    with sqlite3.connect(db_path) as connection:
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

    source_rows = [
        json.loads(line)
        for line in (DATA / "source_checksums_after.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    source_digest = hashlib.sha256()
    for row in sorted(source_rows, key=lambda item: item["source_filename"]):
        source_digest.update(
            f"{row['source_filename']}\t{row['sha256']}\n".encode("utf-8")
        )

    manifest = {
        "data_version": "modern-corpus-v1",
        "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "immutable_scope": "modern PDF-derived documents/pages/chunks and rag.db",
        "source_pdfs_modified": False,
        "source_pdf_count": len(source_rows),
        "source_pdf_set_sha256": source_digest.hexdigest(),
        "counts": counts,
        "database_quick_check": quick_check,
        "database_metadata": metadata,
        "database_schema": [
            {"type": row[0], "name": row[1], "sql": row[2]} for row in schema_rows
        ],
        "artifacts": artifacts,
    }
    out_dir = DATA / "freeze"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "modern_corpus_v1_manifest.json"
    out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"manifest": str(out), **counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
