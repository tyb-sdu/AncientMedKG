from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .ids import (
    composition_fingerprint,
    make_edge_id,
    make_evidence_id,
    make_node_id,
    make_source_id,
    normalize_text,
    sha256_text,
)
from .model import EvidenceRecord, GraphData, GraphEdge, GraphNode, SourceRecord
from .schema import node_spec, schema_version


class BundleError(ValueError):
    """Raised when an input evidence bundle cannot be built safely."""


def _require_key(data: Mapping[str, Any], key: str, context: str) -> Any:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise BundleError(f"{context} requires non-empty {key}")
    return value


def _unique_key_map(
    records: list[Mapping[str, Any]],
    record_type: str,
) -> dict[str, Mapping[str, Any]]:
    mapped: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        key = str(_require_key(record, "key", f"{record_type}[{index}]"))
        if key in mapped:
            raise BundleError(f"duplicate {record_type} key: {key}")
        mapped[key] = record
    return mapped


def build_bundle(bundle: Mapping[str, Any]) -> GraphData:
    expected_schema = schema_version()
    supplied_schema = str(bundle.get("schema_version", expected_schema))
    if supplied_schema != expected_schema:
        raise BundleError(
            f"schema_version mismatch: expected {expected_schema}, got {supplied_schema}"
        )
    bundle_id = str(_require_key(bundle, "bundle_id", "bundle"))
    graph_version = str(_require_key(bundle, "graph_version", "bundle"))

    raw_sources = [dict(value) for value in bundle.get("sources", [])]
    source_by_key: dict[str, str] = {}
    source_records: list[SourceRecord] = []
    seen_source_ids: set[str] = set()
    for index, raw_source in enumerate(raw_sources):
        key = str(raw_source.get("key") or raw_source.get("source_id") or f"source-{index}")
        source_id = str(raw_source.get("source_id") or make_source_id(raw_source))
        if key in source_by_key:
            raise BundleError(f"duplicate source key: {key}")
        if source_id in seen_source_ids:
            raise BundleError(f"duplicate source_id: {source_id}")
        source_by_key[key] = source_id
        source_by_key[source_id] = source_id
        seen_source_ids.add(source_id)
        source_records.append(
            SourceRecord(
                source_id=source_id,
                source_type=str(_require_key(raw_source, "source_type", f"source[{index}]")),
                title=str(_require_key(raw_source, "title", f"source[{index}]")),
                file_name=str(raw_source.get("file_name", "")),
                file_sha256=str(raw_source.get("file_sha256", "")).lower(),
                doi=str(raw_source.get("doi", "")),
                year=str(raw_source.get("year", "")),
                work_id=str(raw_source.get("work_id", "")),
                edition_id=str(raw_source.get("edition_id", "")),
                attributes=dict(raw_source.get("attributes", {})),
            )
        )

    raw_entities = [dict(value) for value in bundle.get("entities", [])]
    entity_map = _unique_key_map(raw_entities, "entity")
    node_by_key: dict[str, str] = {}
    nodes: list[GraphNode] = []
    seen_node_ids: set[str] = set()
    for key, raw_entity in entity_map.items():
        entity_type = str(_require_key(raw_entity, "entity_type", f"entity {key}"))
        canonical_name = normalize_text(
            _require_key(raw_entity, "canonical_name", f"entity {key}")
        )
        spec = node_spec(entity_type)
        attributes = dict(raw_entity.get("attributes", {}))
        if entity_type == "Passage":
            source_ref = str(attributes.get("source_id", ""))
            if source_ref in source_by_key:
                attributes["source_id"] = source_by_key[source_ref]
        if entity_type == "FormulaVariant":
            source_locator = dict(attributes.get("source_locator", {}))
            source_ref = str(source_locator.get("source_id", ""))
            if source_ref in source_by_key:
                source_locator["source_id"] = source_by_key[source_ref]
            attributes["source_locator"] = source_locator
            composition = attributes.get("composition", [])
            attributes["composition_fingerprint"] = composition_fingerprint(composition)
        node_id = str(
            raw_entity.get("node_id")
            or make_node_id(
                entity_type,
                canonical_name,
                namespace=str(raw_entity.get("namespace", "ancientmedkg")),
                external_ids=dict(raw_entity.get("external_ids", {})),
                identity=dict(raw_entity.get("identity", {})),
                attributes=attributes,
            )
        )
        if node_id in seen_node_ids:
            raise BundleError(f"duplicate generated node_id for entity {key}: {node_id}")
        seen_node_ids.add(node_id)
        node_by_key[key] = node_id
        node_by_key[node_id] = node_id
        aliases = tuple(
            sorted(
                {
                    normalize_text(value)
                    for value in raw_entity.get("aliases", [])
                    if normalize_text(value) and normalize_text(value) != canonical_name
                }
            )
        )
        nodes.append(
            GraphNode(
                node_id=node_id,
                entity_type=entity_type,
                canonical_name=canonical_name,
                layer=str(spec["layer"]),
                aliases=aliases,
                external_ids=dict(raw_entity.get("external_ids", {})),
                attributes=attributes,
            )
        )

    raw_evidence = [dict(value) for value in bundle.get("evidence", [])]
    evidence_map = _unique_key_map(raw_evidence, "evidence")
    evidence_by_key: dict[str, str] = {}
    evidence_records: list[EvidenceRecord] = []
    seen_evidence_ids: set[str] = set()
    for key, raw_record in evidence_map.items():
        raw_source_ref = str(
            _require_key(raw_record, "source", f"evidence {key}")
        )
        if raw_source_ref not in source_by_key:
            raise BundleError(
                f"evidence {key} refers to unknown source: {raw_source_ref}"
            )
        source_id = source_by_key[raw_source_ref]
        quote = str(raw_record.get("quote", ""))
        quote_sha256 = sha256_text(quote)
        supplied_quote_sha = str(raw_record.get("quote_sha256", "")).lower()
        if supplied_quote_sha and supplied_quote_sha != quote_sha256:
            raise BundleError(
                f"evidence {key} quote_sha256 does not match quote text"
            )
        locator = dict(raw_record.get("locator", {}))
        evidence_id = str(
            raw_record.get("evidence_id")
            or make_evidence_id(source_id, locator, quote_sha256)
        )
        if evidence_id in seen_evidence_ids:
            raise BundleError(
                f"duplicate generated evidence_id for evidence {key}: {evidence_id}"
            )
        seen_evidence_ids.add(evidence_id)
        evidence_by_key[key] = evidence_id
        evidence_by_key[evidence_id] = evidence_id
        evidence_records.append(
            EvidenceRecord(
                evidence_id=evidence_id,
                source_id=source_id,
                locator=locator,
                quote=quote,
                quote_sha256=quote_sha256,
                evidence_grade=str(
                    _require_key(raw_record, "evidence_grade", f"evidence {key}")
                ),
                evidence_class=str(
                    _require_key(raw_record, "evidence_class", f"evidence {key}")
                ),
                review=dict(raw_record.get("review", {"status": "pending"})),
                attributes=dict(raw_record.get("attributes", {})),
            )
        )

    edges: list[GraphEdge] = []
    seen_edge_ids: set[str] = set()
    for index, raw_assertion in enumerate(bundle.get("assertions", [])):
        assertion = dict(raw_assertion)
        context = f"assertion[{index}]"
        subject_ref = str(_require_key(assertion, "subject", context))
        object_ref = str(_require_key(assertion, "object", context))
        predicate = str(_require_key(assertion, "predicate", context))
        if subject_ref not in node_by_key:
            raise BundleError(f"{context} has unknown subject: {subject_ref}")
        if object_ref not in node_by_key:
            raise BundleError(f"{context} has unknown object: {object_ref}")
        raw_evidence_refs = list(assertion.get("evidence", []))
        evidence_ids: list[str] = []
        for evidence_ref_value in raw_evidence_refs:
            evidence_ref = str(evidence_ref_value)
            if evidence_ref not in evidence_by_key:
                raise BundleError(
                    f"{context} refers to unknown evidence: {evidence_ref}"
                )
            evidence_ids.append(evidence_by_key[evidence_ref])
        assertion_mode = str(assertion.get("assertion_mode", "explicit"))
        attributes = dict(assertion.get("attributes", {}))
        edge_id = str(
            assertion.get("edge_id")
            or make_edge_id(
                node_by_key[subject_ref],
                predicate,
                node_by_key[object_ref],
                evidence_ids,
                assertion_mode,
                attributes,
            )
        )
        if edge_id in seen_edge_ids:
            raise BundleError(f"duplicate assertion edge_id: {edge_id}")
        seen_edge_ids.add(edge_id)
        edges.append(
            GraphEdge(
                edge_id=edge_id,
                subject_id=node_by_key[subject_ref],
                predicate=predicate,
                object_id=node_by_key[object_ref],
                evidence_ids=tuple(sorted(set(evidence_ids))),
                evidence_grade=str(assertion.get("evidence_grade", "")),
                assertion_mode=assertion_mode,
                confidence=float(assertion.get("confidence", 1.0)),
                review_status=str(assertion.get("review_status", "pending")),
                attributes=attributes,
            )
        )

    metadata = dict(bundle.get("metadata", {}))
    metadata["input_bundle_sha256"] = sha256_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return GraphData(
        schema_version=expected_schema,
        graph_version=graph_version,
        bundle_id=bundle_id,
        sources=tuple(sorted(source_records, key=lambda value: value.source_id)),
        nodes=tuple(sorted(nodes, key=lambda value: value.node_id)),
        evidence=tuple(
            sorted(evidence_records, key=lambda value: value.evidence_id)
        ),
        edges=tuple(sorted(edges, key=lambda value: value.edge_id)),
        metadata=metadata,
    )


def build_bundle_file(path: Path) -> GraphData:
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read evidence bundle {path}: {exc}") from exc
    if not isinstance(bundle, dict):
        raise BundleError("evidence bundle root must be a JSON object")
    return build_bundle(bundle)
