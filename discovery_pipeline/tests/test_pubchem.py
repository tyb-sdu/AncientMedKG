from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from discovery_pipeline.pubchem import (
    PubChemResolutionError,
    resolve_candidate,
    resolve_catalog,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _opener(_request: object, timeout: float) -> _Response:
    assert timeout == 30.0
    return _Response(
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
    )


class PubChemTests(unittest.TestCase):
    def test_resolution_records_identity_and_response_hash(self) -> None:
        result = resolve_candidate(
            {
                "candidate_id": "compound:example",
                "canonical_name": "example compound",
                "expected_pubchem_cid": 123,
            },
            opener=_opener,
        )
        self.assertEqual(result["pubchem"]["cid"], 123)
        self.assertEqual(
            result["pubchem"]["identity_status"],
            "resolved_requires_curator_review",
        )
        self.assertEqual(len(result["pubchem"]["response_sha256"]), 64)

    def test_expected_cid_mismatch_is_rejected(self) -> None:
        with self.assertRaises(PubChemResolutionError):
            resolve_candidate(
                {
                    "candidate_id": "compound:example",
                    "canonical_name": "example compound",
                    "expected_pubchem_cid": 999,
                },
                opener=_opener,
            )

    def test_raw_cache_is_verifiable_and_never_overwritten(self) -> None:
        catalog = {
            "catalog_id": "test",
            "candidates": [
                {
                    "candidate_id": "compound:example",
                    "canonical_name": "example compound",
                    "expected_pubchem_cid": 123,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            report = resolve_catalog(
                catalog,
                cache_dir=cache,
                delay_seconds=0,
                opener=_opener,
            )
            raw = cache / "compound_example.response.json"
            self.assertTrue(raw.is_file())
            self.assertEqual(
                report["candidates"][0]["pubchem"]["response_sha256"],
                hashlib.sha256(raw.read_bytes()).hexdigest(),
            )
            with self.assertRaises(FileExistsError):
                resolve_catalog(
                    catalog,
                    cache_dir=cache,
                    delay_seconds=0,
                    opener=_opener,
                )


if __name__ == "__main__":
    unittest.main()
