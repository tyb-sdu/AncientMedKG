from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


REQUIRED_ONTOLOGY_LAYERS = {
    "direct_cause",
    "direct_disease",
    "wound_phenotype",
    "pathogenesis",
    "therapy",
    "exclusion",
}
REQUIRED_EVIDENCE_CHANNELS = {"A_direct", "B_transfer", "context_only", "exclude"}
ALLOWED_EVIDENCE_LEVELS = {"E1", "E2", "E3", "E4", "E5"}


class DomainAssetError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DomainAssetError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DomainAssetError(f"{path} must contain a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _composition_fingerprint(ingredients: list[Mapping[str, Any]]) -> str:
    normalized = sorted(
        (
            {
                "normalized_name": str(value.get("normalized_name", "")).strip(),
                "dose_original": value.get("dose_original"),
            }
            for value in ingredients
        ),
        key=lambda value: value["normalized_name"],
    )
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_release_policy(policy: Any, issues: list[str], prefix: str) -> float:
    if not isinstance(policy, dict):
        issues.append(f"{prefix}.release_policy_missing")
        return 0.7
    threshold = policy.get("candidate_confidence_threshold")
    if not isinstance(threshold, (int, float)) or not 0.0 <= float(threshold) <= 1.0:
        issues.append(f"{prefix}.invalid_candidate_confidence_threshold")
        threshold = 0.7
    if policy.get("comparison") != "greater_than_or_equal":
        issues.append(f"{prefix}.invalid_threshold_comparison")
    if policy.get("below_threshold_action") != "discard":
        issues.append(f"{prefix}.invalid_below_threshold_action")
    if policy.get("at_or_above_threshold_action") != "approve":
        issues.append(f"{prefix}.invalid_approval_action")
    if policy.get("human_reviewed") is not False:
        issues.append(f"{prefix}.human_reviewed_must_be_false")
    return float(threshold)


def validate_ontology(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    issues: list[str] = []
    threshold = _validate_release_policy(value.get("release_policy"), issues, "ontology")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        issues.append("ontology.entries_missing")
        entries = []

    term_ids: list[str] = []
    surfaces: dict[str, str] = {}
    layers: Counter[str] = Counter()
    channels: Counter[str] = Counter()
    surface_count = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            issues.append(f"ontology.entries[{index}].not_an_object")
            continue
        term_id = str(entry.get("term_id", "")).strip()
        canonical = str(entry.get("canonical", "")).strip()
        layer = str(entry.get("layer", "")).strip()
        channel = str(entry.get("evidence_channel", "")).strip()
        forms = entry.get("surface_forms")
        if not term_id:
            issues.append(f"ontology.entries[{index}].term_id_missing")
        term_ids.append(term_id)
        if not canonical:
            issues.append(f"ontology.entries[{index}].canonical_missing")
        if layer not in REQUIRED_ONTOLOGY_LAYERS:
            issues.append(f"ontology.entries[{index}].unsupported_layer:{layer}")
        if channel not in REQUIRED_EVIDENCE_CHANNELS:
            issues.append(f"ontology.entries[{index}].unsupported_channel:{channel}")
        if not isinstance(forms, list) or not forms:
            issues.append(f"ontology.entries[{index}].surface_forms_missing")
            forms = []
        layers[layer] += 1
        channels[channel] += 1
        for form in forms:
            surface = str(form).strip()
            if not surface:
                issues.append(f"ontology.entries[{index}].empty_surface_form")
                continue
            surface_count += 1
            prior = surfaces.setdefault(surface, term_id)
            if prior != term_id:
                issues.append(f"ontology.surface_collision:{surface}:{prior}:{term_id}")

    if "" in term_ids or len(set(term_ids)) != len(term_ids):
        issues.append("ontology.term_ids_not_unique")
    missing_layers = sorted(REQUIRED_ONTOLOGY_LAYERS - set(layers))
    if missing_layers:
        issues.append("ontology.missing_layers:" + ",".join(missing_layers))
    missing_channels = sorted(REQUIRED_EVIDENCE_CHANNELS - set(channels))
    if missing_channels:
        issues.append("ontology.missing_channels:" + ",".join(missing_channels))

    return {
        "valid": not issues,
        "path": str(path),
        "sha256": _sha256_file(path),
        "ontology_id": value.get("ontology_id"),
        "version": value.get("version"),
        "entry_count": len(entries),
        "surface_form_count": surface_count,
        "layer_counts": dict(sorted(layers.items())),
        "evidence_channel_counts": dict(sorted(channels.items())),
        "automatic_approval_threshold": threshold,
        "issues": issues,
    }


def validate_rendongtang_package(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    issues: list[str] = []
    threshold = _validate_release_policy(value.get("release_policy"), issues, "rendongtang")
    work = value.get("work") if isinstance(value.get("work"), dict) else {}
    concept = (
        value.get("formula_concept")
        if isinstance(value.get("formula_concept"), dict)
        else {}
    )
    instances = value.get("formula_instances")
    loci = value.get("evidence_loci")
    relations = value.get("relations")
    if not isinstance(instances, list):
        issues.append("rendongtang.formula_instances_missing")
        instances = []
    if not isinstance(loci, list):
        issues.append("rendongtang.evidence_loci_missing")
        loci = []
    if not isinstance(relations, list):
        issues.append("rendongtang.relations_missing")
        relations = []

    evidence_ids = [str(value.get("evidence_id", "")) for value in loci]
    evidence_pages = {int(value.get("physical_page", 0)) for value in loci}
    if "" in evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
        issues.append("rendongtang.evidence_ids_not_unique")
    if evidence_pages != {137, 138, 227}:
        issues.append("rendongtang.unexpected_evidence_pages")
    for locus in loci:
        page = int(locus.get("physical_page", 0))
        expected_page_id = f"{work.get('book_id')}:p{page:06d}"
        if locus.get("page_id") != expected_page_id:
            issues.append(f"rendongtang.invalid_page_id:{locus.get('evidence_id')}")
        text_sha256 = str(locus.get("text_sha256", ""))
        if len(text_sha256) != 64 or any(value not in "0123456789abcdef" for value in text_sha256):
            issues.append(f"rendongtang.invalid_text_sha256:{locus.get('evidence_id')}")
        if locus.get("evidence_level") != "E1":
            issues.append(f"rendongtang.non_e1_source_locus:{locus.get('evidence_id')}")
        if not locus.get("evidence_terms"):
            issues.append(f"rendongtang.empty_evidence_terms:{locus.get('evidence_id')}")

    instance_ids: list[str] = []
    pages: list[int] = []
    fingerprints: list[str] = []
    concept_id = str(concept.get("formula_concept_id", ""))
    for index, instance in enumerate(instances):
        instance_id = str(instance.get("formula_instance_id", ""))
        instance_ids.append(instance_id)
        page = int(instance.get("physical_page", 0))
        pages.append(page)
        confidence = float(instance.get("candidate_confidence", -1.0))
        expected_status = "approved" if confidence >= threshold else "discarded"
        if instance.get("approval_status") != expected_status:
            issues.append(f"rendongtang.instances[{index}].approval_status_mismatch")
        if instance.get("human_reviewed") is not False:
            issues.append(f"rendongtang.instances[{index}].human_reviewed_must_be_false")
        if instance.get("normalized_name") != value.get("normalized_formula_name"):
            issues.append(f"rendongtang.instances[{index}].formula_name_mismatch")
        if instance.get("formula_concept_id") != concept_id:
            issues.append(f"rendongtang.instances[{index}].concept_id_mismatch")
        if instance.get("direct_burn_evidence") is not False:
            issues.append(f"rendongtang.instances[{index}].direct_burn_must_be_false")
        ingredients = instance.get("ingredients")
        if not isinstance(ingredients, list) or len(ingredients) < 2:
            issues.append(f"rendongtang.instances[{index}].ingredients_incomplete")
            ingredients = []
        ingredient_ids = [str(value.get("ingredient_id", "")) for value in ingredients]
        if "" in ingredient_ids or len(set(ingredient_ids)) != len(ingredient_ids):
            issues.append(f"rendongtang.instances[{index}].ingredient_ids_not_unique")
        actual_fingerprint = _composition_fingerprint(ingredients)
        fingerprints.append(actual_fingerprint)
        if instance.get("composition_fingerprint") != actual_fingerprint:
            issues.append(f"rendongtang.instances[{index}].composition_fingerprint_mismatch")
        instance_evidence = set(instance.get("evidence_ids", []))
        if not instance_evidence or not instance_evidence <= set(evidence_ids):
            issues.append(f"rendongtang.instances[{index}].invalid_evidence_reference")

    if len(instances) != 2:
        issues.append("rendongtang.expected_two_formula_instances")
    if "" in instance_ids or len(set(instance_ids)) != len(instance_ids):
        issues.append("rendongtang.instance_ids_not_unique")
    if set(pages) != {138, 227}:
        issues.append("rendongtang.variant_pages_not_138_and_227")
    if len(set(fingerprints)) != len(instances):
        issues.append("rendongtang.composition_fingerprints_not_unique")

    p227 = next((value for value in instances if value.get("physical_page") == 227), {})
    p227_honeysuckle = next(
        (
            value
            for value in p227.get("ingredients", [])
            if value.get("normalized_name") == "金银花"
        ),
        {},
    )
    if p227_honeysuckle.get("dose_original") is not None:
        issues.append("rendongtang.p227_unverified_honeysuckle_dose_released")
    if p227_honeysuckle.get("dose_status") != "not_asserted_below_field_confidence_threshold":
        issues.append("rendongtang.p227_honeysuckle_dose_boundary_missing")

    relation_ids: list[str] = []
    relation_types: Counter[str] = Counter()
    for index, relation in enumerate(relations):
        relation_id = str(relation.get("relation_id", ""))
        relation_ids.append(relation_id)
        relation_type = str(relation.get("relation_type", ""))
        relation_types[relation_type] += 1
        level = str(relation.get("evidence_level", ""))
        if level not in ALLOWED_EVIDENCE_LEVELS:
            issues.append(f"rendongtang.relations[{index}].invalid_evidence_level")
        relation_evidence = set(relation.get("evidence_ids", []))
        if not relation_evidence or not relation_evidence <= set(evidence_ids):
            issues.append(f"rendongtang.relations[{index}].invalid_evidence_reference")
        if relation_type == "MECHANISM_TRANSFER_HYPOTHESIS":
            if level != "E5" or relation.get("status") != "hypothesis_unvalidated":
                issues.append(f"rendongtang.relations[{index}].invalid_transfer_boundary")
            if relation.get("direct_ancient_evidence") is not False:
                issues.append(f"rendongtang.relations[{index}].transfer_marked_direct")
        elif relation.get("status") != "approved":
            issues.append(f"rendongtang.relations[{index}].direct_relation_not_approved")
    if "" in relation_ids or len(set(relation_ids)) != len(relation_ids):
        issues.append("rendongtang.relation_ids_not_unique")
    if relation_types["VARIANT_OF"] != 2:
        issues.append("rendongtang.variant_of_relations_missing")
    if relation_types["SAME_NAME_DISTINCT_FORMULA"] != 1:
        issues.append("rendongtang.same_name_distinct_relation_missing")
    expected_ingredient_pairs = {
        (str(instance.get("formula_instance_id", "")), str(ingredient.get("ingredient_id", "")))
        for instance in instances
        for ingredient in instance.get("ingredients", [])
    }
    actual_ingredient_pairs = {
        (str(relation.get("source", "")), str(relation.get("target", "")))
        for relation in relations
        if relation.get("relation_type") == "HAS_INGREDIENT"
    }
    if actual_ingredient_pairs != expected_ingredient_pairs:
        issues.append("rendongtang.ingredient_relations_do_not_match_compositions")
    if relation_types["MECHANISM_TRANSFER_HYPOTHESIS"] != 2:
        issues.append("rendongtang.transfer_relations_missing")
    if relation_types["TREATS"]:
        issues.append("rendongtang.automatic_treats_relation_forbidden")

    boundary = value.get("claim_boundary")
    if not isinstance(boundary, dict) or any(
        boundary.get(key) is not False
        for key in (
            "ancient_direct_burn_claim_allowed",
            "clinical_recommendation_allowed",
            "modern_dose_conversion_allowed",
            "automatic_treats_relation_allowed",
        )
    ):
        issues.append("rendongtang.claim_boundary_invalid")
    source_sha256 = str(work.get("source_sha256", ""))
    if (
        not work.get("book_id")
        or int(work.get("page_count", 0)) != 235
        or len(source_sha256) != 64
        or not source_sha256.startswith(str(work.get("book_id", "")).split(":")[-1])
    ):
        issues.append("rendongtang.work_locator_invalid")

    return {
        "valid": not issues,
        "path": str(path),
        "sha256": _sha256_file(path),
        "evidence_package_id": value.get("evidence_package_id"),
        "version": value.get("version"),
        "work": {
            "book_id": work.get("book_id"),
            "title": work.get("title"),
            "page_count": work.get("page_count"),
        },
        "automatic_approval_threshold": threshold,
        "evidence_locus_count": len(loci),
        "formula_instance_count": len(instances),
        "variant_pages": sorted(pages),
        "unique_composition_fingerprints": len(set(fingerprints)),
        "relation_count": len(relations),
        "relation_type_counts": dict(sorted(relation_types.items())),
        "direct_burn_claim_allowed": bool(
            isinstance(boundary, dict)
            and boundary.get("ancient_direct_burn_claim_allowed")
        ),
        "issues": issues,
    }


def validate_domain_assets(ontology_path: Path, evidence_path: Path) -> dict[str, Any]:
    ontology = validate_ontology(ontology_path)
    rendongtang = validate_rendongtang_package(evidence_path)
    issues = [
        *[f"ontology:{value}" for value in ontology["issues"]],
        *[f"rendongtang:{value}" for value in rendongtang["issues"]],
    ]
    if (
        ontology["automatic_approval_threshold"]
        != rendongtang["automatic_approval_threshold"]
    ):
        issues.append("release_policy_threshold_mismatch")
    return {
        "valid": not issues,
        "ontology": ontology,
        "rendongtang": rendongtang,
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Validate the frozen burn ontology and Rendongtang evidence chain"
    )
    parser.add_argument(
        "--ontology",
        type=Path,
        default=root / "data" / "burn_ontology_v1.json",
    )
    parser.add_argument(
        "--rendongtang-evidence",
        type=Path,
        default=root / "data" / "rendongtang_evidence_v1.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_domain_assets(args.ontology, args.rendongtang_evidence)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
