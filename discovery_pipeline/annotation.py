from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable


ANNOTATION_FIELDS = (
    "full_text_checked",
    "source_page_verified",
    "relevance_label",
    "study_type",
    "evidence_direction",
    "supports_c1_source",
    "supports_c2_exposure",
    "supports_c3_burn_wound",
    "supports_c4_target_pathway",
    "supports_c5_safety",
    "confidence_1_to_5",
)
FIXED_FIELDS = (
    "locus_id",
    "candidate_id",
    "canonical_name",
    "context_class",
    "doc_id",
    "title",
    "year",
    "doi",
    "source_filename",
    "source_sha256",
    "pdf_page",
    "chunk_id",
    "chunk_text_sha256",
    "matched_terms",
    "context_terms",
    "snippet",
)
REVIEW_FIELDS = (
    "assignment_id",
    "reviewer_slot",
    *FIXED_FIELDS,
    "reviewer_id",
    "reviewed_at",
    *ANNOTATION_FIELDS,
    "notes",
)
RELEVANCE_LABELS = {
    "direct_burn",
    "direct_wound",
    "mechanistic_support",
    "formula_exposure",
    "safety",
    "background_only",
    "irrelevant",
    "uncertain",
}
STUDY_TYPES = {
    "randomized_trial",
    "controlled_clinical",
    "observational_clinical",
    "animal",
    "in_vitro",
    "analytical_chemistry",
    "systematic_review",
    "narrative_review",
    "computational",
    "other",
    "uncertain",
}
EVIDENCE_DIRECTIONS = {
    "supportive",
    "null",
    "adverse",
    "mixed",
    "not_applicable",
    "uncertain",
}
YES_NO = {"yes", "no"}
GATE_VOTES = {"yes", "no", "uncertain"}
ADJUDICATION_DECISIONS = {"approve", "reject", "needs_more_information"}
CONTEXT_TARGETS = {
    "burn_context": 0.35,
    "wound_context": 0.45,
    "compound_only": 0.20,
}


class AnnotationError(ValueError):
    pass


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _stable_key(seed: str, *parts: object) -> str:
    return _sha256_bytes(
        "\x1f".join([seed, *(str(value) for value in parts)]).encode("utf-8")
    )


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AnnotationError(f"JSON root must be an object: {path}")
    return value


def _atomic_text(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding=encoding, newline="\n")
    os.replace(temporary, path)


