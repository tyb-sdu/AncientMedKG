from __future__ import annotations

import copy
import sys
from pathlib import Path

from research_pipeline.validation import (
    load_json,
    validate_asset_bundle,
    validate_rendongtang_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "research_pipeline" / "data"
EVALUATION = ROOT / "research_pipeline" / "evaluation"
sys.path.insert(0, str(ROOT / "app" / "scripts"))

from evaluate_ancient_retrieval import validate_question_schema  # noqa: E402


def load_bundle():
    return (
        load_json(DATA / "burn_ontology_v1.json"),
        load_json(DATA / "rendongtang_evidence_v1.json"),
        load_json(EVALUATION / "rendongtang_questions_v1.json"),
        load_json(DATA / "proposal_compliance_v1.json"),
    )


def test_research_asset_bundle_is_valid() -> None:
    report = validate_asset_bundle(*load_bundle())

    assert report["valid"] is True
    assert report["issues"] == []
    assert report["summary"]["ontology_entries"] >= 30
    assert report["summary"]["formula_instances"] == 2
    assert report["summary"]["specialized_questions"] == 15


def test_specialized_questions_match_existing_evaluator_schema() -> None:
    questions = load_bundle()[2]

    assert validate_question_schema(questions) == []
    assert {137, 138, 227}.issubset(
        {
            page
            for item in questions
            for locus in item["expected_loci"]
            for page in locus["pdf_pages"]
        }
    )


def test_rendongtang_boundary_rejects_direct_burn_claim() -> None:
    evidence = copy.deepcopy(load_bundle()[1])
    evidence["formula_instances"][0]["direct_burn_evidence"] = True
    evidence["relations"][-1]["direct_ancient_evidence"] = True

    issues = validate_rendongtang_evidence(evidence)

    assert any("direct_burn_evidence must remain false" in issue for issue in issues)
    assert any("must not claim direct ancient evidence" in issue for issue in issues)


def test_same_name_formula_instances_have_distinct_compositions() -> None:
    evidence = load_bundle()[1]
    instances = evidence["formula_instances"]

    assert instances[0]["normalized_name"] == instances[1]["normalized_name"]
    assert {
        item["normalized_name"] for item in instances[0]["ingredients"]
    } != {item["normalized_name"] for item in instances[1]["ingredients"]}
    assert instances[0]["source_locus"]["physical_page"] == 138
    assert instances[1]["source_locus"]["physical_page"] == 227
