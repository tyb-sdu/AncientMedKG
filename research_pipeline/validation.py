from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ONTOLOGY_LAYERS = {
    "direct_cause",
    "direct_disease",
    "wound_phenotype",
    "pathogenesis",
    "therapy",
    "exclusion",
}
EVIDENCE_CHANNELS = {"A_direct", "B_transfer", "context_only", "exclude"}
EVIDENCE_LEVELS = {"E1", "E2", "E3", "E4", "E5"}
REQUIREMENT_STATUSES = {
    "completed",
    "partial",
    "not_started",
    "blocked_experiment",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _nonempty_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def validate_ontology(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("schema_version") != 1:
        issues.append("ontology.schema_version must equal 1")
    if not str(payload.get("ontology_id") or "").strip():
        issues.append("ontology.ontology_id is required")

    rules = payload.get("retrieval_rules")
    if not isinstance(rules, dict):
        issues.append("ontology.retrieval_rules must be an object")
    else:
        window = rules.get("context_window_chars")
        if not isinstance(window, int) or window < 1:
            issues.append("ontology.retrieval_rules.context_window_chars must be positive")
        labels = rules.get("classification_labels")
        expected = {"direct_burn", "suspected_burn", "ulcer_transfer", "irrelevant"}
        if not isinstance(labels, list) or set(labels) != expected:
            issues.append(
                "ontology classification_labels must contain the four frozen labels"
            )

    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        return issues + ["ontology.entries must be a non-empty array"]

    seen_ids: set[str] = set()
    seen_canonical: set[str] = set()
    layer_counts: Counter[str] = Counter()
    for index, entry in enumerate(entries, start=1):
        prefix = f"ontology.entries[{index}]"
        term_id = str(entry.get("term_id") or "").strip()
        canonical = str(entry.get("canonical") or "").strip()
        layer = str(entry.get("layer") or "")
        channel = str(entry.get("evidence_channel") or "")
        if not term_id or term_id in seen_ids:
            issues.append(f"{prefix}.term_id is missing or duplicated: {term_id!r}")
        seen_ids.add(term_id)
        if not canonical or canonical in seen_canonical:
            issues.append(f"{prefix}.canonical is missing or duplicated: {canonical!r}")
        seen_canonical.add(canonical)
        if layer not in ONTOLOGY_LAYERS:
            issues.append(f"{prefix}.layer is invalid: {layer!r}")
        else:
            layer_counts[layer] += 1
        if channel not in EVIDENCE_CHANNELS:
            issues.append(f"{prefix}.evidence_channel is invalid: {channel!r}")
        weight = entry.get("search_weight")
        if not isinstance(weight, (int, float)) or not -1.0 <= float(weight) <= 1.0:
            issues.append(f"{prefix}.search_weight must be between -1 and 1")
        if not _nonempty_strings(entry.get("surface_forms")):
            issues.append(f"{prefix}.surface_forms must be a non-empty string array")
        if layer == "exclusion" and isinstance(weight, (int, float)) and weight >= 0:
            issues.append(f"{prefix}.exclusion search_weight must be negative")
        if layer in {"direct_cause", "wound_phenotype"} and not _nonempty_strings(
            entry.get("required_context")
        ):
            issues.append(f"{prefix}.required_context is required for contextual terms")

    missing_layers = sorted(ONTOLOGY_LAYERS - set(layer_counts))
    if missing_layers:
        issues.append(f"ontology is missing layers: {missing_layers}")
    if layer_counts["direct_disease"] < 3:
        issues.append("ontology must contain at least three direct disease terms")
    if layer_counts["exclusion"] < 3:
        issues.append("ontology must contain at least three exclusion rules")
    return issues


def _validate_locus(locus: Any, prefix: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(locus, dict):
        return [f"{prefix} must be an object"]
    if not str(locus.get("book_id") or "").startswith("ancient:"):
        issues.append(f"{prefix}.book_id must use an ancient: identifier")
    if not str(locus.get("expected_title") or "").strip():
        issues.append(f"{prefix}.expected_title is required")
    page = locus.get("physical_page")
    if not isinstance(page, int) or page < 1:
        issues.append(f"{prefix}.physical_page must be positive")
    if not _nonempty_strings(locus.get("evidence_terms")):
        issues.append(f"{prefix}.evidence_terms must be non-empty")
    return issues


def validate_rendongtang_evidence(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("schema_version") != 1:
        issues.append("evidence.schema_version must equal 1")
    if payload.get("normalized_formula_name") != "忍冬汤":
        issues.append("evidence.normalized_formula_name must be 忍冬汤")

    instances = payload.get("formula_instances")
    if not isinstance(instances, list) or len(instances) < 2:
        return issues + ["evidence must contain at least two formula instances"]

    seen_ids: set[str] = set()
    composition_signatures: set[tuple[str, ...]] = set()
    pages: set[int] = set()
    for index, instance in enumerate(instances, start=1):
        prefix = f"evidence.formula_instances[{index}]"
        instance_id = str(instance.get("formula_instance_id") or "").strip()
        if not instance_id or instance_id in seen_ids:
            issues.append(f"{prefix}.formula_instance_id is missing or duplicated")
        seen_ids.add(instance_id)
        if instance.get("normalized_name") != "忍冬汤":
            issues.append(f"{prefix}.normalized_name must be 忍冬汤")
        if instance.get("evidence_level") not in EVIDENCE_LEVELS:
            issues.append(f"{prefix}.evidence_level is invalid")
        if instance.get("direct_burn_evidence") is not False:
            issues.append(f"{prefix}.direct_burn_evidence must remain false")
        locus = instance.get("source_locus")
        issues.extend(_validate_locus(locus, f"{prefix}.source_locus"))
        if isinstance(locus, dict) and isinstance(locus.get("physical_page"), int):
            pages.add(locus["physical_page"])
        ingredients = instance.get("ingredients")
        if not isinstance(ingredients, list) or not ingredients:
            issues.append(f"{prefix}.ingredients must be non-empty")
        else:
            names = tuple(
                sorted(
                    str(item.get("normalized_name") or "").strip()
                    for item in ingredients
                    if isinstance(item, dict)
                )
            )
            if not names or any(not name for name in names):
                issues.append(f"{prefix}.ingredients require normalized_name")
            composition_signatures.add(names)
        if not str(instance.get("indication_original") or "").strip():
            issues.append(f"{prefix}.indication_original is required")
        if not str(instance.get("review_status") or "").strip():
            issues.append(f"{prefix}.review_status is required")

    if len(composition_signatures) < 2:
        issues.append("same-name formula instances must have distinct compositions")
    if not {138, 227}.issubset(pages):
        issues.append("evidence must distinguish the physical-page 138 and 227 formulas")

    context_loci = payload.get("context_loci")
    if not isinstance(context_loci, list) or not context_loci:
        issues.append("evidence.context_loci must be non-empty")
    else:
        for index, locus in enumerate(context_loci, start=1):
            issues.extend(_validate_locus(locus, f"evidence.context_loci[{index}]"))

    relations = payload.get("relations")
    if not isinstance(relations, list) or not relations:
        issues.append("evidence.relations must be non-empty")
    else:
        transfer_relations = []
        same_name_relations = []
        for index, relation in enumerate(relations, start=1):
            prefix = f"evidence.relations[{index}]"
            level = relation.get("evidence_level")
            if level not in EVIDENCE_LEVELS:
                issues.append(f"{prefix}.evidence_level is invalid")
            relation_type = relation.get("relation_type")
            if relation_type == "mechanism_transfer_hypothesis":
                transfer_relations.append(relation)
                if level not in {"E4", "E5"}:
                    issues.append(f"{prefix} transfer evidence must be E4 or E5")
                if relation.get("direct_ancient_evidence") is not False:
                    issues.append(f"{prefix} must not claim direct ancient evidence")
                if relation.get("status") != "hypothesis_unvalidated":
                    issues.append(f"{prefix} must remain an unvalidated hypothesis")
            if relation_type == "same_name_distinct_formula":
                same_name_relations.append(relation)
        if not transfer_relations:
            issues.append("evidence requires a mechanism_transfer_hypothesis relation")
        if not same_name_relations:
            issues.append("evidence requires a same_name_distinct_formula relation")

    boundary = payload.get("claim_boundary")
    if not isinstance(boundary, dict):
        issues.append("evidence.claim_boundary must be an object")
    else:
        if boundary.get("ancient_direct_burn_claim_allowed") is not False:
            issues.append("claim boundary must forbid a direct ancient burn claim")
        if boundary.get("clinical_recommendation_allowed") is not False:
            issues.append("claim boundary must forbid clinical recommendations")
    return issues


def validate_questions(questions: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(questions, list) or len(questions) < 10:
        return ["questions must be an array containing at least ten items"]

    seen_ids: set[str] = set()
    categories: set[str] = set()
    positive_pages: set[int] = set()
    negative_count = 0
    for index, item in enumerate(questions, start=1):
        prefix = f"questions[{index}]"
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in seen_ids:
            issues.append(f"{prefix}.id is missing or duplicated")
        seen_ids.add(item_id)
        category = str(item.get("category") or "").strip()
        if not category:
            issues.append(f"{prefix}.category is required")
        categories.add(category)
        if not str(item.get("question") or "").strip():
            issues.append(f"{prefix}.question is required")
        expected = item.get("expect_answer")
        loci = item.get("expected_loci")
        if expected is True:
            if not isinstance(loci, list) or not loci:
                issues.append(f"{prefix} positive question requires expected_loci")
            else:
                for locus_index, locus in enumerate(loci, start=1):
                    locus_prefix = f"{prefix}.expected_loci[{locus_index}]"
                    if not str(locus.get("doc_id") or "").startswith("ancient:"):
                        issues.append(f"{locus_prefix}.doc_id must use ancient:")
                    pages = locus.get("pdf_pages")
                    if not isinstance(pages, list) or not pages:
                        issues.append(f"{locus_prefix}.pdf_pages must be non-empty")
                    else:
                        positive_pages.update(int(page) for page in pages)
                    if not _nonempty_strings(locus.get("evidence_terms")):
                        issues.append(f"{locus_prefix}.evidence_terms must be non-empty")
        elif expected is False:
            negative_count += 1
            if loci != []:
                issues.append(f"{prefix} no-answer question must use empty expected_loci")
            if not str(item.get("boundary_reason") or "").strip():
                issues.append(f"{prefix} no-answer question requires boundary_reason")
        else:
            issues.append(f"{prefix}.expect_answer must be boolean")

    required_categories = {
        "source_fact",
        "composition",
        "same_name_disambiguation",
        "evidence_boundary",
    }
    if not required_categories.issubset(categories):
        issues.append(
            f"questions are missing categories: {sorted(required_categories - categories)}"
        )
    if not {137, 138, 227}.issubset(positive_pages):
        issues.append("questions must cover physical pages 137, 138, and 227")
    if negative_count < 2:
        issues.append("questions require at least two evidence-boundary negatives")
    return issues


def validate_compliance_matrix(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("schema_version") != 1:
        issues.append("compliance.schema_version must equal 1")
    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return issues + ["compliance.requirements must be non-empty"]

    seen_ids: set[str] = set()
    phases: set[str] = set()
    for index, item in enumerate(requirements, start=1):
        prefix = f"compliance.requirements[{index}]"
        requirement_id = str(item.get("requirement_id") or "").strip()
        if not requirement_id or requirement_id in seen_ids:
            issues.append(f"{prefix}.requirement_id is missing or duplicated")
        seen_ids.add(requirement_id)
        status = item.get("status")
        if status not in REQUIREMENT_STATUSES:
            issues.append(f"{prefix}.status is invalid: {status!r}")
        phase = str(item.get("phase") or "").strip()
        if not phase:
            issues.append(f"{prefix}.phase is required")
        phases.add(phase)
        if not str(item.get("requirement") or "").strip():
            issues.append(f"{prefix}.requirement is required")
        evidence = item.get("evidence")
        remaining = item.get("remaining_tasks")
        if not isinstance(evidence, list):
            issues.append(f"{prefix}.evidence must be an array")
        if not isinstance(remaining, list):
            issues.append(f"{prefix}.remaining_tasks must be an array")
        if status == "completed" and remaining:
            issues.append(f"{prefix} completed item cannot retain remaining_tasks")
        if status in {"partial", "not_started"} and not remaining:
            issues.append(f"{prefix} incomplete item requires remaining_tasks")
        if phase == "experimental" and status not in {
            "blocked_experiment",
            "not_started",
        }:
            issues.append(f"{prefix} experimental work must not be marked completed")
    if "non_experimental" not in phases or "experimental" not in phases:
        issues.append("compliance matrix must separate non-experimental and experimental work")
    return issues


def validate_asset_bundle(
    ontology: dict[str, Any],
    evidence: dict[str, Any],
    questions: Any,
    compliance: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "ontology": validate_ontology(ontology),
        "rendongtang_evidence": validate_rendongtang_evidence(evidence),
        "questions": validate_questions(questions),
        "compliance": validate_compliance_matrix(compliance),
    }
    issues = [
        {"asset": asset, "message": message}
        for asset, messages in checks.items()
        for message in messages
    ]
    statuses = Counter(
        item.get("status") for item in compliance.get("requirements", [])
    )
    return {
        "valid": not issues,
        "issues": issues,
        "summary": {
            "ontology_entries": len(ontology.get("entries", [])),
            "formula_instances": len(evidence.get("formula_instances", [])),
            "specialized_questions": len(questions) if isinstance(questions, list) else 0,
            "requirement_statuses": dict(statuses),
        },
    }