def _atomic_csv(path: Path, rows: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _require_new_output(output_dir: Path, filenames: Iterable[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = [output_dir / name for name in filenames if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite {existing[0]}")


def _read_loci(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise AnnotationError(f"blank locus line: {line_number}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise AnnotationError(f"locus line {line_number} is not an object")
            locus_id = str(value.get("locus_id", ""))
            if not locus_id or locus_id in seen:
                raise AnnotationError(f"missing or duplicate locus_id: {locus_id!r}")
            seen.add(locus_id)
            if value.get("review_status") != "pending_full_text_review":
                raise AnnotationError(f"locus is not pending review: {locus_id}")
            if (
                value.get("evidence_status")
                != "retrieval_candidate_not_scientific_evidence"
            ):
                raise AnnotationError(f"locus has invalid evidence status: {locus_id}")
            if value.get("context_class") not in CONTEXT_TARGETS:
                raise AnnotationError(f"locus has invalid context class: {locus_id}")
            records.append(value)
    return records


def _allocate_quotas(capacities: dict[str, int], batch_size: int) -> dict[str, int]:
    if batch_size <= 0:
        raise AnnotationError("batch_size must be positive")
    if batch_size > sum(capacities.values()):
        raise AnnotationError("batch_size exceeds available loci")
    active = sorted(key for key, capacity in capacities.items() if capacity > 0)
    if batch_size < len(active):
        raise AnnotationError("batch_size must be at least the represented candidate count")
    quotas = {key: 0 for key in active}
    allocated = 0
    while allocated < batch_size:
        progressed = False
        for candidate_id in active:
            if quotas[candidate_id] >= capacities[candidate_id]:
                continue
            quotas[candidate_id] += 1
            allocated += 1
            progressed = True
            if allocated == batch_size:
                break
        if not progressed:
            raise AnnotationError("could not allocate the requested batch")
    return quotas


def _diverse_order(records: list[dict[str, Any]], seed: str) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda value: _stable_key(seed, value["locus_id"]))
    first_per_document: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    seen_documents: set[str] = set()
    for record in ordered:
        doc_id = str(record.get("doc_id", ""))
        if doc_id not in seen_documents:
            seen_documents.add(doc_id)
            first_per_document.append(record)
        else:
            remaining.append(record)
    return first_per_document + remaining


def _select_stratified_records(
    grouped: dict[str, list[dict[str, Any]]],
    quotas: dict[str, int],
    seed: str,
) -> list[dict[str, Any]]:
    candidates = sorted(quotas)
    pools = {
        (candidate_id, context): _diverse_order(
            [
                value
                for value in grouped[candidate_id]
                if value["context_class"] == context
            ],
            f"{seed}:{candidate_id}:{context}",
        )
        for candidate_id in candidates
        for context in CONTEXT_TARGETS
    }
    offsets = {key: 0 for key in pools}
    remaining = dict(quotas)
    selected: list[dict[str, Any]] = []
    selected_documents: set[str] = set()

    def peek(candidate_id: str, context: str) -> tuple[int, dict[str, Any]] | None:
        key = (candidate_id, context)
        start = offsets[key]
        if start >= len(pools[key]):
            return None
        for index in range(start, len(pools[key])):
            record = pools[key][index]
            if str(record.get("doc_id", "")) not in selected_documents:
                return index, record
        return start, pools[key][start]

    def consume(
        candidate_id: str,
        context: str,
        choice: tuple[int, dict[str, Any]],
    ) -> None:
        key = (candidate_id, context)
        index, record = choice
        start = offsets[key]
        pools[key][start], pools[key][index] = pools[key][index], pools[key][start]
        offsets[key] += 1
        remaining[candidate_id] -= 1
        selected.append(record)
        selected_documents.add(str(record.get("doc_id", "")))

    def take(context: str, target: int) -> int:
        taken = 0
        while taken < target:
            progressed = False
            for candidate_id in candidates:
                if remaining[candidate_id] <= 0:
                    continue
                choice = peek(candidate_id, context)
                if choice is None:
                    continue
                consume(candidate_id, context, choice)
                taken += 1
                progressed = True
                if taken == target:
                    break
            if not progressed:
                break
        return taken

    batch_size = sum(quotas.values())
    ideal_burn = math.floor(batch_size * CONTEXT_TARGETS["burn_context"])
    burn_selected = take("burn_context", ideal_burn)
    after_burn = batch_size - burn_selected
    residual_weight = (
        CONTEXT_TARGETS["wound_context"] + CONTEXT_TARGETS["compound_only"]
    )
    adaptive_wound = round(
        after_burn * CONTEXT_TARGETS["wound_context"] / residual_weight
    )
    wound_selected = take("wound_context", adaptive_wound)
    take("compound_only", batch_size - burn_selected - wound_selected)

    # Candidate capacity can make the global context targets infeasible. Fill
    # each remaining quota from its own unselected pools without dropping a row.
    for candidate_id in candidates:
        while remaining[candidate_id] > 0:
            available = []
            for context in ("burn_context", "wound_context", "compound_only"):
                choice = peek(candidate_id, context)
                if choice is not None:
                    available.append((context, choice))
            if not available:
                raise AnnotationError(
                    f"sampling did not satisfy quota for {candidate_id}"
                )
            context, choice = min(
                available,
                key=lambda value: (
                    {"burn_context": 0, "wound_context": 1, "compound_only": 2}[
                        value[0]
                    ],
                    _stable_key(seed, "fill", value[1][1]["locus_id"]),
                ),
            )
            consume(candidate_id, context, choice)
    if len(selected) != batch_size:
        raise AnnotationError("stratified sampling did not satisfy batch size")
    if len({str(value["locus_id"]) for value in selected}) != batch_size:
        raise AnnotationError("stratified sampling selected duplicate loci")
    return selected


def _csv_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "" if value is None else str(value)


def _fixed_row(record: dict[str, Any], canonical_name: str) -> dict[str, str]:
    values = {field: _csv_value(record.get(field)) for field in FIXED_FIELDS}
    values["canonical_name"] = canonical_name
    return values


def _assignment_id(batch_id: str, slot: str, locus_id: str) -> str:
    digest = _stable_key(batch_id, slot, locus_id)[:20]
    return f"assignment:{slot.lower()}:{digest}"


def prepare_annotation_batch(
    *,
    loci_path: Path,
    coverage_summary_path: Path,
    catalog_path: Path,
    output_dir: Path,
    batch_size: int = 500,
    seed: str = "rendongtang-dual-review-v1",
) -> dict[str, Any]:
    filenames = (
        "review_master.jsonl",
        "reviewer_A.csv",
        "reviewer_B.csv",
        "ANNOTATION_CODEBOOK.md",
        "ANNOTATION_GUIDE_ZH.md",
        "batch_manifest.json",
    )
    _require_new_output(output_dir, filenames)
    catalog = _load_object(catalog_path)
    summary = _load_object(coverage_summary_path)
    catalog_sha256 = _sha256_json(catalog)
    loci_sha256 = _sha256_file(loci_path)
    if summary.get("catalog_sha256") != catalog_sha256:
        raise AnnotationError("coverage summary catalog SHA-256 mismatch")
    if summary.get("loci_sha256") != loci_sha256:
        raise AnnotationError("coverage summary loci SHA-256 mismatch")
    records = _read_loci(loci_path)
    if summary.get("locus_count") != len(records):
        raise AnnotationError("coverage summary locus count mismatch")
    catalog_by_id = {
        str(value["candidate_id"]): dict(value)
        for value in catalog.get("candidates", [])
    }
    if len(catalog_by_id) != len(catalog.get("candidates", [])):
        raise AnnotationError("catalog contains duplicate candidate_id values")
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        candidate_id = str(record.get("candidate_id", ""))
        if candidate_id not in catalog_by_id:
            raise AnnotationError(f"unknown locus candidate_id: {candidate_id}")
        grouped[candidate_id].append(record)
    missing = sorted(set(catalog_by_id) - set(grouped))
    if missing:
        raise AnnotationError(f"catalog candidates have no loci: {missing}")
    quotas = _allocate_quotas(
        {candidate_id: len(values) for candidate_id, values in grouped.items()},
        batch_size,
    )
    selected = _select_stratified_records(dict(grouped), quotas, seed)
    selected.sort(key=lambda value: _stable_key(seed, "master", value["locus_id"]))
    batch_id = f"review:{_stable_key(seed, catalog_sha256, loci_sha256, batch_size)[:24]}"

    master_records = []
    for rank, record in enumerate(selected, start=1):
        candidate_id = str(record["candidate_id"])
        master_records.append(
            {
                "batch_id": batch_id,
                "selection_rank": rank,
                "selection_status": "dual_review_pending",
                "canonical_name": str(
                    catalog_by_id[candidate_id]["canonical_name"]
                ),
                **record,
            }
        )
    master_payload = "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for value in master_records
    )
    master_path = output_dir / "review_master.jsonl"
    _atomic_text(master_path, master_payload)

    reviewer_paths: dict[str, Path] = {}
    for slot in ("A", "B"):
        ordered = sorted(
            master_records,
            key=lambda value: _stable_key(seed, slot, value["locus_id"]),
        )
        rows = []
        for record in ordered:
            candidate_id = str(record["candidate_id"])
            fixed = _fixed_row(
                record,
                str(catalog_by_id[candidate_id]["canonical_name"]),
            )
            rows.append(
                {
                    "assignment_id": _assignment_id(
                        batch_id, slot, str(record["locus_id"])
                    ),
                    "reviewer_slot": slot,
                    **fixed,
                    "reviewer_id": "",
                    "reviewed_at": "",
                    **{field: "" for field in ANNOTATION_FIELDS},
                    "notes": "",
                }
            )
        path = output_dir / f"reviewer_{slot}.csv"
        _atomic_csv(path, rows, REVIEW_FIELDS)
        reviewer_paths[slot] = path

    codebook_source = Path(__file__).with_name("ANNOTATION_CODEBOOK.md")
    codebook_path = output_dir / "ANNOTATION_CODEBOOK.md"
    shutil.copyfile(codebook_source, codebook_path)
    guide_source = Path(__file__).with_name("ANNOTATION_GUIDE_ZH.md")
    guide_path = output_dir / "ANNOTATION_GUIDE_ZH.md"
    shutil.copyfile(guide_source, guide_path)
    context_distribution = Counter(value["context_class"] for value in selected)
    candidate_distribution = Counter(value["candidate_id"] for value in selected)
    document_count = len({str(value["doc_id"]) for value in selected})
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "seed": seed,
        "batch_size": batch_size,
        "selection_policy": {
            "candidate_allocation": "balanced_round_robin_with_capacity_redistribution",
            "context_targets": CONTEXT_TARGETS,
            "context_allocation": (
                "global_burn_first_then_adaptive_wound_compound_distribution"
            ),
            "document_diversity": "first_unique_document_before_repeat_within_context",
        },
        "scientific_boundary": (
            "Selection and dual agreement do not approve scientific evidence. "
            "An explicit adjudication step is required before KG promotion."
        ),
        "inputs": {
            "catalog_sha256": catalog_sha256,
            "coverage_summary_sha256": _sha256_file(coverage_summary_path),
            "loci_sha256": loci_sha256,
            "source_locus_count": len(records),
        },
        "distribution": {
            "candidate": dict(sorted(candidate_distribution.items())),
            "context": dict(sorted(context_distribution.items())),
            "unique_documents": document_count,
        },
        "files": {},
    }
    for name, path in (
        ("review_master.jsonl", master_path),
        ("reviewer_A.csv", reviewer_paths["A"]),
        ("reviewer_B.csv", reviewer_paths["B"]),
        ("ANNOTATION_CODEBOOK.md", codebook_path),
        ("ANNOTATION_GUIDE_ZH.md", guide_path),
    ):
        manifest["files"][name] = {
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
    manifest_path = output_dir / "batch_manifest.json"
    _atomic_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def validate_annotation_batch(manifest_path: Path) -> dict[str, Any]:
    manifest = _load_object(manifest_path)
    root = manifest_path.parent
    required_files = (
        "review_master.jsonl",
        "reviewer_A.csv",
        "reviewer_B.csv",
        "ANNOTATION_CODEBOOK.md",
        "ANNOTATION_GUIDE_ZH.md",
    )
    for name in required_files:
        path = root / name
        expected_sha = manifest.get("files", {}).get(name, {}).get("sha256")
        if not path.is_file() or _sha256_file(path) != expected_sha:
            raise AnnotationError(f"batch file is missing or changed: {name}")
    master = _read_loci_for_merge(root / "review_master.jsonl")
    if len(master) != manifest.get("batch_size"):
        raise AnnotationError("batch size differs from review master")
    batch_id = str(manifest.get("batch_id", ""))
    for locus_id, record in master.items():
        if record.get("batch_id") != batch_id:
            raise AnnotationError(f"master batch_id differs for {locus_id}")
        if record.get("selection_status") != "dual_review_pending":
            raise AnnotationError(f"master selection status differs for {locus_id}")
        if not str(record.get("canonical_name", "")).strip():
            raise AnnotationError(f"master canonical name is empty for {locus_id}")

    orders: dict[str, list[str]] = {}
    for slot in ("A", "B"):
        path = root / f"reviewer_{slot}.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != REVIEW_FIELDS:
                raise AnnotationError(f"review columns differ from schema: {path}")
            rows = list(reader)
        if len(rows) != len(master):
            raise AnnotationError(f"reviewer {slot} row count differs from master")
        indexed: set[str] = set()
        orders[slot] = []
        for row in rows:
            locus_id = row["locus_id"]
            if locus_id not in master or locus_id in indexed:
                raise AnnotationError(f"invalid reviewer {slot} locus: {locus_id!r}")
            indexed.add(locus_id)
            orders[slot].append(locus_id)
            if row["reviewer_slot"] != slot:
                raise AnnotationError(f"reviewer slot differs for {slot}/{locus_id}")
            if row["assignment_id"] != _assignment_id(batch_id, slot, locus_id):
                raise AnnotationError(f"assignment ID differs for {slot}/{locus_id}")
            expected_fixed = _fixed_row(
                master[locus_id], str(master[locus_id]["canonical_name"])
            )
            for field in FIXED_FIELDS:
                if row[field] != expected_fixed[field]:
                    raise AnnotationError(
                        f"fixed source field differs for {slot}/{locus_id}/{field}"
                    )
            for field in ("reviewer_id", "reviewed_at", *ANNOTATION_FIELDS, "notes"):
                if row[field] != "":
                    raise AnnotationError(
                        f"new review sheet is not blank: {slot}/{locus_id}/{field}"
                    )
        if indexed != set(master):
            raise AnnotationError(f"reviewer {slot} does not cover the master")
    if len(master) > 1 and orders["A"] == orders["B"]:
        raise AnnotationError("reviewer A and B orders are identical")

    candidate_distribution = dict(
        sorted(Counter(value["candidate_id"] for value in master.values()).items())
    )
    context_distribution = dict(
        sorted(Counter(value["context_class"] for value in master.values()).items())
    )
    unique_documents = len({str(value["doc_id"]) for value in master.values()})
    actual_distribution = {
        "candidate": candidate_distribution,
        "context": context_distribution,
        "unique_documents": unique_documents,
    }
    if actual_distribution != manifest.get("distribution"):
        raise AnnotationError("batch distribution differs from manifest")
    return {
        "valid": True,
        "batch_id": batch_id,
        "batch_size": len(master),
        "distribution": actual_distribution,
        "reviewer_orders_distinct": orders["A"] != orders["B"],
        "source_locus_count": manifest.get("inputs", {}).get("source_locus_count"),
        "files_verified": list(required_files),
    }


def _read_review_csv(path: Path, expected_slot: str) -> tuple[str, dict[str, dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != REVIEW_FIELDS:
            raise AnnotationError(f"review columns differ from schema: {path}")
        rows = list(reader)
    if not rows:
        raise AnnotationError(f"review file is empty: {path}")
    reviewer_ids = {row["reviewer_id"].strip() for row in rows}
    if "" in reviewer_ids or len(reviewer_ids) != 1:
        raise AnnotationError(f"reviewer_id must be one non-empty value in {path}")
    if {row["reviewer_slot"] for row in rows} != {expected_slot}:
        raise AnnotationError(f"reviewer_slot mismatch in {path}")
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        locus_id = row["locus_id"]
        if not locus_id or locus_id in indexed:
            raise AnnotationError(f"missing or duplicate review locus: {locus_id!r}")
        if row["full_text_checked"] not in YES_NO:
            raise AnnotationError(f"invalid full_text_checked for {locus_id}")
        if row["source_page_verified"] not in YES_NO:
            raise AnnotationError(f"invalid source_page_verified for {locus_id}")
        _validate_iso_date(row["reviewed_at"], "reviewed_at", locus_id)
        if row["relevance_label"] not in RELEVANCE_LABELS:
            raise AnnotationError(f"invalid relevance_label for {locus_id}")
        if row["study_type"] not in STUDY_TYPES:
            raise AnnotationError(f"invalid study_type for {locus_id}")
        if row["evidence_direction"] not in EVIDENCE_DIRECTIONS:
            raise AnnotationError(f"invalid evidence_direction for {locus_id}")
        for field in ANNOTATION_FIELDS[5:10]:
            if row[field] not in GATE_VOTES:
                raise AnnotationError(f"invalid {field} for {locus_id}")
        try:
            confidence = int(row["confidence_1_to_5"])
        except ValueError as exc:
            raise AnnotationError(f"invalid confidence for {locus_id}") from exc
        if not 1 <= confidence <= 5:
            raise AnnotationError(f"confidence outside 1-5 for {locus_id}")
        indexed[locus_id] = row
    return next(iter(reviewer_ids)), indexed


def _cohen_kappa(left: list[str], right: list[str]) -> dict[str, Any]:
    if len(left) != len(right) or not left:
        raise AnnotationError("kappa requires equal non-empty label arrays")
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    categories = set(left_counts) | set(right_counts)
    expected = sum(
        (left_counts[value] / len(left)) * (right_counts[value] / len(right))
        for value in categories
    )
    if math.isclose(expected, 1.0):
        kappa: float | None = None
        reason = "undefined_constant_marginals"
    else:
        kappa = (observed - expected) / (1.0 - expected)
        reason = ""
    return {
        "observed_agreement": round(observed, 6),
        "expected_agreement": round(expected, 6),
        "cohen_kappa": None if kappa is None else round(kappa, 6),
        "undefined_reason": reason,
        "categories": sorted(categories),
    }


def _adjudication_fields() -> tuple[str, ...]:
    return (
        *FIXED_FIELDS,
        "reviewer_a_id",
        "reviewer_a_reviewed_at",
        "reviewer_b_id",
        "reviewer_b_reviewed_at",
        "adjudication_reason",
        "disagreement_fields",
        *(f"a_{field}" for field in ANNOTATION_FIELDS),
        *(f"b_{field}" for field in ANNOTATION_FIELDS),
        *(f"final_{field}" for field in ANNOTATION_FIELDS),
        "adjudicator_id",
        "adjudicated_at",
        "adjudication_decision",
        "adjudication_notes",
    )


def merge_annotation_reviews(
    *,
    manifest_path: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    filenames = (
        "merged_annotations.jsonl",
        "adjudication_queue.csv",
        "agreement_report.json",
    )
    _require_new_output(output_dir, filenames)
    manifest = _load_object(manifest_path)
    batch_id = str(manifest.get("batch_id", ""))
    batch_root = manifest_path.parent
    master_path = batch_root / "review_master.jsonl"
    expected_master_hash = (
        manifest.get("files", {}).get("review_master.jsonl", {}).get("sha256")
    )
    if not master_path.is_file() or _sha256_file(master_path) != expected_master_hash:
        raise AnnotationError("review master is missing or its SHA-256 differs")
    master_records = _read_loci_for_merge(master_path)
    reviewer_a_id, reviewer_a = _read_review_csv(reviewer_a_path, "A")
    reviewer_b_id, reviewer_b = _read_review_csv(reviewer_b_path, "B")
    if reviewer_a_id == reviewer_b_id:
        raise AnnotationError("dual review requires two distinct reviewer_id values")
    expected_loci = set(master_records)
    if set(reviewer_a) != expected_loci or set(reviewer_b) != expected_loci:
        raise AnnotationError("review files do not cover exactly the master loci")

    merged_records: list[dict[str, Any]] = []
    adjudication_rows: list[dict[str, Any]] = []
    field_pairs: defaultdict[str, tuple[list[str], list[str]]] = defaultdict(
        lambda: ([], [])
    )
    strict_agreement = 0
    for locus_id in sorted(expected_loci):
        master = master_records[locus_id]
        left = reviewer_a[locus_id]
        right = reviewer_b[locus_id]
        for slot, row in (("A", left), ("B", right)):
            if row["assignment_id"] != _assignment_id(batch_id, slot, locus_id):
                raise AnnotationError(f"assignment_id mismatch for {slot}/{locus_id}")
            expected_fixed = _fixed_row(master, str(master["canonical_name"]))
            for field in FIXED_FIELDS:
                if row[field] != expected_fixed[field]:
                    raise AnnotationError(f"fixed source field changed: {slot}/{locus_id}/{field}")
        disagreements = []
        for field in ANNOTATION_FIELDS:
            field_pairs[field][0].append(left[field])
            field_pairs[field][1].append(right[field])
            if left[field] != right[field]:
                disagreements.append(field)
        if not disagreements:
            strict_agreement += 1
        consensus = {
            field: left[field] if left[field] == right[field] else None
            for field in ANNOTATION_FIELDS
        }
        requires_resolution = bool(disagreements) or any(
            value is None for value in consensus.values()
        )
        merged_records.append(
            {
                "batch_id": batch_id,
                "locus_id": locus_id,
                "candidate_id": master["candidate_id"],
                "doc_id": master["doc_id"],
                "pdf_page": master["pdf_page"],
                "chunk_id": master["chunk_id"],
                "source_sha256": master["source_sha256"],
                "chunk_text_sha256": master["chunk_text_sha256"],
                "reviewer_a_id": reviewer_a_id,
                "reviewer_b_id": reviewer_b_id,
                "reviewer_a": {
                    "reviewed_at": left["reviewed_at"],
                    **{field: left[field] for field in ANNOTATION_FIELDS},
                    "notes": left["notes"],
                },
                "reviewer_b": {
                    "reviewed_at": right["reviewed_at"],
                    **{field: right[field] for field in ANNOTATION_FIELDS},
                    "notes": right["notes"],
                },
                "consensus": consensus,
                "disagreement_fields": disagreements,
                "review_status": (
                    "adjudication_required"
                    if requires_resolution
                    else "dual_agreement_unadjudicated"
                ),
                "scientific_evidence_approved": False,
            }
        )
        fixed = _fixed_row(master, str(master["canonical_name"]))
        row: dict[str, Any] = {
            **fixed,
            "reviewer_a_id": reviewer_a_id,
            "reviewer_a_reviewed_at": left["reviewed_at"],
            "reviewer_b_id": reviewer_b_id,
            "reviewer_b_reviewed_at": right["reviewed_at"],
            "adjudication_reason": (
                "field_disagreement"
                if requires_resolution
                else "dual_agreement_confirmation"
            ),
            "disagreement_fields": ";".join(disagreements),
        }
        for field in ANNOTATION_FIELDS:
            row[f"a_{field}"] = left[field]
            row[f"b_{field}"] = right[field]
            row[f"final_{field}"] = ""
        row.update(
            {
                "adjudicator_id": "",
                "adjudicated_at": "",
                "adjudication_decision": "",
                "adjudication_notes": "",
            }
        )
        adjudication_rows.append(row)

    merged_payload = "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for value in merged_records
    )
    merged_path = output_dir / "merged_annotations.jsonl"
    _atomic_text(merged_path, merged_payload)
    adjudication_path = output_dir / "adjudication_queue.csv"
    _atomic_csv(adjudication_path, adjudication_rows, _adjudication_fields())
    metrics = {
        field: _cohen_kappa(values[0], values[1])
        for field, values in sorted(field_pairs.items())
    }
    report = {
        "schema_version": 1,
        "batch_id": batch_id,
        "valid": True,
        "reviewer_a_id": reviewer_a_id,
        "reviewer_b_id": reviewer_b_id,
        "item_count": len(expected_loci),
        "strict_agreement_count": strict_agreement,
        "strict_agreement_rate": round(strict_agreement / len(expected_loci), 6),
        "adjudication_required_count": len(adjudication_rows),
        "field_agreement": metrics,
        "scientific_evidence_approved_count": 0,
        "scientific_boundary": (
            "Dual-review agreement is not final approval. Complete adjudication "
            "and source verification before KG promotion."
        ),
        "inputs": {
            "batch_manifest_sha256": _sha256_file(manifest_path),
            "reviewer_a_sha256": _sha256_file(reviewer_a_path),
            "reviewer_b_sha256": _sha256_file(reviewer_b_path),
        },
        "files": {
            "merged_annotations.jsonl": {
                "sha256": _sha256_file(merged_path),
                "bytes": merged_path.stat().st_size,
            },
            "adjudication_queue.csv": {
                "sha256": _sha256_file(adjudication_path),
                "bytes": adjudication_path.stat().st_size,
            },
        },
    }
    report_path = output_dir / "agreement_report.json"
    _atomic_text(
        report_path,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return report


def _validate_final_annotation(row: dict[str, str], locus_id: str) -> dict[str, str]:
    final = {
        field: row[f"final_{field}"].strip()
        for field in ANNOTATION_FIELDS
    }
    if final["full_text_checked"] not in YES_NO:
        raise AnnotationError(f"invalid final full_text_checked for {locus_id}")
    if final["source_page_verified"] not in YES_NO:
        raise AnnotationError(f"invalid final source_page_verified for {locus_id}")
    if final["relevance_label"] not in RELEVANCE_LABELS:
        raise AnnotationError(f"invalid final relevance_label for {locus_id}")
    if final["study_type"] not in STUDY_TYPES:
        raise AnnotationError(f"invalid final study_type for {locus_id}")
    if final["evidence_direction"] not in EVIDENCE_DIRECTIONS:
        raise AnnotationError(f"invalid final evidence_direction for {locus_id}")
    for field in ANNOTATION_FIELDS[5:10]:
        if final[field] not in GATE_VOTES:
            raise AnnotationError(f"invalid final {field} for {locus_id}")
    try:
        confidence = int(final["confidence_1_to_5"])
    except ValueError as exc:
        raise AnnotationError(f"invalid final confidence for {locus_id}") from exc
    if not 1 <= confidence <= 5:
        raise AnnotationError(f"final confidence outside 1-5 for {locus_id}")
    return final


def _validate_iso_date(value: str, field: str, locus_id: str) -> str:
    normalized = value.strip()
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as exc:
        raise AnnotationError(f"invalid {field} for {locus_id}") from exc
    if parsed.isoformat() != normalized:
        raise AnnotationError(f"invalid {field} for {locus_id}")
    return normalized


def finalize_annotation_adjudication(
    *,
    batch_manifest_path: Path,
    agreement_report_path: Path,
    adjudication_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    filenames = ("final_annotations.jsonl", "finalization_report.json")
    _require_new_output(output_dir, filenames)
    batch_manifest = _load_object(batch_manifest_path)
    agreement = _load_object(agreement_report_path)
    if agreement.get("batch_id") != batch_manifest.get("batch_id"):
        raise AnnotationError("agreement and batch IDs differ")
    if (
        agreement.get("inputs", {}).get("batch_manifest_sha256")
        != _sha256_file(batch_manifest_path)
    ):
        raise AnnotationError("batch manifest SHA-256 differs from agreement input")
    merged_path = agreement_report_path.parent / "merged_annotations.jsonl"
    expected_merged_sha = (
        agreement.get("files", {}).get("merged_annotations.jsonl", {}).get("sha256")
    )
    if not merged_path.is_file() or _sha256_file(merged_path) != expected_merged_sha:
        raise AnnotationError("merged annotations are missing or changed")
    merged = _read_loci_for_merge(merged_path)

    master_path = batch_manifest_path.parent / "review_master.jsonl"
    expected_master_sha = (
        batch_manifest.get("files", {}).get("review_master.jsonl", {}).get("sha256")
    )
    if not master_path.is_file() or _sha256_file(master_path) != expected_master_sha:
        raise AnnotationError("review master is missing or changed")
    master = _read_loci_for_merge(master_path)
    if set(master) != set(merged):
        raise AnnotationError("master and merged loci differ")

    with adjudication_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _adjudication_fields():
            raise AnnotationError("adjudication columns differ from schema")
        rows = list(reader)
    by_locus: dict[str, dict[str, str]] = {}
    for row in rows:
        locus_id = row["locus_id"]
        if not locus_id or locus_id in by_locus:
            raise AnnotationError(f"missing or duplicate adjudication locus: {locus_id!r}")
        by_locus[locus_id] = row
    if set(by_locus) != set(master):
        raise AnnotationError("adjudication does not cover exactly the batch loci")

    finalized: list[dict[str, Any]] = []
    decisions: Counter[str] = Counter()
    adjudicators: set[str] = set()
    for locus_id in sorted(master):
        source = master[locus_id]
        merged_record = merged[locus_id]
        row = by_locus[locus_id]
        expected_fixed = _fixed_row(source, str(source["canonical_name"]))
        for field in FIXED_FIELDS:
            if row[field] != expected_fixed[field]:
                raise AnnotationError(f"adjudication source field changed: {locus_id}/{field}")
        for slot, key in (("a", "reviewer_a"), ("b", "reviewer_b")):
            for field in ANNOTATION_FIELDS:
                if row[f"{slot}_{field}"] != merged_record[key][field]:
                    raise AnnotationError(
                        f"adjudication reviewer vote changed: {locus_id}/{slot}/{field}"
                    )
        if row["reviewer_a_id"] != merged_record["reviewer_a_id"]:
            raise AnnotationError(f"reviewer A identity changed for {locus_id}")
        if row["reviewer_b_id"] != merged_record["reviewer_b_id"]:
            raise AnnotationError(f"reviewer B identity changed for {locus_id}")
        if row["reviewer_a_reviewed_at"] != merged_record["reviewer_a"]["reviewed_at"]:
            raise AnnotationError(f"reviewer A date changed for {locus_id}")
        if row["reviewer_b_reviewed_at"] != merged_record["reviewer_b"]["reviewed_at"]:
            raise AnnotationError(f"reviewer B date changed for {locus_id}")
        expected_disagreements = ";".join(merged_record["disagreement_fields"])
        if row["disagreement_fields"] != expected_disagreements:
            raise AnnotationError(f"disagreement fields changed for {locus_id}")
        expected_reason = (
            "field_disagreement"
            if merged_record["disagreement_fields"]
            else "dual_agreement_confirmation"
        )
        if row["adjudication_reason"] != expected_reason:
            raise AnnotationError(f"adjudication reason changed for {locus_id}")
        adjudicator_id = row["adjudicator_id"].strip()
        if not adjudicator_id:
            raise AnnotationError(f"adjudicator_id is empty for {locus_id}")
        if adjudicator_id in {
            merged_record["reviewer_a_id"],
            merged_record["reviewer_b_id"],
        }:
            raise AnnotationError(f"adjudicator is not independent for {locus_id}")
        adjudicated_at = _validate_iso_date(
            row["adjudicated_at"], "adjudicated_at", locus_id
        )
        decision = row["adjudication_decision"].strip()
        if decision not in ADJUDICATION_DECISIONS:
            raise AnnotationError(f"invalid adjudication decision for {locus_id}")
        final = _validate_final_annotation(row, locus_id)
        if decision == "approve":
            if final["full_text_checked"] != "yes":
                raise AnnotationError(f"approved evidence lacks full-text review: {locus_id}")
            if final["source_page_verified"] != "yes":
                raise AnnotationError(f"approved evidence lacks page verification: {locus_id}")
            if final["relevance_label"] in {
                "background_only",
                "irrelevant",
                "uncertain",
            }:
                raise AnnotationError(f"approved evidence has non-evidentiary relevance: {locus_id}")
            if final["study_type"] == "uncertain":
                raise AnnotationError(f"approved evidence has uncertain study type: {locus_id}")
            if int(final["confidence_1_to_5"]) < 3:
                raise AnnotationError(f"approved evidence confidence is below 3: {locus_id}")
        decisions[decision] += 1
        adjudicators.add(adjudicator_id)
        finalized.append(
            {
                "batch_id": batch_manifest["batch_id"],
                **{field: source.get(field) for field in FIXED_FIELDS},
                "reviewer_a_id": merged_record["reviewer_a_id"],
                "reviewer_b_id": merged_record["reviewer_b_id"],
                "adjudicator_id": adjudicator_id,
                "reviewer_a_reviewed_at": merged_record["reviewer_a"]["reviewed_at"],
                "reviewer_b_reviewed_at": merged_record["reviewer_b"]["reviewed_at"],
                "adjudicated_at": adjudicated_at,
                "adjudication_decision": decision,
                "adjudication_notes": row["adjudication_notes"],
                "final_annotation": final,
                "review_status": (
                    "approved"
                    if decision == "approve"
                    else ("rejected" if decision == "reject" else "pending")
                ),
                "scientific_evidence_approved": decision == "approve",
            }
        )

    payload = "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for value in finalized
    )
    final_path = output_dir / "final_annotations.jsonl"
    _atomic_text(final_path, payload)
    report = {
        "schema_version": 1,
        "batch_id": batch_manifest["batch_id"],
        "valid": True,
        "item_count": len(finalized),
        "decision_counts": dict(sorted(decisions.items())),
        "approved_scientific_evidence_count": decisions.get("approve", 0),
        "adjudicator_ids": sorted(adjudicators),
        "inputs": {
            "batch_manifest_sha256": _sha256_file(batch_manifest_path),
            "agreement_report_sha256": _sha256_file(agreement_report_path),
            "adjudication_sha256": _sha256_file(adjudication_path),
        },
        "files": {
            "final_annotations.jsonl": {
                "sha256": _sha256_file(final_path),
                "bytes": final_path.stat().st_size,
            }
        },
    }
    report_path = output_dir / "finalization_report.json"
    _atomic_text(
        report_path,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return report


def _read_loci_for_merge(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            value = json.loads(line)
            locus_id = str(value["locus_id"])
            if locus_id in records:
                raise AnnotationError(f"duplicate master locus: {locus_id}")
            records[locus_id] = value
    if not records:
        raise AnnotationError("review master is empty")
    return records
