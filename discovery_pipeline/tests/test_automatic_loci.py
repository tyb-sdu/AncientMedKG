from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from discovery_pipeline.automatic_loci import (
    AutomaticLocusError,
    filter_loci_automatically,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _database(path: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                relevance_score REAL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                pdf_page INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            """
        )
        rows = [
            ("chunk:burn", "doc:burn", 3, "Chlorogenic acid improved burn wound repair.", 50.0, "a" * 64),
            ("chunk:wound", "doc:wound", 5, "Rutin was studied in wound healing.", 100.0, "b" * 64),
            ("chunk:only", "doc:only", 7, "Rutin content was measured by HPLC.", 100.0, "c" * 64),
        ]
        records: list[dict[str, object]] = []
        for chunk_id, doc_id, page, text, relevance, source_sha in rows:
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?)",
                (doc_id, source_sha, relevance),
            )
            connection.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                (chunk_id, doc_id, page, text),
            )
            term = "chlorogenic acid" if "Chlorogenic" in text else "rutin"
            context = (
                "burn_context"
                if "burn" in text
                else "wound_context"
                if "wound" in text
                else "compound_only"
            )
            records.append(
                {
                    "locus_id": f"locus:{chunk_id}",
                    "candidate_id": f"compound:{term.replace(' ', '_')}",
                    "matched_terms": [term],
                    "context_class": context,
                    "context_terms": [],
                    "doc_id": doc_id,
                    "source_sha256": source_sha,
                    "pdf_page": page,
                    "chunk_id": chunk_id,
                    "chunk_text_sha256": _sha(text),
                    "document_relevance_score": relevance,
                }
            )
        connection.commit()
        return records
    finally:
        connection.close()


def _write_loci(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            for value in records
        ),
        encoding="utf-8",
    )


def test_automatic_loci_approves_domain_context_and_discards_compound_only() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "rag.db"
        loci = root / "loci.jsonl"
        records = _database(database)
        _write_loci(loci, records)
        report = filter_loci_automatically(
            loci_path=loci,
            database_path=database,
            output_dir=root / "automatic",
            threshold=0.7,
            approved_at="2026-08-02",
        )
        assert report["valid"] is True
        assert report["approved_loci"] == 2
        assert report["discarded_loci"] == 1
        assert report["human_review_required"] is False
        approved = [
            json.loads(line)
            for line in (root / "automatic" / "approved_loci.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert all(value["candidate_confidence"] >= 0.7 for value in approved)
        assert all(value["human_reviewed"] is False for value in approved)
        assert all(value["scientific_evidence_approved"] is False for value in approved)
        discarded = json.loads(
            (root / "automatic" / "discarded_loci.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        assert discarded["automatic_decision_reasons"] == [
            "no_burn_or_wound_context"
        ]


def test_source_mismatch_is_discarded_with_zero_confidence() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "rag.db"
        loci = root / "loci.jsonl"
        records = _database(database)[:1]
        records[0]["chunk_text_sha256"] = "0" * 64
        _write_loci(loci, records)
        report = filter_loci_automatically(
            loci_path=loci,
            database_path=database,
            output_dir=root / "automatic",
        )
        assert report["approved_loci"] == 0
        assert report["discard_reason_counts"] == {
            "chunk_text_sha256_mismatch": 1
        }
        discarded = json.loads(
            (root / "automatic" / "discarded_loci.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        assert discarded["candidate_confidence"] == 0.0


def test_duplicate_locus_ids_fail_before_outputs_are_written() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "rag.db"
        loci = root / "loci.jsonl"
        records = _database(database)[:1]
        _write_loci(loci, records + records)
        with pytest.raises(AutomaticLocusError, match="duplicate locus_id"):
            filter_loci_automatically(
                loci_path=loci,
                database_path=database,
                output_dir=root / "automatic",
            )
        assert not (root / "automatic" / "approved_loci.jsonl").exists()
