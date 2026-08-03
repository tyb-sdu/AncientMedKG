from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from discovery_pipeline.structured_evidence import structure_modern_evidence


def test_structuring_keeps_only_source_verified_semantic_evidence(tmp_path: Path) -> None:
    database = tmp_path / "rag.db"
    text_good = (
        "Chlorogenic acid accelerated wound healing in rats at 20 mg/kg, "
        "reduced inflammation through the Nrf2/HO-1 pathway."
    )
    text_weak = "Chlorogenic acid content was measured by HPLC."
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY, title TEXT, year TEXT, doi TEXT,
                source_filename TEXT, sha256 TEXT
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY, doc_id TEXT, pdf_page INTEGER, text TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            ("doc:1", "Burn study", "2024", "10.1/test", "study.pdf", "a" * 64),
        )
        connection.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?)",
            [("chunk:good", "doc:1", 2, text_good), ("chunk:weak", "doc:1", 3, text_weak)],
        )
    loci = tmp_path / "approved.jsonl"
    records = []
    for chunk_id, page, text in (("chunk:good", 2, text_good), ("chunk:weak", 3, text_weak)):
        records.append(
            {
                "locus_id": f"locus:{chunk_id}",
                "candidate_id": "compound:chlorogenic_acid",
                "candidate_confidence": 0.9,
                "doc_id": "doc:1",
                "pdf_page": page,
                "chunk_id": chunk_id,
                "source_sha256": "a" * 64,
                "chunk_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    loci.write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    report = structure_modern_evidence(
        approved_loci_path=loci,
        database_path=database,
        output_dir=tmp_path / "structured",
        threshold=0.7,
    )
    assert report["approved_structured_evidence"] == 1
    assert report["discarded_after_structuring"] == 1
    assert report["target_counts"] == {"HMOX1": 1, "NFE2L2": 1}
    assert report["pathway_counts"] == {"pathway:nrf2_ho1": 1}
    approved = json.loads(
        (tmp_path / "structured" / "approved_structured_evidence.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert approved["structured_fields"]["study_type"] == "animal"
    assert approved["structured_fields"]["doses"] == ["20 mg/kg"]
    assert approved["human_reviewed"] is False
