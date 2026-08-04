from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
INCHIKEY_PATTERN = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


class CompoundOnlyInputError(ValueError):
    pass


def _decode_object(raw: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompoundOnlyInputError(f"cannot decode {label}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CompoundOnlyInputError(f"{label} root must be an object")
    return value


def _required_text(value: Mapping[str, Any], field: str, label: str) -> str:
    text = str(value.get(field, "")).strip()
    if not text:
        raise CompoundOnlyInputError(f"{label}.{field} is required")
    return text


def _expected_hash(snapshot: Mapping[str, Any], field: str) -> str:
    digest = _required_text(snapshot, field, "source_snapshot").lower()
    if not HASH_PATTERN.fullmatch(digest):
        raise CompoundOnlyInputError(
            f"source_snapshot.{field} must be a SHA-256 digest"
        )
    return digest


def _rows_by_id(root: Mapping[str, Any], field: str, label: str) -> dict[str, Mapping[str, Any]]:
    rows = root.get(field)
    if not isinstance(rows, list) or not rows:
        raise CompoundOnlyInputError(f"{label}.{field} must be a non-empty array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise CompoundOnlyInputError(f"{label}.{field}[{index}] must be an object")
        candidate_id = _required_text(row, "candidate_id", f"{label}.{field}[{index}]")
        if candidate_id in result:
            raise CompoundOnlyInputError(f"duplicate candidate_id in {label}: {candidate_id}")
        result[candidate_id] = row
    return result


def _frequency(row: Mapping[str, Any], candidate_id: str) -> dict[str, int]:
    source_fields = {
        "document_count": "document_count",
        "matching_locus_count": "chunk_count",
        "wound_or_burn_document_count": "wound_or_burn_document_count",
        "wound_or_burn_locus_count": "wound_or_burn_chunk_count",
        "burn_document_count": "burn_document_count",
        "burn_locus_count": "burn_chunk_count",
    }
    result: dict[str, int] = {}
    for output_field, source_field in source_fields.items():
        raw = row.get(source_field)
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise CompoundOnlyInputError(
                f"{candidate_id}.{source_field} must be a non-negative integer"
            )
        result[output_field] = raw
    if not (
        result["burn_document_count"]
        <= result["wound_or_burn_document_count"]
        <= result["document_count"]
    ):
        raise CompoundOnlyInputError(f"{candidate_id}: document frequencies are not nested")
    if not (
        result["burn_locus_count"]
        <= result["wound_or_burn_locus_count"]
        <= result["matching_locus_count"]
    ):
        raise CompoundOnlyInputError(f"{candidate_id}: locus frequencies are not nested")
    return result


def _chemistry(row: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    value = row.get("pubchem")
    if not isinstance(value, Mapping):
        raise CompoundOnlyInputError(f"{candidate_id}.pubchem must be an object")
    cid = value.get("cid")
    if not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0:
        raise CompoundOnlyInputError(f"{candidate_id}.pubchem.cid must be positive")
    try:
        molecular_weight = float(value.get("molecular_weight"))
    except (TypeError, ValueError) as exc:
        raise CompoundOnlyInputError(
            f"{candidate_id}.pubchem.molecular_weight must be numeric"
        ) from exc
    if not math.isfinite(molecular_weight) or molecular_weight <= 0:
        raise CompoundOnlyInputError(
            f"{candidate_id}.pubchem.molecular_weight must be finite and positive"
        )
    inchikey = _required_text(value, "inchikey", f"{candidate_id}.pubchem")
    if not INCHIKEY_PATTERN.fullmatch(inchikey):
        raise CompoundOnlyInputError(f"{candidate_id}.pubchem.inchikey is invalid")
    return {
        "pubchem_cid": cid,
        "pubchem_title": _required_text(value, "title", f"{candidate_id}.pubchem"),
        "molecular_formula": _required_text(
            value, "molecular_formula", f"{candidate_id}.pubchem"
        ),
        "molecular_weight": molecular_weight,
        "inchikey": inchikey,
        "identity_status": _required_text(
            value, "identity_status", f"{candidate_id}.pubchem"
        ),
        "response_sha256": _required_text(
            value, "response_sha256", f"{candidate_id}.pubchem"
        ),
    }


def select_compounds_without_mechanism(
    policy: Mapping[str, Any],
    *,
    catalog_bytes: bytes,
    coverage_summary_bytes: bytes,
    pubchem_resolution_bytes: bytes,
) -> dict[str, Any]:
    if policy.get("analysis_mode") != "compound_screening_no_pathway_inference":
        raise CompoundOnlyInputError(
            "analysis_mode must be compound_screening_no_pathway_inference"
        )
    snapshot = policy.get("source_snapshot")
    thresholds = policy.get("thresholds")
    if not isinstance(snapshot, Mapping) or not isinstance(thresholds, Mapping):
        raise CompoundOnlyInputError("source_snapshot and thresholds must be objects")
    expected_hashes = {
        "candidate_catalog_sha256": _expected_hash(snapshot, "candidate_catalog_sha256"),
        "coverage_summary_sha256": _expected_hash(snapshot, "coverage_summary_sha256"),
        "pubchem_resolution_sha256": _expected_hash(snapshot, "pubchem_resolution_sha256"),
    }
    actual_hashes = {
        "candidate_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "coverage_summary_sha256": hashlib.sha256(coverage_summary_bytes).hexdigest(),
        "pubchem_resolution_sha256": hashlib.sha256(pubchem_resolution_bytes).hexdigest(),
    }
    mismatches = [
        field for field in expected_hashes if expected_hashes[field] != actual_hashes[field]
    ]
    if mismatches:
        raise CompoundOnlyInputError(
            "source artifact SHA-256 mismatch: " + ", ".join(sorted(mismatches))
        )

    minimum_burn_documents = thresholds.get("minimum_burn_document_count")
    if (
        not isinstance(minimum_burn_documents, int)
        or isinstance(minimum_burn_documents, bool)
        or minimum_burn_documents <= 0
    ):
        raise CompoundOnlyInputError(
            "thresholds.minimum_burn_document_count must be a positive integer"
        )

    catalog = _decode_object(catalog_bytes, "candidate catalog")
    coverage = _decode_object(coverage_summary_bytes, "coverage summary")
    pubchem = _decode_object(pubchem_resolution_bytes, "PubChem resolution")
    catalog_by_id = _rows_by_id(catalog, "candidates", "candidate catalog")
    coverage_by_id = _rows_by_id(coverage, "candidates", "coverage summary")
    pubchem_field = "resolved_candidates" if "resolved_candidates" in pubchem else "candidates"
    pubchem_by_id = _rows_by_id(pubchem, pubchem_field, "PubChem resolution")
    candidate_ids = set(catalog_by_id)
    if set(coverage_by_id) != candidate_ids or set(pubchem_by_id) != candidate_ids:
        raise CompoundOnlyInputError(
            "catalog, coverage, and PubChem candidate sets must match exactly"
        )
    expected_count = snapshot.get("candidate_count")
    if expected_count != len(candidate_ids):
        raise CompoundOnlyInputError(
            f"source_snapshot.candidate_count must equal {len(candidate_ids)}"
        )

    normalized: list[dict[str, Any]] = []
    for candidate_id in sorted(candidate_ids):
        candidate = catalog_by_id[candidate_id]
        frequency = _frequency(coverage_by_id[candidate_id], candidate_id)
        chemistry = _chemistry(pubchem_by_id[candidate_id], candidate_id)
        if frequency["burn_document_count"] >= minimum_burn_documents:
            selection_status = "selected_for_analytical_follow_up"
        elif frequency["burn_document_count"] > 0:
            selection_status = "reserve_below_frequency_threshold"
        else:
            selection_status = "excluded_no_burn_document"
        normalized.append(
            {
                "candidate_id": candidate_id,
                "canonical_name": _required_text(candidate, "canonical_name", candidate_id),
                "name_zh": _required_text(candidate, "name_zh", candidate_id),
                "herb_ids": sorted(set(candidate.get("herb_ids", []))),
                "candidate_role": _required_text(
                    candidate, "candidate_role", candidate_id
                ),
                "corpus_frequency": frequency,
                "chemical_properties": chemistry,
                "selection_status": selection_status,
                "scientific_claim_status": "retrieval_frequency_and_identity_only",
            }
        )

    ranked = sorted(
        normalized,
        key=lambda value: (
            -value["corpus_frequency"]["burn_document_count"],
            -value["corpus_frequency"]["wound_or_burn_document_count"],
            -value["corpus_frequency"]["document_count"],
            value["candidate_id"],
        ),
    )
    for rank, candidate in enumerate(ranked, start=1):
        candidate["frequency_rank"] = rank
    selected = [
        candidate
        for candidate in ranked
        if candidate["selection_status"] == "selected_for_analytical_follow_up"
    ]
    burn_present = [
        candidate
        for candidate in ranked
        if candidate["corpus_frequency"]["burn_document_count"] > 0
    ]
    return {
        "schema_version": 2,
        "selection_id": str(policy.get("selection_id", "")).strip(),
        "analysis_mode": "compound_screening_no_pathway_inference",
        "source_snapshot": {
            "modern_document_count": int(snapshot.get("modern_document_count", 0)),
            "candidate_count": len(candidate_ids),
            **actual_hashes,
        },
        "screening_funnel": [
            {"stage": "formula_ingredient_candidate_pool", "count": len(ranked)},
            {"stage": "identity_and_source_verified", "count": len(ranked)},
            {"stage": "at_least_one_burn_document", "count": len(burn_present)},
            {
                "stage": "minimum_burn_document_threshold",
                "threshold": minimum_burn_documents,
                "count": len(selected),
            },
        ],
        "ranking_policy": {
            "eligibility_threshold": (
                f"burn_document_count >= {minimum_burn_documents}"
            ),
            "primary": "burn_document_count_desc",
            "secondary": "wound_or_burn_document_count_desc",
            "tertiary": "document_count_desc",
            "tie_breaker": "candidate_id_asc",
            "composite_mechanism_score": False,
        },
        "frequency_definition": (
            "Counts are distinct source-document and matching-locus frequencies in the "
            "fixed project corpus. They are not external citation counts and do not "
            "establish efficacy."
        ),
        "selected_compounds": selected,
        "ranked_candidate_pool": ranked,
        "mechanism_analysis": {
            "enabled": False,
            "targets": [],
            "pathways": [],
            "phenotype_claims": [],
        },
        "scientific_boundary": (
            "This is a reproducible 13-candidate frequency and chemical-identity "
            "screen. It prioritizes analytical follow-up only and makes no efficacy, "
            "safety, target, pathway, angiogenesis, or wound-repair claim."
        ),
    }


def verify_compound_only_sources(
    *,
    policy: Mapping[str, Any],
    catalog_bytes: bytes,
    coverage_summary_bytes: bytes,
    pubchem_resolution_bytes: bytes,
) -> dict[str, Any]:
    try:
        result = select_compounds_without_mechanism(
            policy,
            catalog_bytes=catalog_bytes,
            coverage_summary_bytes=coverage_summary_bytes,
            pubchem_resolution_bytes=pubchem_resolution_bytes,
        )
    except CompoundOnlyInputError as exc:
        return {
            "schema_version": 2,
            "selection_id": str(policy.get("selection_id", "")).strip(),
            "valid": False,
            "verified_candidate_count": 0,
            "issues": [str(exc)],
        }
    return {
        "schema_version": 2,
        "selection_id": result["selection_id"],
        "valid": True,
        "verified_candidate_count": len(result["ranked_candidate_pool"]),
        "selected_candidate_ids": [
            value["candidate_id"] for value in result["selected_compounds"]
        ],
        "issues": [],
    }
