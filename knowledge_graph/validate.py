from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .ids import canonical_json, composition_fingerprint, normalized_key, sha256_text
from .model import EvidenceRecord, GraphData, GraphEdge, GraphNode
from .schema import load_schema


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GRADE_RANK = {"E1": 1, "E2": 2, "E3": 3, "E4": 4, "E5": 5}
_PDF_SOURCE_TYPES = {"ancient_pdf", "modern_pdf"}
_PAGE_LOCATOR_KEYS = {"page_id", "physical_page", "pdf_page"}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    record_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "record_id": self.record_id,
        }


def _duplicate_values(values: list[str]) -> set[str]:
    counts = Counter(values)
    return {value for value, count in counts.items() if count > 1}


def _is_traceable(
    edge: GraphEdge,
    evidence_by_id: dict[str, EvidenceRecord],
    source_ids: set[str],
) -> bool:
    if not edge.evidence_ids:
        return False
    for evidence_id in edge.evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or evidence.source_id not in source_ids:
            return False
        if not evidence.locator:
            return False
        if evidence.quote and evidence.quote_sha256 != sha256_text(evidence.quote):
            return False
    return True


def validate_graph(graph: GraphData, *, release: bool = False) -> dict[str, Any]:
    schema = load_schema()
    issues: list[ValidationIssue] = []

    def add(severity: str, code: str, message: str, record_id: str = "") -> None:
        issues.append(ValidationIssue(severity, code, message, record_id))

    for duplicate in _duplicate_values([value.source_id for value in graph.sources]):
        add("error", "duplicate_source_id", "source_id is not unique", duplicate)
    for duplicate in _duplicate_values([value.node_id for value in graph.nodes]):
        add("error", "duplicate_node_id", "node_id is not unique", duplicate)
    for duplicate in _duplicate_values(
        [value.evidence_id for value in graph.evidence]
    ):
        add("error", "duplicate_evidence_id", "evidence_id is not unique", duplicate)
    for duplicate in _duplicate_values([value.edge_id for value in graph.edges]):
        add("error", "duplicate_edge_id", "edge_id is not unique", duplicate)

    source_by_id = {value.source_id: value for value in graph.sources}
    node_by_id = {value.node_id: value for value in graph.nodes}
    evidence_by_id = {value.evidence_id: value for value in graph.evidence}

    for source in graph.sources:
        if not source.title.strip():
            add("error", "source_title_missing", "source title is empty", source.source_id)
        if source.source_type in _PDF_SOURCE_TYPES and not _SHA256.fullmatch(
            source.file_sha256
        ):
            add(
                "error",
                "source_sha256_invalid",
                "PDF source requires a lowercase 64-character SHA-256",
                source.source_id,
            )

    formula_variants_by_name: defaultdict[str, list[GraphNode]] = defaultdict(list)
    for node in graph.nodes:
        spec = schema["node_types"].get(node.entity_type)
        if spec is None:
            add("error", "unknown_entity_type", node.entity_type, node.node_id)
            continue
        if node.layer != spec["layer"]:
            add(
                "error",
                "node_layer_mismatch",
                f"{node.entity_type} must use layer {spec['layer']}, got {node.layer}",
                node.node_id,
            )
        if not node.canonical_name.strip():
            add("error", "canonical_name_missing", "canonical_name is empty", node.node_id)
        for required_attribute in spec.get("required_attributes", []):
            if required_attribute not in node.attributes or node.attributes[
                required_attribute
            ] in (None, "", [], {}):
                add(
                    "error",
                    "required_attribute_missing",
                    f"{node.entity_type} requires attribute {required_attribute}",
                    node.node_id,
                )
        if node.entity_type == "FormulaVariant":
            formula_variants_by_name[node.canonical_name.casefold()].append(node)
            composition = node.attributes.get("composition", [])
            expected = composition_fingerprint(composition)
            if node.attributes.get("composition_fingerprint") != expected:
                add(
                    "error",
                    "composition_fingerprint_mismatch",
                    "FormulaVariant composition fingerprint is missing or stale",
                    node.node_id,
                )

    for name, variants in formula_variants_by_name.items():
        fingerprints = [
            str(value.attributes.get("composition_fingerprint", ""))
            for value in variants
        ]
        locators = [
            value.attributes.get("source_locator", {}) for value in variants
        ]
        identities = {
            (fingerprint, canonical_json(locator))
            for fingerprint, locator in zip(fingerprints, locators, strict=True)
        }
        if len(identities) != len(variants):
            add(
                "error",
                "formula_variant_collision",
                f"same-name formula variants collide for canonical name {name}",
            )

    for evidence in graph.evidence:
        if evidence.source_id not in source_by_id:
            add(
                "error",
                "evidence_source_missing",
                f"unknown source_id {evidence.source_id}",
                evidence.evidence_id,
            )
        if evidence.evidence_grade not in schema["evidence_grades"]:
            add(
                "error",
                "evidence_grade_invalid",
                evidence.evidence_grade,
                evidence.evidence_id,
            )
        if evidence.evidence_class not in schema["evidence_classes"]:
            add(
                "error",
                "evidence_class_invalid",
                evidence.evidence_class,
                evidence.evidence_id,
            )
        review_status = str(evidence.review.get("status", "pending"))
        if review_status not in schema["review_statuses"]:
            add(
                "error",
                "evidence_review_status_invalid",
                review_status,
                evidence.evidence_id,
            )
        if evidence.quote_sha256 != sha256_text(evidence.quote):
            add(
                "error",
                "quote_sha256_mismatch",
                "quote_sha256 does not match quote",
                evidence.evidence_id,
            )
        if evidence.evidence_grade == "E1":
            if not evidence.quote.strip():
                add(
                    "error",
                    "e1_quote_missing",
                    "E1 evidence requires a direct quote",
                    evidence.evidence_id,
                )
            if not (_PAGE_LOCATOR_KEYS & set(evidence.locator)):
                add(
                    "error",
                    "e1_page_locator_missing",
                    "E1 evidence requires page_id, physical_page, or pdf_page",
                    evidence.evidence_id,
                )
        if evidence.evidence_class == "direct_ancient" and evidence.evidence_grade != "E1":
            add(
                "error",
                "direct_ancient_grade_invalid",
                "direct_ancient evidence must be E1",
                evidence.evidence_id,
            )
        if release and review_status != "approved":
            add(
                "error",
                "evidence_not_approved",
                "release evidence must be approved",
                evidence.evidence_id,
            )
        elif not release and review_status == "pending":
            add(
                "warning",
                "evidence_review_pending",
                "draft evidence still requires review",
                evidence.evidence_id,
            )

    key_path_edge_count = 0
    key_path_traceable_count = 0
    ordinary_edge_count = 0
    ordinary_traceable_count = 0
    for edge in graph.edges:
        subject = node_by_id.get(edge.subject_id)
        object_node = node_by_id.get(edge.object_id)
        if subject is None:
            add(
                "error",
                "edge_subject_missing",
                f"unknown subject_id {edge.subject_id}",
                edge.edge_id,
            )
        if object_node is None:
            add(
                "error",
                "edge_object_missing",
                f"unknown object_id {edge.object_id}",
                edge.edge_id,
            )
        spec = schema["relationship_types"].get(edge.predicate)
        if spec is None:
            add("error", "unknown_predicate", edge.predicate, edge.edge_id)
            continue
        if subject is not None and subject.entity_type not in spec["source"]:
            add(
                "error",
                "predicate_source_type_invalid",
                f"{edge.predicate} cannot start at {subject.entity_type}",
                edge.edge_id,
            )
        if object_node is not None and object_node.entity_type not in spec["target"]:
            add(
                "error",
                "predicate_target_type_invalid",
                f"{edge.predicate} cannot end at {object_node.entity_type}",
                edge.edge_id,
            )
        if (
            spec.get("same_type")
            and subject is not None
            and object_node is not None
            and subject.entity_type != object_node.entity_type
        ):
            add(
                "error",
                "predicate_same_type_required",
                f"{edge.predicate} requires matching entity types",
                edge.edge_id,
            )
        if edge.evidence_grade not in schema["evidence_grades"]:
            add(
                "error",
                "edge_grade_invalid",
                edge.evidence_grade,
                edge.edge_id,
            )
        if edge.assertion_mode not in schema["assertion_modes"]:
            add(
                "error",
                "assertion_mode_invalid",
                edge.assertion_mode,
                edge.edge_id,
            )
        if edge.review_status not in schema["review_statuses"]:
            add(
                "error",
                "edge_review_status_invalid",
                edge.review_status,
                edge.edge_id,
            )
        if not 0.0 <= edge.confidence <= 1.0:
            add(
                "error",
                "confidence_out_of_range",
                "confidence must be between 0 and 1",
                edge.edge_id,
            )
        missing_evidence = [
            evidence_id
            for evidence_id in edge.evidence_ids
            if evidence_id not in evidence_by_id
        ]
        if missing_evidence:
            add(
                "error",
                "edge_evidence_missing",
                f"unknown evidence IDs: {missing_evidence}",
                edge.edge_id,
            )
        supporting = [
            evidence_by_id[evidence_id]
            for evidence_id in edge.evidence_ids
            if evidence_id in evidence_by_id
        ]
        supporting_grade_ranks = [
            _GRADE_RANK[value.evidence_grade]
            for value in supporting
            if value.evidence_grade in _GRADE_RANK
        ]
        if supporting_grade_ranks and edge.evidence_grade in _GRADE_RANK:
            strongest_available = min(supporting_grade_ranks)
            if _GRADE_RANK[edge.evidence_grade] < strongest_available:
                add(
                    "error",
                    "edge_grade_overstated",
                    "edge grade is stronger than its supporting evidence",
                    edge.edge_id,
                )
        allowed_grades = spec.get("allowed_grades")
        if allowed_grades and edge.evidence_grade not in allowed_grades:
            add(
                "error",
                "predicate_grade_invalid",
                f"{edge.predicate} permits only {allowed_grades}",
                edge.edge_id,
            )
        allowed_modes = spec.get("allowed_modes")
        if allowed_modes and edge.assertion_mode not in allowed_modes:
            add(
                "error",
                "predicate_mode_invalid",
                f"{edge.predicate} permits only {allowed_modes}",
                edge.edge_id,
            )
        if edge.predicate == "TREATS" and object_node is not None:
            if object_node.entity_type == "BurnPhenotype":
                has_direct_ancient = any(
                    value.evidence_class == "direct_ancient" for value in supporting
                )
                if has_direct_ancient and not edge.attributes.get("direct_burn_term"):
                    add(
                        "error",
                        "burn_transfer_misclassified",
                        "ancient TREATS -> BurnPhenotype requires direct_burn_term=true; "
                        "otherwise use MECHANISM_TRANSFER",
                        edge.edge_id,
                    )
        if edge.review_status == "rejected":
            add(
                "error",
                "rejected_edge_present",
                "rejected assertions cannot enter a graph build",
                edge.edge_id,
            )
        if release and edge.review_status != "approved":
            add(
                "error",
                "edge_not_approved",
                "release assertions must be approved",
                edge.edge_id,
            )
        elif not release and edge.review_status == "pending":
            add(
                "warning",
                "edge_review_pending",
                "draft assertion still requires review",
                edge.edge_id,
            )

        if (
            edge.predicate == "RECORDED_IN"
            and object_node is not None
            and supporting
        ):
            passage_locator = dict(object_node.attributes.get("locator", {}))
            for evidence in supporting:
                for locator_key in ("page_id", "physical_page", "pdf_page"):
                    passage_value = passage_locator.get(locator_key)
                    evidence_value = evidence.locator.get(locator_key)
                    if (
                        passage_value is not None
                        and evidence_value is not None
                        and str(passage_value) != str(evidence_value)
                    ):
                        add(
                            "error" if release else "warning",
                            "recorded_in_locator_mismatch",
                            f"{locator_key} differs between Passage and evidence",
                            edge.edge_id,
                        )

        traceable = _is_traceable(edge, evidence_by_id, set(source_by_id))
        if spec.get("key_path"):
            key_path_edge_count += 1
            key_path_traceable_count += int(traceable)
        else:
            ordinary_edge_count += 1
            ordinary_traceable_count += int(traceable)

    formula_variant_count = sum(
        value.entity_type == "FormulaVariant" for value in graph.nodes
    )
    complete_formula_variant_count = sum(
        value.entity_type == "FormulaVariant"
        and bool(value.attributes.get("formula_name"))
        and bool(value.attributes.get("composition"))
        and bool(value.attributes.get("source_locator"))
        and bool(value.attributes.get("composition_fingerprint"))
        for value in graph.nodes
    )
    key_path_rate = (
        key_path_traceable_count / key_path_edge_count
        if key_path_edge_count
        else 1.0
    )
    ordinary_rate = (
        ordinary_traceable_count / ordinary_edge_count
        if ordinary_edge_count
        else 1.0
    )
    formula_rate = (
        complete_formula_variant_count / formula_variant_count
        if formula_variant_count
        else 1.0
    )
    outgoing_by_subject: defaultdict[str, list[GraphEdge]] = defaultdict(list)
    for edge in graph.edges:
        outgoing_by_subject[edge.subject_id].append(edge)
    for variant in (
        value for value in graph.nodes if value.entity_type == "FormulaVariant"
    ):
        variant_edges = outgoing_by_subject[variant.node_id]
        variant_of_edges = [
            value for value in variant_edges if value.predicate == "VARIANT_OF"
        ]
        if len(variant_of_edges) != 1:
            add(
                "error" if release else "warning",
                "formula_variant_concept_cardinality",
                "FormulaVariant must have exactly one VARIANT_OF relationship",
                variant.node_id,
            )
        ingredient_edges = [
            value for value in variant_edges if value.predicate == "HAS_INGREDIENT"
        ]
        graph_ingredient_names = {
            normalized_key(node_by_id[value.object_id].canonical_name)
            for value in ingredient_edges
            if value.object_id in node_by_id
            and node_by_id[value.object_id].entity_type == "Herb"
        }
        composition_ingredient_names = {
            normalized_key(
                item.get("herb")
                or item.get("canonical_name")
                or item.get("herb_id")
                or ""
            )
            for item in variant.attributes.get("composition", [])
        }
        composition_ingredient_names.discard("")
        if graph_ingredient_names != composition_ingredient_names:
            add(
                "error" if release else "warning",
                "formula_ingredient_edges_mismatch",
                "HAS_INGREDIENT endpoints must exactly match FormulaVariant composition",
                variant.node_id,
            )

    metrics = {
        "source_count": len(graph.sources),
        "node_count": len(graph.nodes),
        "evidence_count": len(graph.evidence),
        "edge_count": len(graph.edges),
        "key_path_edge_count": key_path_edge_count,
        "key_path_traceability_rate": key_path_rate,
        "ordinary_edge_count": ordinary_edge_count,
        "ordinary_edge_traceability_rate": ordinary_rate,
        "formula_variant_count": formula_variant_count,
        "formula_variant_completeness_rate": formula_rate,
    }
    if release:
        empty_sections = [
            name
            for name, count in (
                ("sources", len(graph.sources)),
                ("nodes", len(graph.nodes)),
                ("evidence", len(graph.evidence)),
                ("edges", len(graph.edges)),
            )
            if count == 0
        ]
        if empty_sections:
            add(
                "error",
                "empty_release_graph",
                f"release graph cannot have empty sections: {empty_sections}",
            )
        thresholds = schema["release_thresholds"]
        for metric_name, threshold in thresholds.items():
            if float(metrics[metric_name]) < float(threshold):
                add(
                    "error",
                    "release_threshold_failed",
                    f"{metric_name}={metrics[metric_name]:.6f} < {threshold}",
                )

    issue_dicts = [value.to_dict() for value in issues]
    error_count = sum(value.severity == "error" for value in issues)
    warning_count = sum(value.severity == "warning" for value in issues)
    return {
        "valid": error_count == 0,
        "release_mode": release,
        "schema_version": graph.schema_version,
        "graph_version": graph.graph_version,
        "metrics": metrics,
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issue_dicts,
    }
