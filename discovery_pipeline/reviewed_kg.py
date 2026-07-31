from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from knowledge_graph.build import build_bundle
from knowledge_graph.validate import validate_graph

from .annotation import AnnotationError


_GRADE_RANK = {"E1": 1, "E2": 2, "E3": 3, "E4": 4, "E5": 5}
_ORIGINAL_STUDIES = {
    "randomized_trial",
    "controlled_clinical",
    "observational_clinical",
    "animal",
    "in_vitro",
    "analytical_chemistry",
}
_REVIEWS = {"systematic_review", "narrative_review"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AnnotationError(f"JSON root must be an object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise AnnotationError(f"blank JSONL line at {line_number}: {path}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise AnnotationError(f"JSONL line is not an object: {line_number}")
            records.append(value)
    return records


def _evidence_profile(study_type: str) -> tuple[str, str]:
    if study_type in _ORIGINAL_STUDIES:
        return "E1", "experimental"
    if study_type in _REVIEWS:
        return "E2", "authoritative_curated"
    if study_type == "computational":
        return "E4", "database_prediction"
    return "E4", "modern_bridge"


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def build_reviewed_kg_bundle(
    *,
    finalization_report_path: Path,
    catalog_path: Path,
    modern_database_path: Path,
    graph_version: str,
    parent_version: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not graph_version.strip():
        raise AnnotationError("graph_version must be non-empty")
    report = _load_object(finalization_report_path)
    if report.get("valid") is not True:
        raise AnnotationError("finalization report is not valid")
    annotations_path = finalization_report_path.parent / "final_annotations.jsonl"
    expected_sha = (
        report.get("files", {}).get("final_annotations.jsonl", {}).get("sha256")
    )
    if not annotations_path.is_file() or _sha256_file(annotations_path) != expected_sha:
        raise AnnotationError("final annotations are missing or changed")
    annotations = _load_jsonl(annotations_path)
    if len(annotations) != report.get("item_count"):
        raise AnnotationError("final annotation count differs from report")
    approved = [
        value
        for value in annotations
        if value.get("scientific_evidence_approved") is True
        and value.get("review_status") == "approved"
    ]
    if len(approved) != report.get("approved_scientific_evidence_count"):
        raise AnnotationError("approved annotation count differs from report")
    if not approved:
        raise AnnotationError("no approved evidence is available for KG conversion")

    catalog = _load_object(catalog_path)
    raw_candidates = list(catalog.get("candidates", []))
    catalog_by_id = {
        str(value["candidate_id"]): dict(value) for value in raw_candidates
    }
    if len(catalog_by_id) != len(raw_candidates):
        raise AnnotationError("catalog contains duplicate candidate_id values")

    database_sha_before = _sha256_file(modern_database_path)
    sources_by_doc: dict[str, dict[str, Any]] = {}
    studies_by_doc: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    evidence_by_pair: defaultdict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    connection = _open_read_only(modern_database_path)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise AnnotationError(f"modern database quick_check failed: {quick_check}")
        for record in sorted(approved, key=lambda value: str(value["locus_id"])):
            candidate_id = str(record["candidate_id"])
            if candidate_id not in catalog_by_id:
                raise AnnotationError(f"approved evidence has unknown candidate: {candidate_id}")
            row = connection.execute(
                """
                SELECT c.chunk_id, c.doc_id, c.pdf_page, c.text,
                       d.title, d.year, d.doi, d.source_filename, d.sha256
                FROM chunks AS c
                JOIN documents AS d ON d.doc_id = c.doc_id
                WHERE c.chunk_id = ?
                """,
                (str(record["chunk_id"]),),
            ).fetchone()
            if row is None:
                raise AnnotationError(f"approved source chunk is missing: {record['chunk_id']}")
            text = str(row["text"] or "")
            checks = {
                "doc_id": str(row["doc_id"]),
                "pdf_page": int(row["pdf_page"]),
                "title": str(row["title"] or ""),
                "year": str(row["year"] or ""),
                "doi": str(row["doi"] or ""),
                "source_filename": str(row["source_filename"] or ""),
                "source_sha256": str(row["sha256"] or ""),
                "chunk_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            for field, actual in checks.items():
                expected = record.get(field)
                if field == "pdf_page":
                    equal = int(expected) == actual
                else:
                    equal = str(expected or "") == str(actual or "")
                if not equal:
                    raise AnnotationError(
                        f"approved source differs from database: {record['locus_id']}/{field}"
                    )
            doc_id = str(row["doc_id"])
            source_key = f"source:{hashlib.sha256(doc_id.encode()).hexdigest()[:20]}"
            study_key = f"study:{hashlib.sha256(doc_id.encode()).hexdigest()[:20]}"
            sources_by_doc.setdefault(
                doc_id,
                {
                    "key": source_key,
                    "source_type": "modern_pdf",
                    "title": str(row["title"]),
                    "file_name": str(row["source_filename"]),
                    "file_sha256": str(row["sha256"]),
                    "doi": str(row["doi"] or ""),
                    "year": str(row["year"] or ""),
                    "attributes": {"doc_id": doc_id},
                },
            )
            studies_by_doc.setdefault(
                doc_id,
                {
                    "key": study_key,
                    "entity_type": "Study",
                    "canonical_name": str(row["title"]),
                    "identity": {"doc_id": doc_id},
                    "external_ids": (
                        {"doi": str(row["doi"])} if row["doi"] else {}
                    ),
                    "attributes": {
                        "doc_id": doc_id,
                        "year": str(row["year"] or ""),
                        "source_key": source_key,
                    },
                },
            )
            final = dict(record["final_annotation"])
            grade, evidence_class = _evidence_profile(str(final["study_type"]))
            evidence_key = f"evidence:{hashlib.sha256(str(record['locus_id']).encode()).hexdigest()[:20]}"
            evidence.append(
                {
                    "key": evidence_key,
                    "source": source_key,
                    "locator": {
                        "doc_id": doc_id,
                        "chunk_id": str(row["chunk_id"]),
                        "pdf_page": int(row["pdf_page"]),
                        "chunk_text_sha256": checks["chunk_text_sha256"],
                    },
                    "quote": text,
                    "evidence_grade": grade,
                    "evidence_class": evidence_class,
                    "review": {
                        "status": "approved",
                        "reviewer_a": str(record["reviewer_a_id"]),
                        "reviewer_a_reviewed_at": str(
                            record["reviewer_a_reviewed_at"]
                        ),
                        "reviewer_b": str(record["reviewer_b_id"]),
                        "reviewer_b_reviewed_at": str(
                            record["reviewer_b_reviewed_at"]
                        ),
                        "adjudicator": str(record["adjudicator_id"]),
                        "adjudicated_at": str(record["adjudicated_at"]),
                        "workflow": "blinded_dual_review_with_independent_adjudication",
                    },
                    "attributes": {
                        "locus_id": str(record["locus_id"]),
                        "annotation": final,
                        "adjudication_decision": str(record["adjudication_decision"]),
                    },
                }
            )
            evidence_by_pair[(candidate_id, doc_id)].append(
                {"key": evidence_key, "grade": grade, "locus_id": str(record["locus_id"])}
            )
    finally:
        connection.close()
    database_sha_after = _sha256_file(modern_database_path)
    if database_sha_before != database_sha_after:
        raise AnnotationError("modern database changed during KG conversion")

    compounds: list[dict[str, Any]] = []
    represented_candidates = sorted({candidate for candidate, _ in evidence_by_pair})
    for candidate_id in represented_candidates:
        candidate = catalog_by_id[candidate_id]
        aliases = [
            str(value)
            for value in [candidate.get("name_zh", ""), *candidate.get("aliases", [])]
            if str(value).strip()
        ]
        compounds.append(
            {
                "key": candidate_id,
                "entity_type": "Compound",
                "canonical_name": str(candidate["canonical_name"]),
                "aliases": aliases,
                "identity": {"candidate_id": candidate_id},
                "attributes": {
                    "candidate_id": candidate_id,
                    "candidate_role": str(candidate.get("candidate_role", "")),
                    "herb_ids": list(candidate.get("herb_ids", [])),
                    "identity_review_status": "requires_curator_review",
                    "expected_pubchem_cid": candidate.get("expected_pubchem_cid"),
                },
            }
        )

    assertions = []
    for (candidate_id, doc_id), supports in sorted(evidence_by_pair.items()):
        edge_grade = max(
            (value["grade"] for value in supports),
            key=lambda value: _GRADE_RANK[value],
        )
        assertions.append(
            {
                "subject": candidate_id,
                "predicate": "STUDIED_IN",
                "object": studies_by_doc[doc_id]["key"],
                "evidence": [value["key"] for value in supports],
                "evidence_grade": edge_grade,
                "assertion_mode": "explicit",
                "confidence": 0.8,
                "review_status": "pending",
                "attributes": {
                    "locus_ids": sorted(value["locus_id"] for value in supports),
                    "pending_reason": "compound_identity_requires_curator_review",
                    "claim_scope": "compound_mention_studied_in_source_only",
                },
            }
        )

    bundle = {
        "schema_version": "1.0.0",
        "bundle_id": f"reviewed-literature-overlay:{report['batch_id']}",
        "graph_version": graph_version,
        "metadata": {
            "description": "Adjudicated modern-literature study linkage overlay.",
            "parent_version": parent_version or None,
            "release_approved": False,
            "release_blocker": "compound_identity_requires_curator_review",
            "claim_scope": (
                "STUDIED_IN records only; no target, pathway, efficacy, safety, "
                "or burn-treatment claim is inferred from annotation labels."
            ),
            "batch_id": report["batch_id"],
            "finalization_report_sha256": _sha256_file(finalization_report_path),
            "catalog_sha256": _sha256_file(catalog_path),
            "modern_database_sha256": database_sha_before,
        },
        "sources": [sources_by_doc[key] for key in sorted(sources_by_doc)],
        "entities": compounds + [studies_by_doc[key] for key in sorted(studies_by_doc)],
        "evidence": evidence,
        "assertions": assertions,
    }
    graph = build_bundle(bundle)
    validation = validate_graph(graph, release=False)
    if not validation["valid"]:
        raise AnnotationError("generated reviewed KG bundle failed structural validation")
    result = {
        "valid": True,
        "graph_version": graph_version,
        "source_count": len(bundle["sources"]),
        "compound_count": len(compounds),
        "study_count": len(studies_by_doc),
        "evidence_count": len(evidence),
        "assertion_count": len(assertions),
        "source_database_unchanged": True,
        "modern_database_sha256": database_sha_before,
        "draft_validation": validation,
        "release_ready": False,
        "release_blocker": "compound_identity_requires_curator_review",
    }
    return bundle, result
