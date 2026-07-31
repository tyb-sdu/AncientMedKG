from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from discovery_pipeline.corpus_scan import scan_corpus
from discovery_pipeline.doctor import validate_discovery_intake
from discovery_pipeline.pubchem import resolve_catalog


class _Response:
    def __init__(self) -> None:
        self.payload = json.dumps(
            {
                "PropertyTable": {
                    "Properties": [
                        {
                            "CID": 123,
                            "Title": "Example",
                            "MolecularFormula": "C1H2",
                            "MolecularWeight": "14.03",
                            "InChIKey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C",
                            "ConnectivitySMILES": "C",
                            "SMILES": "[CH4]",
                        }
                    ]
                }
            }
        ).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _opener(_request: object, timeout: float) -> _Response:
    assert timeout == 30.0
    return _Response()


class DoctorTests(unittest.TestCase):
    def _artifacts(self, root: Path) -> dict[str, Path]:
        catalog = {
            "schema_version": 1,
            "catalog_id": "test",
            "candidates": [
                {
                    "candidate_id": "compound:example",
                    "canonical_name": "example",
                    "name_zh": "",
                    "aliases": [],
                    "expected_pubchem_cid": 123,
                }
            ],
        }
        catalog_path = root / "catalog.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        cache = root / "cache"
        resolution = resolve_catalog(
            catalog,
            cache_dir=cache,
            delay_seconds=0,
            opener=_opener,
        )
        resolution_path = root / "resolution.json"
        resolution_path.write_text(json.dumps(resolution), encoding="utf-8")

        database = root / "rag.db"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE documents (
                    doc_id TEXT PRIMARY KEY, title TEXT, year TEXT, doi TEXT,
                    source_filename TEXT, sha256 TEXT, relevance_score INTEGER,
                    topic_tags TEXT
                );
                CREATE TABLE chunks (
                    chunk_id TEXT PRIMARY KEY, doc_id TEXT, pdf_page INTEGER,
                    chunk_index INTEGER, text TEXT, normalized_text TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("doc:1", "Example", "2026", "", "one.pdf", "a" * 64, 100, "[]"),
            )
            connection.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
                ("chunk:1", "doc:1", 2, 0, "example wound", "example wound"),
            )
            connection.commit()
        finally:
            connection.close()
        output = root / "scan"
        scan_corpus(database, catalog, output)
        return {
            "catalog_path": catalog_path,
            "resolution_path": resolution_path,
            "coverage_summary_path": output / "compound_coverage_summary.json",
            "loci_path": output / "compound_loci.jsonl",
            "database_path": database,
            "cache_dir": cache,
        }

    def test_valid_intake_has_integrity_but_is_not_scientific_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = self._artifacts(Path(temporary))
            report = validate_discovery_intake(**artifacts)
            self.assertTrue(report["valid"])
            self.assertTrue(report["computational_intake_complete"])
            self.assertFalse(report["scientific_release_ready"])
            self.assertEqual(report["counts"]["retrieval_loci"], 1)

    def test_tampered_loci_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = self._artifacts(Path(temporary))
            artifacts["loci_path"].write_text(
                artifacts["loci_path"].read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            report = validate_discovery_intake(**artifacts)
            self.assertFalse(report["valid"])
            codes = {value["code"] for value in report["issues"]}
            self.assertIn("coverage_loci_sha256_mismatch", codes)
            self.assertIn("locus_blank_line", codes)


if __name__ == "__main__":
    unittest.main()
