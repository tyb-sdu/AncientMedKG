from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, Mapping


_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE.sub(" ", text).strip()


def normalized_key(value: object) -> str:
    return normalize_text(value).casefold()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_digest(namespace: str, payload: Any, length: int = 24) -> str:
    material = f"{namespace}\n{canonical_json(payload)}"
    return sha256_text(material)[:length]


def make_source_id(source: Mapping[str, Any]) -> str:
    payload = {
        "source_type": normalized_key(source.get("source_type", "")),
        "title": normalized_key(source.get("title", "")),
        "file_sha256": normalized_key(source.get("file_sha256", "")),
        "doi": normalized_key(source.get("doi", "")),
        "work_id": normalized_key(source.get("work_id", "")),
        "edition_id": normalized_key(source.get("edition_id", "")),
    }
    return f"kg:source:{stable_digest('source', payload)}"


def normalize_composition(composition: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw_item in composition:
        item = {
            "herb": normalized_key(
                raw_item.get("herb")
                or raw_item.get("canonical_name")
                or raw_item.get("herb_id")
            ),
            "dose_value": normalized_key(
                raw_item.get("dose_value") or raw_item.get("dose") or ""
            ),
            "dose_unit": normalized_key(raw_item.get("dose_unit") or raw_item.get("unit") or ""),
            "processing": normalized_key(raw_item.get("processing") or ""),
            "role": normalized_key(raw_item.get("role") or ""),
        }
        normalized.append(item)
    return sorted(normalized, key=canonical_json)


def composition_fingerprint(composition: Iterable[Mapping[str, Any]]) -> str:
    return sha256_text(canonical_json(normalize_composition(composition)))


def make_node_id(
    entity_type: str,
    canonical_name: str,
    *,
    namespace: str = "ancientmedkg",
    external_ids: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> str:
    attributes = attributes or {}
    payload: dict[str, Any] = {
        "entity_type": entity_type,
        "namespace": normalized_key(namespace),
        "canonical_name": normalized_key(canonical_name),
        "external_ids": {
            normalized_key(key): normalized_key(value)
            for key, value in sorted((external_ids or {}).items())
            if normalize_text(value)
        },
        "identity": dict(identity or {}),
    }
    if entity_type == "FormulaVariant":
        composition = attributes.get("composition", [])
        payload["formula_name"] = normalized_key(
            attributes.get("formula_name") or canonical_name
        )
        payload["composition_fingerprint"] = composition_fingerprint(composition)
        payload["source_locator"] = attributes.get("source_locator", {})
    elif entity_type == "Passage":
        payload["source_id"] = attributes.get("source_id", "")
        payload["locator"] = attributes.get("locator", {})
    digest = stable_digest(f"node:{entity_type}", payload)
    return f"kg:{entity_type.lower()}:{digest}"


def make_evidence_id(
    source_id: str,
    locator: Mapping[str, Any],
    quote_sha256: str,
) -> str:
    payload = {
        "source_id": source_id,
        "locator": dict(locator),
        "quote_sha256": quote_sha256,
    }
    return f"kg:evidence:{stable_digest('evidence', payload)}"


def make_edge_id(
    subject_id: str,
    predicate: str,
    object_id: str,
    evidence_ids: Iterable[str],
    assertion_mode: str,
    attributes: Mapping[str, Any] | None = None,
) -> str:
    payload = {
        "subject_id": subject_id,
        "predicate": predicate,
        "object_id": object_id,
        "evidence_ids": sorted(set(evidence_ids)),
        "assertion_mode": assertion_mode,
        "attributes": dict(attributes or {}),
    }
    return f"kg:assertion:{stable_digest('edge', payload)}"
