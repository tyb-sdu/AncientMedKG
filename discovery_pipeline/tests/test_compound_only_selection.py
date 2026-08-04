from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from discovery_pipeline.compound_only_selection import (
    CompoundOnlyInputError,
    select_compounds_without_mechanism,
    verify_compound_only_sources,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "discovery_pipeline" / "data" / "compound_screening_v2.json"
CATALOG_PATH = ROOT / "discovery_pipeline" / "data" / "compound_candidates_v1.json"


def _artifacts() -> tuple[dict, bytes, bytes, bytes]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    catalog = CATALOG_PATH.read_bytes()
    candidate_ids = [
        row["candidate_id"]
        for row in json.loads(catalog.decode("utf-8"))["candidates"]
    ]
    burn_counts = {
        "compound:chlorogenic_acid": 21,
        "compound:glycyrrhizic_acid": 18,
        "compound:caffeic_acid": 8,
        "compound:luteolin": 7,
        "compound:rutin": 6,
        "compound:glycyrrhetinic_acid": 6,
        "compound:sweroside": 0,
    }
    coverage_rows = []
    pubchem_rows = []
    for index, candidate_id in enumerate(candidate_ids, start=1):
        burn = burn_counts.get(candidate_id, 1)
        wound = max(burn, burn + 4)
        documents = wound + 10
        coverage_rows.append(
            {
                "candidate_id": candidate_id,
                "document_count": documents,
                "chunk_count": documents + 20,
                "wound_or_burn_document_count": wound,
                "wound_or_burn_chunk_count": wound + 5,
                "burn_document_count": burn,
                "burn_chunk_count": burn + 1 if burn else 0,
            }
        )
        pubchem_rows.append(
            {
                "candidate_id": candidate_id,
                "pubchem": {
                    "cid": index,
                    "title": candidate_id,
                    "molecular_formula": "C1H2O1",
                    "molecular_weight": "30.03",
                    "inchikey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C",
                    "identity_status": "resolved_requires_curator_review",
                    "response_sha256": "a" * 64,
                },
            }
        )
    coverage = json.dumps(
        {"candidates": coverage_rows}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    pubchem = json.dumps(
        {"resolved_candidates": pubchem_rows}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    policy["source_snapshot"].update(
        {
            "candidate_catalog_sha256": hashlib.sha256(catalog).hexdigest(),
            "coverage_summary_sha256": hashlib.sha256(coverage).hexdigest(),
            "pubchem_resolution_sha256": hashlib.sha256(pubchem).hexdigest(),
        }
    )
    return policy, catalog, coverage, pubchem


def _screen(policy: dict, catalog: bytes, coverage: bytes, pubchem: bytes) -> dict:
    return select_compounds_without_mechanism(
        policy,
        catalog_bytes=catalog,
        coverage_summary_bytes=coverage,
        pubchem_resolution_bytes=pubchem,
    )


def test_full_thirteen_candidate_pool_is_screened_before_two_are_selected() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    result = _screen(policy, catalog, coverage, pubchem)
    assert result["analysis_mode"] == "compound_screening_no_pathway_inference"
    assert [stage["count"] for stage in result["screening_funnel"]] == [13, 13, 12, 2]
    assert [value["candidate_id"] for value in result["selected_compounds"]] == [
        "compound:chlorogenic_acid",
        "compound:glycyrrhizic_acid",
    ]
    assert len(result["ranked_candidate_pool"]) == 13


def test_ranking_uses_distinct_document_frequency() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    result = _screen(policy, catalog, coverage, pubchem)
    top_three = result["ranked_candidate_pool"][:3]
    assert [row["candidate_id"] for row in top_three] == [
        "compound:chlorogenic_acid",
        "compound:glycyrrhizic_acid",
        "compound:caffeic_acid",
    ]
    assert [row["corpus_frequency"]["burn_document_count"] for row in top_three] == [
        21,
        18,
        8,
    ]


def test_mechanism_claims_are_not_emitted() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    result = _screen(policy, catalog, coverage, pubchem)
    assert result["mechanism_analysis"] == {
        "enabled": False,
        "targets": [],
        "pathways": [],
        "phenotype_claims": [],
    }
    assert result["ranking_policy"]["composite_mechanism_score"] is False


def test_frequency_counts_must_be_nested() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    changed = json.loads(coverage.decode("utf-8"))
    changed["candidates"][0]["burn_document_count"] = 100
    changed_bytes = json.dumps(changed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    policy["source_snapshot"]["coverage_summary_sha256"] = hashlib.sha256(
        changed_bytes
    ).hexdigest()
    with pytest.raises(CompoundOnlyInputError, match="frequencies are not nested"):
        _screen(policy, catalog, changed_bytes, pubchem)


def test_candidate_sets_must_match_exactly() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    changed = json.loads(coverage.decode("utf-8"))
    changed["candidates"].pop()
    changed_bytes = json.dumps(changed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    policy["source_snapshot"]["coverage_summary_sha256"] = hashlib.sha256(
        changed_bytes
    ).hexdigest()
    with pytest.raises(CompoundOnlyInputError, match="candidate sets must match"):
        _screen(policy, catalog, changed_bytes, pubchem)


def test_source_snapshot_requires_exact_hashes() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    policy["source_snapshot"]["coverage_summary_sha256"] = "0" * 64
    with pytest.raises(CompoundOnlyInputError, match="SHA-256 mismatch"):
        _screen(policy, catalog, coverage, pubchem)


def test_source_verifier_reports_all_thirteen_candidates() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    report = verify_compound_only_sources(
        policy=policy,
        catalog_bytes=catalog,
        coverage_summary_bytes=coverage,
        pubchem_resolution_bytes=pubchem,
    )
    assert report["valid"] is True
    assert report["verified_candidate_count"] == 13
    assert report["selected_candidate_ids"] == [
        "compound:chlorogenic_acid",
        "compound:glycyrrhizic_acid",
    ]


def test_verifier_rejects_changed_source_artifact() -> None:
    policy, catalog, coverage, pubchem = _artifacts()
    changed = copy.deepcopy(json.loads(coverage.decode("utf-8")))
    changed["candidates"][0]["burn_document_count"] += 1
    changed_bytes = json.dumps(changed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    report = verify_compound_only_sources(
        policy=policy,
        catalog_bytes=catalog,
        coverage_summary_bytes=changed_bytes,
        pubchem_resolution_bytes=pubchem,
    )
    assert report["valid"] is False
    assert "coverage_summary_sha256" in report["issues"][0]
