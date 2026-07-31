from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
CONTEXT_CLASSES = {"burn_context", "wound_context", "compound_only"}


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_discovery_intake(
    *,
    catalog_path: Path,
    resolution_path: Path,
    coverage_summary_path: Path,
    loci_path: Path,
    database_path: Path,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []

    def issue(code: str, detail: str) -> None:
        issues.append({"code": code, "detail": detail})

    catalog = _load_object(catalog_path)
    resolution = _load_object(resolution_path)
    summary = _load_object(coverage_summary_path)
    catalog_sha256 = _sha256_json(catalog)
    catalog_candidates = [dict(value) for value in catalog.get("candidates", [])]
    catalog_ids = [str(value.get("candidate_id", "")) for value in catalog_candidates]
    if not catalog_ids or any(not value for value in catalog_ids):
        issue("catalog_candidate_id_missing", "catalog has empty or no candidate IDs")
    duplicate_catalog_ids = sorted(
        key for key, count in Counter(catalog_ids).items() if key and count > 1
    )
    if duplicate_catalog_ids:
        issue("catalog_candidate_id_duplicate", str(duplicate_catalog_ids))
    catalog_by_id = {
        str(value.get("candidate_id", "")): value for value in catalog_candidates
    }

    if resolution.get("catalog_id") != catalog.get("catalog_id"):
        issue("resolution_catalog_id_mismatch", "resolution/catalog catalog_id differs")
    if resolution.get("catalog_sha256") != catalog_sha256:
        issue("resolution_catalog_sha256_mismatch", "resolution catalog hash differs")
    resolved = [dict(value) for value in resolution.get("candidates", [])]
    resolved_ids = [str(value.get("candidate_id", "")) for value in resolved]
    if set(resolved_ids) != set(catalog_ids) or len(resolved_ids) != len(catalog_ids):
        issue("resolution_candidate_set_mismatch", "resolved candidate set differs")
    if resolution.get("resolved_count") != len(resolved):
        issue("resolution_count_mismatch", "resolved_count does not match records")

    cids: list[int] = []
    inchikeys: list[str] = []
    identity_rows: list[dict[str, Any]] = []
    for candidate in resolved:
        candidate_id = str(candidate.get("candidate_id", ""))
        pubchem = candidate.get("pubchem", {})
        if not isinstance(pubchem, dict):
            issue("pubchem_record_invalid", candidate_id)
            continue
        try:
            cid = int(pubchem.get("cid"))
        except (TypeError, ValueError):
            issue("pubchem_cid_invalid", candidate_id)
            continue
        inchikey = str(pubchem.get("inchikey", ""))
        response_sha256 = str(pubchem.get("response_sha256", ""))
        if not INCHIKEY_RE.fullmatch(inchikey):
            issue("pubchem_inchikey_invalid", candidate_id)
        if not SHA256_RE.fullmatch(response_sha256):
            issue("pubchem_response_sha256_invalid", candidate_id)
        if not str(pubchem.get("query_url", "")).startswith(
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/"
        ):
            issue("pubchem_query_url_invalid", candidate_id)
        expected = catalog_by_id.get(candidate_id, {}).get("expected_pubchem_cid")
        if expected is not None and cid != int(expected):
            issue(
                "pubchem_expected_cid_mismatch",
                f"{candidate_id}: expected {expected}, got {cid}",
            )
        if pubchem.get("identity_status") != "resolved_requires_curator_review":
            issue("pubchem_identity_status_invalid", candidate_id)
        if cache_dir is not None:
            raw_path = cache_dir / (
                f"{candidate_id.replace(':', '_')}.response.json"
            )
            if not raw_path.is_file():
                issue("pubchem_raw_cache_missing", str(raw_path))
            elif _sha256_file(raw_path) != response_sha256:
                issue("pubchem_raw_cache_sha256_mismatch", candidate_id)
        cids.append(cid)
        inchikeys.append(inchikey)
        identity_rows.append(
            {
                "candidate_id": candidate_id,
                "cid": cid,
                "inchikey": inchikey,
                "response_sha256": response_sha256,
            }
        )
    if len(cids) != len(set(cids)):
        issue("pubchem_cid_duplicate", "candidate identities contain duplicate CIDs")
    if len(inchikeys) != len(set(inchikeys)):
        issue(
            "pubchem_inchikey_duplicate",
            "candidate identities contain duplicate InChIKeys",
        )
    expected_identity_fingerprint = _sha256_json(
        sorted(identity_rows, key=lambda value: value["candidate_id"])
    )
    if resolution.get("identity_fingerprint") != expected_identity_fingerprint:
        issue("identity_fingerprint_mismatch", "resolved identity fingerprint differs")

    if summary.get("catalog_id") != catalog.get("catalog_id"):
        issue("coverage_catalog_id_mismatch", "coverage/catalog catalog_id differs")
    if summary.get("catalog_sha256") != catalog_sha256:
        issue("coverage_catalog_sha256_mismatch", "coverage catalog hash differs")
    if summary.get("candidate_count") != len(catalog_ids):
        issue("coverage_candidate_count_mismatch", "candidate_count differs")
    database_sha256 = _sha256_file(database_path)
    if summary.get("database_sha256") != database_sha256:
        issue("coverage_database_sha256_mismatch", "database hash differs")
    if summary.get("source_database_unchanged") is not True:
        issue("coverage_database_stability_missing", "scan did not prove DB stability")
    loci_sha256 = _sha256_file(loci_path)
    if summary.get("loci_sha256") != loci_sha256:
        issue("coverage_loci_sha256_mismatch", "loci file hash differs")

    aggregate: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "chunk_count": 0,
            "document_ids": set(),
            "burn_chunk_count": 0,
            "burn_document_ids": set(),
            "wound_chunk_count": 0,
            "wound_document_ids": set(),
        }
    )
    seen_loci: set[str] = set()
    malformed_topic_tag_documents: set[str] = set()
    locus_count = 0
    with loci_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                issue("locus_blank_line", str(line_number))
                continue
            try:
                locus = json.loads(line)
            except json.JSONDecodeError as exc:
                issue("locus_json_invalid", f"line {line_number}: {exc}")
                continue
            locus_count += 1
            candidate_id = str(locus.get("candidate_id", ""))
            doc_id = str(locus.get("doc_id", ""))
            chunk_id = str(locus.get("chunk_id", ""))
            locus_id = str(locus.get("locus_id", ""))
            if candidate_id not in catalog_by_id:
                issue("locus_candidate_unknown", f"line {line_number}: {candidate_id}")
            if locus_id != f"locus:{candidate_id}:{chunk_id}":
                issue("locus_id_invalid", f"line {line_number}: {locus_id}")
            if locus_id in seen_loci:
                issue("locus_id_duplicate", locus_id)
            seen_loci.add(locus_id)
            if not doc_id or not chunk_id:
                issue("locus_source_id_missing", f"line {line_number}")
            if locus.get("review_status") != "pending_full_text_review":
                issue("locus_review_status_invalid", locus_id)
            if (
                locus.get("evidence_status")
                != "retrieval_candidate_not_scientific_evidence"
            ):
                issue("locus_evidence_status_invalid", locus_id)
            context_class = locus.get("context_class")
            if context_class not in CONTEXT_CLASSES:
                issue("locus_context_class_invalid", locus_id)
            try:
                if int(locus.get("pdf_page")) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                issue("locus_pdf_page_invalid", locus_id)
            for field in ("source_sha256", "chunk_text_sha256"):
                if not SHA256_RE.fullmatch(str(locus.get(field, ""))):
                    issue(f"locus_{field}_invalid", locus_id)
            if not locus.get("matched_terms"):
                issue("locus_matched_terms_empty", locus_id)
            topic_status = locus.get("document_topic_tags_status")
            if topic_status not in {"valid", "malformed"}:
                issue("locus_topic_tags_status_invalid", locus_id)
            if topic_status == "malformed":
                malformed_topic_tag_documents.add(doc_id)

            stat = aggregate[candidate_id]
            stat["chunk_count"] += 1
            stat["document_ids"].add(doc_id)
            if context_class == "burn_context":
                stat["burn_chunk_count"] += 1
                stat["burn_document_ids"].add(doc_id)
            if context_class in {"burn_context", "wound_context"}:
                stat["wound_chunk_count"] += 1
                stat["wound_document_ids"].add(doc_id)

    if summary.get("locus_count") != locus_count:
        issue("coverage_locus_count_mismatch", "locus_count differs")
    summary_candidates = {
        str(value.get("candidate_id", "")): value
        for value in summary.get("candidates", [])
    }
    if set(summary_candidates) != set(catalog_ids):
        issue("coverage_candidate_set_mismatch", "summary candidate set differs")
    for candidate_id in catalog_ids:
        expected = summary_candidates.get(candidate_id, {})
        stat = aggregate[candidate_id]
        comparisons = {
            "chunk_count": stat["chunk_count"],
            "document_count": len(stat["document_ids"]),
            "burn_chunk_count": stat["burn_chunk_count"],
            "burn_document_count": len(stat["burn_document_ids"]),
            "wound_or_burn_chunk_count": stat["wound_chunk_count"],
            "wound_or_burn_document_count": len(stat["wound_document_ids"]),
        }
        for field, actual in comparisons.items():
            if expected.get(field) != actual:
                issue(
                    "coverage_aggregate_mismatch",
                    f"{candidate_id}.{field}: summary={expected.get(field)}, loci={actual}",
                )
    data_quality = summary.get("data_quality", {})
    if sorted(data_quality.get("malformed_topic_tags_document_ids", [])) != sorted(
        malformed_topic_tag_documents
    ):
        issue("coverage_topic_tags_mismatch", "malformed topic-tag IDs differ")

    return {
        "schema_version": 1,
        "valid": not issues,
        "issues": issues,
        "computational_intake_complete": not issues,
        "scientific_release_ready": False,
        "scientific_release_blockers": [
            "chemical_identity_curator_review",
            "full_text_and_study_grade_review",
            "C0_C5_gate_and_score_review",
            "mechanism_evidence_review",
            "experimental_validation",
        ],
        "counts": {
            "catalog_candidates": len(catalog_ids),
            "resolved_candidates": len(resolved),
            "retrieval_loci": locus_count,
            "malformed_topic_tags_documents": len(
                malformed_topic_tag_documents
            ),
        },
        "fingerprints": {
            "catalog_sha256": catalog_sha256,
            "resolution_sha256": _sha256_file(resolution_path),
            "identity_fingerprint": expected_identity_fingerprint,
            "database_sha256": database_sha256,
            "coverage_summary_sha256": _sha256_file(coverage_summary_path),
            "loci_sha256": loci_sha256,
        },
    }
