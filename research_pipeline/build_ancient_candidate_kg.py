from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_pipeline.formula_candidates import (
    extract_formula_candidates,
    load_formula_lexicon,
)


LAYER_ENTITY_TYPES = {
    "direct_cause": "Disease",
    "direct_disease": "Disease",
    "wound_phenotype": "BurnPhenotype",
    "pathogenesis": "Syndrome",
    "therapy": "TreatmentMethod",
}
GENERIC_EXCLUSION_TERM_IDS = {"burn.exclude.decoction_name"}


class CandidateGraphError(ValueError):
    pass


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _open_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve().as_posix()
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _load_ontology(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise CandidateGraphError("ontology must be an object with entries")
    term_ids = [str(entry.get("term_id", "")) for entry in value["entries"]]
    if not term_ids or "" in term_ids or len(set(term_ids)) != len(term_ids):
        raise CandidateGraphError("ontology term_id values must be non-empty and unique")
    unknown_layers = {
        str(entry.get("layer", ""))
        for entry in value["entries"]
        if entry.get("layer") not in {*LAYER_ENTITY_TYPES, "exclusion"}
    }
    if unknown_layers:
        raise CandidateGraphError(f"ontology contains unsupported layers: {unknown_layers}")
    return value


def _entry_occurrences(text: str, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw: list[tuple[int, int, str]] = []
    for surface in sorted(
        {str(value) for value in entry.get("surface_forms", []) if str(value)},
        key=lambda value: (-len(value), value),
    ):
        start = 0
        while True:
            index = text.find(surface, start)
            if index < 0:
                break
            raw.append((index, index + len(surface), surface))
            start = index + 1
    retained: list[tuple[int, int, str]] = []
    for start, end, surface in sorted(raw, key=lambda value: (-(value[1] - value[0]), value[0])):
        if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in retained):
            continue
        retained.append((start, end, surface))
    return [
        {
            "term_id": str(entry["term_id"]),
            "canonical": str(entry["canonical"]),
            "layer": str(entry["layer"]),
            "evidence_channel": str(entry["evidence_channel"]),
            "search_weight": float(entry["search_weight"]),
            "surface": surface,
            "start": start,
            "end": end,
        }
        for start, end, surface in sorted(retained)
    ]


def _overlaps(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return int(left["start"]) < int(right["end"]) and int(left["end"]) > int(
        right["start"]
    )


def _match_entries(
    text: str,
    entries: list[Mapping[str, Any]],
    *,
    window_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exclusions: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("evidence_channel") == "exclude":
            exclusions.extend(_entry_occurrences(text, entry))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("evidence_channel") == "exclude":
            continue
        required_context = [str(value) for value in entry.get("required_context", [])]
        for match in _entry_occurrences(text, entry):
            left = max(0, int(match["start"]) - window_chars)
            right = min(len(text), int(match["end"]) + window_chars)
            window = text[left:right]
            reasons: list[str] = []
            if required_context and not any(value in window for value in required_context):
                reasons.append("required_context_missing")
            suppressors = sorted(
                {
                    str(exclusion["term_id"])
                    for exclusion in exclusions
                    if exclusion["term_id"] not in GENERIC_EXCLUSION_TERM_IDS
                    and not _overlaps(match, exclusion)
                    and int(exclusion["start"]) < right
                    and int(exclusion["end"]) > left
                }
            )
            if suppressors:
                reasons.append("exclusion_context:" + ",".join(suppressors))
            enriched = {**match, "window_start": left, "window_end": right}
            if reasons:
                rejected.append({**enriched, "reasons": reasons})
            else:
                accepted.append(enriched)
    return accepted, rejected


def _combined_score(matches: Iterable[Mapping[str, Any]]) -> float:
    weights: dict[str, float] = {}
    for match in matches:
        term_id = str(match["term_id"])
        weights[term_id] = max(weights.get(term_id, 0.0), float(match["search_weight"]))
    residual = 1.0
    for weight in weights.values():
        residual *= 1.0 - min(1.0, max(0.0, weight))
    return round(1.0 - residual, 6)


def _candidate_confidence(
    semantic_score: float,
    average_ocr_confidence: Any,
    low_confidence_ocr: bool,
) -> tuple[float, float]:
    if average_ocr_confidence is None:
        text_quality_factor = 1.0
    else:
        try:
            text_quality_factor = float(average_ocr_confidence)
        except (TypeError, ValueError) as exc:
            raise CandidateGraphError(
                f"invalid average OCR confidence: {average_ocr_confidence!r}"
            ) from exc
        if not 0.0 <= text_quality_factor <= 1.0:
            raise CandidateGraphError(
                f"average OCR confidence outside 0-1: {text_quality_factor}"
            )
    confidence = semantic_score * text_quality_factor
    if low_confidence_ocr:
        confidence = min(confidence, 0.69)
    return round(confidence, 6), round(text_quality_factor, 6)


def _page_classification(
    accepted: list[dict[str, Any]],
    rules: Mapping[str, Any],
) -> tuple[str, float, float]:
    direct = [value for value in accepted if value["evidence_channel"] == "A_direct"]
    transfer = [
        value for value in accepted if value["evidence_channel"] == "B_transfer"
    ]
    direct_score = _combined_score(direct)
    transfer_score = _combined_score(transfer)
    if direct and direct_score >= float(rules["direct_channel_threshold_initial"]):
        return "direct_burn_candidate", direct_score, transfer_score
    transfer_layers = {str(value["layer"]) for value in transfer}
    if (
        len(transfer_layers) >= int(rules["transfer_minimum_evidence_classes"])
        and transfer_score >= float(rules["transfer_channel_threshold_initial"])
    ):
        return "ulcer_transfer_candidate", direct_score, transfer_score
    return "not_selected", direct_score, transfer_score


def _work_title(raw_title: str) -> str:
    value = re.sub(r"^\d+_", "", raw_title)
    for marker in ("_公开扫描版", "_文本检索副本", "_可检索版", "_SSID_"):
        value = value.split(marker, 1)[0]
    value = re.sub(r"_卷.*$", "", value)
    return value or raw_title


def _quote(text: str, match: Mapping[str, Any], window_chars: int) -> str:
    left = max(0, int(match["start"]) - window_chars)
    right = min(len(text), int(match["end"]) + window_chars)
    return text[left:right]


def build_ancient_candidate_bundle(
    *,
    database_path: Path,
    ontology_path: Path,
    output_bundle_path: Path,
    output_manifest_path: Path,
    graph_version: str,
    parent_version: str = "",
    formula_lexicon_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if output_bundle_path.exists() or output_manifest_path.exists():
        raise FileExistsError("refusing to overwrite candidate KG outputs")
    ontology = _load_ontology(ontology_path)
    rules = dict(ontology.get("retrieval_rules", {}))
    window_chars = int(rules["context_window_chars"])
    database_sha256_before = _sha256_file(database_path)
    ontology_sha256 = _sha256_file(ontology_path)
    entries = [dict(value) for value in ontology["entries"]]
    if formula_lexicon_path is None:
        formula_lexicon_path = (
            Path(__file__).resolve().parent / "data" / "formula_herb_lexicon_v1.json"
        )
    formula_lexicon = load_formula_lexicon(formula_lexicon_path)
    formula_lexicon_sha256 = _sha256_file(formula_lexicon_path)
    formula_definitions = {
        str(value["formula_id"]): dict(value)
        for value in formula_lexicon["formulas"]
    }

    sources: dict[str, dict[str, Any]] = {}
    entities: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    assertions: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    candidate_rows: list[dict[str, Any]] = []
    book_evidence: defaultdict[str, set[str]] = defaultdict(set)
    classification_counts: Counter[str] = Counter()
    book_classification_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    accepted_term_counts: Counter[str] = Counter()
    rejected_reason_counts: Counter[str] = Counter()
    formula_candidate_counts: Counter[str] = Counter()
    formula_ingredient_counts: Counter[str] = Counter()

    def add_entity(key: str, value: dict[str, Any]) -> None:
        prior = entities.setdefault(key, value)
        if prior != value:
            raise CandidateGraphError(f"entity key collision: {key}")

    def add_assertion(
        subject: str,
        predicate: str,
        object_key: str,
        evidence_keys: Iterable[str],
        *,
        evidence_grade: str,
        assertion_mode: str,
        confidence: float,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_attributes = dict(attributes or {})
        identity = (
            subject,
            predicate,
            object_key,
            assertion_mode,
            _canonical_json(normalized_attributes),
        )
        record = assertions.get(identity)
        if record is None:
            record = {
                "subject": subject,
                "predicate": predicate,
                "object": object_key,
                "evidence": [],
                "evidence_grade": evidence_grade,
                "assertion_mode": assertion_mode,
                "confidence": confidence,
                "review_status": "pending",
                "attributes": normalized_attributes,
            }
            assertions[identity] = record
        if record["evidence_grade"] != evidence_grade:
            raise CandidateGraphError(f"assertion grade collision: {identity}")
        record["confidence"] = max(float(record["confidence"]), confidence)
        record["evidence"] = sorted(
            {*record["evidence"], *(str(value) for value in evidence_keys)}
        )

    connection = _open_read_only(database_path)
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise CandidateGraphError(f"database quick_check failed: {quick_check}")
        books = {
            str(row["book_id"]): dict(row)
            for row in connection.execute("SELECT * FROM books ORDER BY book_id")
        }
        rows = connection.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label,
                   p.text, p.average_confidence, p.low_confidence
            FROM pages AS p
            ORDER BY p.book_id, p.physical_page
            """
        )
        page_count = 0
        for row in rows:
            page_count += 1
            text = str(row["text"] or "")
            accepted, rejected = _match_entries(
                text,
                entries,
                window_chars=window_chars,
            )
            classification, direct_score, transfer_score = _page_classification(
                accepted, rules
            )
            formula_candidates = extract_formula_candidates(text, formula_lexicon)
            burn_semantic_confidence = (
                direct_score
                if classification == "direct_burn_candidate"
                else transfer_score
            )
            if classification == "not_selected" and formula_candidates:
                classification = "formula_reference_candidate"
            formula_semantic_confidence = max(
                (
                    float(value["semantic_confidence"])
                    for value in formula_candidates
                ),
                default=0.0,
            )
            semantic_confidence = max(
                burn_semantic_confidence, formula_semantic_confidence
            )
            burn_candidate_confidence, _ = _candidate_confidence(
                burn_semantic_confidence,
                row["average_confidence"],
                bool(row["low_confidence"]),
            )
            candidate_confidence, text_quality_factor = _candidate_confidence(
                semantic_confidence,
                row["average_confidence"],
                bool(row["low_confidence"]),
            )
            classification_counts[classification] += 1
            book_id = str(row["book_id"])
            book = books[book_id]
            title = str(book["title"])
            book_classification_counts[title][classification] += 1
            for value in rejected:
                for reason in value["reasons"]:
                    rejected_reason_counts[reason] += 1
            if classification == "not_selected":
                continue

            selected_matches = [
                value
                for value in accepted
                if value["evidence_channel"] in {"A_direct", "B_transfer", "context_only"}
            ]
            page_id = str(row["page_id"])
            physical_page = int(row["physical_page"])
            page_text_sha256 = _sha256_text(text)
            source_key = f"source:{book_id}"
            work_key = f"work:{book_id}"
            edition_key = f"edition:{book_id}"
            passage_key = f"passage:{page_id}"
            canonical_work_title = _work_title(title)
            sources[source_key] = {
                "key": source_key,
                "source_type": "ancient_pdf",
                "title": title,
                "file_name": str(book["filename"]),
                "file_sha256": str(book["source_sha256"]),
                "work_id": f"work:{book_id}",
                "edition_id": f"edition:{book_id}:{str(book['source_sha256'])[:16]}",
                "attributes": {
                    "book_id": book_id,
                    "page_count": int(book["page_count"]),
                    "processing_mode": str(book["processing_mode"]),
                },
            }
            add_entity(
                work_key,
                {
                    "key": work_key,
                    "entity_type": "ClassicWork",
                    "canonical_name": canonical_work_title,
                    "identity": {"book_id": book_id},
                    "attributes": {"database_title": title},
                },
            )
            add_entity(
                edition_key,
                {
                    "key": edition_key,
                    "entity_type": "Edition",
                    "canonical_name": f"{canonical_work_title}检索版本",
                    "identity": {
                        "book_id": book_id,
                        "source_sha256": str(book["source_sha256"]),
                    },
                    "attributes": {
                        "processing_mode": str(book["processing_mode"]),
                        "source_sha256": str(book["source_sha256"]),
                    },
                },
            )
            add_entity(
                passage_key,
                {
                    "key": passage_key,
                    "entity_type": "Passage",
                    "canonical_name": f"{canonical_work_title}第{physical_page}页",
                    "attributes": {
                        "source_id": source_key,
                        "locator": {
                            "page_id": page_id,
                            "book_id": book_id,
                            "physical_page": physical_page,
                        },
                        "page_text_sha256": page_text_sha256,
                        "candidate_classification": classification,
                    },
                },
            )

            term_evidence: defaultdict[str, set[str]] = defaultdict(set)
            page_evidence: set[str] = set()
            for match in selected_matches:
                term_id = str(match["term_id"])
                layer = str(match["layer"])
                entity_type = LAYER_ENTITY_TYPES[layer]
                term_key = f"term:{term_id}"
                entry = next(value for value in entries if value["term_id"] == term_id)
                add_entity(
                    term_key,
                    {
                        "key": term_key,
                        "entity_type": entity_type,
                        "canonical_name": str(match["canonical"]),
                        "aliases": sorted(
                            {
                                str(value)
                                for value in entry.get("surface_forms", [])
                                if str(value) != str(match["canonical"])
                            }
                        ),
                        "identity": {"ontology_term_id": term_id},
                        "external_ids": {"ontology_term_id": term_id},
                        "attributes": {
                            "ontology_layer": layer,
                            "evidence_channel": str(match["evidence_channel"]),
                            "search_weight": float(match["search_weight"]),
                            "automatic_candidate": True,
                        },
                    },
                )
                quote = _quote(text, match, window_chars)
                evidence_key = f"evidence:{page_id}:{_sha256_text(quote)[:20]}"
                evidence_record = evidence.get(evidence_key)
                if evidence_record is None:
                    evidence_record = {
                        "key": evidence_key,
                        "source": source_key,
                        "locator": {
                            "page_id": page_id,
                            "book_id": book_id,
                            "physical_page": physical_page,
                            "pdf_page_label": row["pdf_page_label"],
                            "page_text_sha256": page_text_sha256,
                        },
                        "quote": quote,
                        "evidence_grade": "E1",
                        "evidence_class": "direct_ancient",
                        "review": {
                            "status": "pending",
                            "reason": "awaiting_automatic_confidence_threshold",
                        },
                        "attributes": {
                            "candidate_classification": classification,
                            "candidate_confidence": burn_candidate_confidence,
                            "semantic_confidence": burn_semantic_confidence,
                            "text_quality_factor": text_quality_factor,
                            "confidence_basis": (
                                "direct_channel_score"
                                if classification == "direct_burn_candidate"
                                else "transfer_channel_score"
                            ),
                            "matched_term_ids": [],
                            "matched_surfaces": [],
                            "average_ocr_confidence": row["average_confidence"],
                            "low_confidence_ocr": bool(row["low_confidence"]),
                        },
                    }
                    evidence[evidence_key] = evidence_record
                evidence_record["attributes"]["matched_term_ids"] = sorted(
                    {*evidence_record["attributes"]["matched_term_ids"], term_id}
                )
                evidence_record["attributes"]["matched_surfaces"] = sorted(
                    {
                        *evidence_record["attributes"]["matched_surfaces"],
                        str(match["surface"]),
                    }
                )
                term_evidence[term_key].add(evidence_key)
                page_evidence.add(evidence_key)
                accepted_term_counts[term_id] += 1

            formula_ids_on_page: list[str] = []
            formula_variant_keys: list[str] = []
            for formula_candidate in formula_candidates:
                formula_id = str(formula_candidate["formula_id"])
                formula_definition = formula_definitions[formula_id]
                formula_name = str(formula_candidate["canonical_name"])
                formula_confidence, _ = _candidate_confidence(
                    float(formula_candidate["semantic_confidence"]),
                    row["average_confidence"],
                    bool(row["low_confidence"]),
                )
                formula_concept_key = f"formula-concept:{formula_id}"
                composition = [dict(value) for value in formula_candidate["composition"]]
                variant_digest = _sha256_text(
                    _canonical_json(
                        {
                            "formula_id": formula_id,
                            "composition": composition,
                            "page_id": page_id,
                            "name_start": int(formula_candidate["name_start"]),
                        }
                    )
                )[:20]
                formula_variant_key = f"formula-variant:{variant_digest}"
                formula_ids_on_page.append(formula_id)
                formula_variant_keys.append(formula_variant_key)
                formula_candidate_counts[formula_id] += 1

                add_entity(
                    formula_concept_key,
                    {
                        "key": formula_concept_key,
                        "entity_type": "FormulaConcept",
                        "canonical_name": formula_name,
                        "aliases": sorted(
                            {
                                str(value)
                                for value in formula_definition.get("aliases", [])
                                if str(value) != formula_name
                            }
                        ),
                        "identity": {"formula_lexicon_id": formula_id},
                        "external_ids": {"formula_lexicon_id": formula_id},
                        "attributes": {
                            "automatic_candidate": True,
                            "lexicon_id": formula_lexicon["lexicon_id"],
                        },
                    },
                )
                add_entity(
                    formula_variant_key,
                    {
                        "key": formula_variant_key,
                        "entity_type": "FormulaVariant",
                        "canonical_name": formula_name,
                        "aliases": [str(formula_candidate["name_surface"])],
                        "identity": {
                            "formula_lexicon_id": formula_id,
                            "page_id": page_id,
                            "name_start": int(formula_candidate["name_start"]),
                        },
                        "attributes": {
                            "formula_name": str(formula_candidate["name_surface"]),
                            "composition": composition,
                            "source_locator": {
                                "source_id": source_key,
                                "book_id": book_id,
                                "page_id": page_id,
                                "physical_page": physical_page,
                                "name_start": int(formula_candidate["name_start"]),
                            },
                            "composition_scope": formula_candidate[
                                "composition_scope"
                            ],
                            "composition_complete": bool(
                                formula_candidate["composition_complete"]
                            ),
                            "undosed_ingredients": list(
                                formula_candidate["undosed_ingredients"]
                            ),
                            "preparation_markers": list(
                                formula_candidate["preparation_markers"]
                            ),
                            "semantic_confidence": float(
                                formula_candidate["semantic_confidence"]
                            ),
                            "automatic_candidate": True,
                            "extraction_policy": formula_candidate[
                                "extraction_policy"
                            ],
                        },
                    },
                )

                quote = str(formula_candidate["quote"])
                evidence_key = (
                    f"evidence:{page_id}:formula:"
                    + _sha256_text(
                        _canonical_json(
                            {
                                "formula_id": formula_id,
                                "name_start": int(formula_candidate["name_start"]),
                                "quote": quote,
                            }
                        )
                    )[:20]
                )
                evidence_record = evidence.get(evidence_key)
                if evidence_record is None:
                    evidence_record = {
                        "key": evidence_key,
                        "source": source_key,
                        "locator": {
                            "page_id": page_id,
                            "book_id": book_id,
                            "physical_page": physical_page,
                            "pdf_page_label": row["pdf_page_label"],
                            "page_text_sha256": page_text_sha256,
                            "char_start": int(formula_candidate["window_start"]),
                            "char_end": int(formula_candidate["window_end"]),
                        },
                        "quote": quote,
                        "evidence_grade": "E1",
                        "evidence_class": "direct_ancient",
                        "review": {
                            "status": "pending",
                            "reason": "awaiting_automatic_confidence_threshold",
                        },
                        "attributes": {
                            "candidate_classification": classification,
                            "candidate_confidence": formula_confidence,
                            "semantic_confidence": float(
                                formula_candidate["semantic_confidence"]
                            ),
                            "text_quality_factor": text_quality_factor,
                            "confidence_basis": "explicit_formula_name_and_dosed_ingredients",
                            "formula_ids": [],
                            "average_ocr_confidence": row["average_confidence"],
                            "low_confidence_ocr": bool(row["low_confidence"]),
                        },
                    }
                    evidence[evidence_key] = evidence_record
                else:
                    evidence_record["attributes"]["candidate_confidence"] = max(
                        float(
                            evidence_record["attributes"]["candidate_confidence"]
                        ),
                        formula_confidence,
                    )
                evidence_record["attributes"]["formula_ids"] = sorted(
                    {
                        *evidence_record["attributes"].get("formula_ids", []),
                        formula_id,
                    }
                )
                page_evidence.add(evidence_key)

                relation_confidence = float(formula_candidate["semantic_confidence"])
                add_assertion(
                    formula_variant_key,
                    "VARIANT_OF",
                    formula_concept_key,
                    [evidence_key],
                    evidence_grade="E1",
                    assertion_mode="explicit",
                    confidence=relation_confidence,
                    attributes={
                        "automatic_candidate": True,
                        "identity_basis": "composition_and_source_locator",
                    },
                )
                for entity_key in (formula_concept_key, formula_variant_key):
                    add_assertion(
                        entity_key,
                        "RECORDED_IN",
                        passage_key,
                        [evidence_key],
                        evidence_grade="E1",
                        assertion_mode="explicit",
                        confidence=relation_confidence,
                    )
                    add_assertion(
                        passage_key,
                        "MENTIONS",
                        entity_key,
                        [evidence_key],
                        evidence_grade="E1",
                        assertion_mode="explicit",
                        confidence=relation_confidence,
                    )

                mentions_by_name = {
                    str(value["canonical_name"]): value
                    for value in formula_candidate["ingredient_mentions"]
                }
                for herb_definition in formula_definition.get("ingredients", []):
                    herb_name = str(herb_definition["canonical_name"])
                    mention = mentions_by_name.get(herb_name)
                    if mention is None:
                        continue
                    herb_id = str(herb_definition["herb_id"])
                    herb_key = f"herb:{herb_id}"
                    add_entity(
                        herb_key,
                        {
                            "key": herb_key,
                            "entity_type": "Herb",
                            "canonical_name": herb_name,
                            "aliases": sorted(
                                {
                                    str(value)
                                    for value in herb_definition.get("aliases", [])
                                    if str(value) != herb_name
                                }
                            ),
                            "identity": {"herb_lexicon_id": herb_id},
                            "external_ids": {"herb_lexicon_id": herb_id},
                            "attributes": {
                                "automatic_candidate": True,
                                "lexicon_id": formula_lexicon["lexicon_id"],
                            },
                        },
                    )
                    formula_ingredient_counts[herb_id] += 1
                    add_assertion(
                        herb_key,
                        "RECORDED_IN",
                        passage_key,
                        [evidence_key],
                        evidence_grade="E1",
                        assertion_mode="explicit",
                        confidence=relation_confidence,
                    )
                    add_assertion(
                        passage_key,
                        "MENTIONS",
                        herb_key,
                        [evidence_key],
                        evidence_grade="E1",
                        assertion_mode="explicit",
                        confidence=relation_confidence,
                    )
                for item in composition:
                    herb_name = str(item["herb"])
                    mention = mentions_by_name[herb_name]
                    herb_key = f"herb:{str(mention['herb_id'])}"
                    add_assertion(
                        formula_variant_key,
                        "HAS_INGREDIENT",
                        herb_key,
                        [evidence_key],
                        evidence_grade="E1",
                        assertion_mode="explicit",
                        confidence=relation_confidence,
                        attributes={
                            "dose_value": item["dose_value"],
                            "dose_unit": item["dose_unit"],
                            "dose_text_original": item["dose_text_original"],
                            "automatic_candidate": True,
                        },
                    )

            add_assertion(
                edition_key,
                "HAS_PASSAGE",
                passage_key,
                page_evidence,
                evidence_grade="E1",
                assertion_mode="explicit",
                confidence=1.0,
            )
            book_evidence[book_id].update(page_evidence)
            for term_key, evidence_keys in sorted(term_evidence.items()):
                add_assertion(
                    term_key,
                    "RECORDED_IN",
                    passage_key,
                    evidence_keys,
                    evidence_grade="E1",
                    assertion_mode="explicit",
                    confidence=0.95,
                )
                add_assertion(
                    passage_key,
                    "MENTIONS",
                    term_key,
                    evidence_keys,
                    evidence_grade="E1",
                    assertion_mode="explicit",
                    confidence=0.95,
                )

            clinical_terms = {
                term_key
                for term_key in term_evidence
                if entities[term_key]["entity_type"]
                in {"Disease", "Syndrome", "BurnPhenotype"}
            }
            therapy_terms = {
                term_key
                for term_key in term_evidence
                if entities[term_key]["entity_type"] == "TreatmentMethod"
            }
            for clinical_key in sorted(clinical_terms):
                for therapy_key in sorted(therapy_terms):
                    support = term_evidence[clinical_key] | term_evidence[therapy_key]
                    add_assertion(
                        clinical_key,
                        "HAS_TREATMENT_METHOD",
                        therapy_key,
                        support,
                        evidence_grade="E5",
                        assertion_mode="hypothesis",
                        confidence=(
                            0.35 if classification == "direct_burn_candidate" else 0.25
                        ),
                        attributes={
                            "candidate_basis": "same_page_cooccurrence_not_causality",
                            "automatic_candidate": True,
                        },
                    )

            candidate_rows.append(
                {
                    "page_id": page_id,
                    "book_id": book_id,
                    "title": title,
                    "physical_page": physical_page,
                    "pdf_page_label": row["pdf_page_label"],
                    "page_text_sha256": page_text_sha256,
                    "source_sha256": str(book["source_sha256"]),
                    "classification": classification,
                    "direct_score": direct_score,
                    "transfer_score": transfer_score,
                    "candidate_confidence": candidate_confidence,
                    "semantic_confidence": semantic_confidence,
                    "text_quality_factor": text_quality_factor,
                    "matched_term_ids": sorted({str(value["term_id"]) for value in selected_matches}),
                    "matched_surfaces": sorted({str(value["surface"]) for value in selected_matches}),
                    "formula_ids": sorted(set(formula_ids_on_page)),
                    "formula_variant_keys": sorted(set(formula_variant_keys)),
                    "rejected_match_count": len(rejected),
                    "low_confidence_ocr": bool(row["low_confidence"]),
                    "review_status": "pending_automatic_confidence_threshold",
                }
            )
        expected_page_count = sum(int(value["page_count"]) for value in books.values())
        if page_count != expected_page_count:
            raise CandidateGraphError(
                f"page count differs: rows={page_count}, books={expected_page_count}"
            )
    finally:
        connection.close()

    for book_id, evidence_keys in sorted(book_evidence.items()):
        add_assertion(
            f"work:{book_id}",
            "HAS_EDITION",
            f"edition:{book_id}",
            evidence_keys,
            evidence_grade="E1",
            assertion_mode="explicit",
            confidence=1.0,
        )

    candidate_rows.sort(key=lambda value: (value["book_id"], value["physical_page"]))
    manifest_payload = "".join(_canonical_json(value) + "\n" for value in candidate_rows)
    _atomic_text(output_manifest_path, manifest_payload)
    candidate_manifest_sha256 = _sha256_file(output_manifest_path)
    bundle_id = "ancient-candidate:" + _sha256_text(
        _canonical_json(
            {
                "database_sha256": database_sha256_before,
                "ontology_sha256": ontology_sha256,
                "formula_lexicon_sha256": formula_lexicon_sha256,
                "graph_version": graph_version,
                "candidate_manifest_sha256": candidate_manifest_sha256,
            }
        )
    )[:24]
    bundle = {
        "schema_version": "1.0.0",
        "bundle_id": bundle_id,
        "graph_version": graph_version,
        "metadata": {
            "description": "Ancient burn and target-formula candidate graph.",
            "parent_version": parent_version or None,
            "status": "candidate_pending_automatic_threshold",
            "release_approved": False,
            "database_sha256": database_sha256_before,
            "ontology_id": ontology["ontology_id"],
            "ontology_sha256": ontology_sha256,
            "formula_lexicon_id": formula_lexicon["lexicon_id"],
            "formula_lexicon_sha256": formula_lexicon_sha256,
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "selection_policy": {
                "direct": "valid direct term and calibrated initial score threshold",
                "transfer": "at least two ontology layers and transfer score threshold",
                "generic_decoction_exclusion": "ignored because no positive singleton 汤 term exists",
                "therapy_relation": "same-page hypothesis only; never TREATS",
                "formula": (
                    "explicit target formula name plus at least two target herbs "
                    "with exact source doses; no efficacy relation inferred"
                ),
            },
        },
        "sources": [sources[key] for key in sorted(sources)],
        "entities": [entities[key] for key in sorted(entities)],
        "evidence": [evidence[key] for key in sorted(evidence)],
        "assertions": [assertions[key] for key in sorted(assertions)],
    }
    _atomic_text(
        output_bundle_path,
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    database_sha256_after = _sha256_file(database_path)
    if database_sha256_after != database_sha256_before:
        raise CandidateGraphError("source database changed during candidate build")
    report = {
        "valid": True,
        "bundle_id": bundle_id,
        "graph_version": graph_version,
        "database_sha256_before": database_sha256_before,
        "database_sha256_after": database_sha256_after,
        "source_database_unchanged": True,
        "ontology_sha256": ontology_sha256,
        "formula_lexicon_id": formula_lexicon["lexicon_id"],
        "formula_lexicon_sha256": formula_lexicon_sha256,
        "scanned_books": len(books),
        "scanned_pages": page_count,
        "selected_pages": len(candidate_rows),
        "classification_counts": dict(sorted(classification_counts.items())),
        "selected_pages_by_book": {
            title: {
                key: value
                for key, value in sorted(counts.items())
                if key != "not_selected"
            }
            for title, counts in sorted(book_classification_counts.items())
        },
        "accepted_term_occurrences": sum(accepted_term_counts.values()),
        "accepted_term_counts": dict(sorted(accepted_term_counts.items())),
        "formula_candidates": sum(formula_candidate_counts.values()),
        "formula_candidate_counts": dict(sorted(formula_candidate_counts.items())),
        "formula_ingredient_mentions": sum(formula_ingredient_counts.values()),
        "formula_ingredient_counts": dict(sorted(formula_ingredient_counts.items())),
        "rejected_reason_counts": dict(sorted(rejected_reason_counts.items())),
        "sources": len(bundle["sources"]),
        "entities": len(bundle["entities"]),
        "evidence": len(bundle["evidence"]),
        "assertions": len(bundle["assertions"]),
        "candidate_manifest": str(output_manifest_path),
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "bundle": str(output_bundle_path),
        "bundle_sha256": _sha256_file(output_bundle_path),
        "review_status": "pending_automatic_threshold",
        "release_approved": False,
    }
    return bundle, report


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Build a source-traceable pending KG candidate layer from all ancient books"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--ontology",
        type=Path,
        default=root / "data" / "burn_ontology_v1.json",
    )
    parser.add_argument(
        "--formula-lexicon",
        type=Path,
        default=root / "data" / "formula_herb_lexicon_v1.json",
    )
    parser.add_argument("--output-bundle", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--graph-version", required=True)
    parser.add_argument("--parent-version", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _, report = build_ancient_candidate_bundle(
        database_path=args.database,
        ontology_path=args.ontology,
        output_bundle_path=args.output_bundle,
        output_manifest_path=args.output_manifest,
        graph_version=args.graph_version,
        parent_version=args.parent_version,
        formula_lexicon_path=args.formula_lexicon,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
