from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from discovery_pipeline.corpus_scan import BURN_TERMS, WOUND_TERMS


class AutomaticLocusError(ValueError):
    pass


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalized(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def _pattern(term: str) -> re.Pattern[str]:
    normalized = _normalized(term)
    escaped = re.escape(normalized)
    if re.fullmatch(r"[a-z0-9][a-z0-9 .+()/_-]*", normalized):
        return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
    return re.compile(escaped)


def _matched(text: str, terms: Iterable[str]) -> list[str]:
    normalized = _normalized(text)
    return [str(term) for term in terms if _pattern(str(term)).search(normalized)]


def _context(text: str) -> tuple[str, list[str]]:
    burn = _matched(text, BURN_TERMS)
    if burn:
        return "burn_context", burn
    wound = _matched(text, WOUND_TERMS)
    if wound:
        return "wound_context", wound
    return "compound_only", []


def _relevance_factor(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    if score > 1.0:
        score /= 100.0
    return min(1.0, max(0.0, score))


def _confidence(
    *,
    exact_term_match: bool,
    context_class: str,
    relevance_factor: float,
    source_integrity: bool,
) -> tuple[float, dict[str, float]]:
    components = {
        "exact_compound_term": 0.4 if exact_term_match else 0.0,
        "domain_context": {
            "burn_context": 0.3,
            "wound_context": 0.22,
            "compound_only": 0.0,
        }.get(context_class, 0.0),
        "document_relevance": round(0.2 * relevance_factor, 6),
        "source_integrity": 0.05 if source_integrity else 0.0,
    }
    confidence = sum(components.values()) if source_integrity else 0.0
    return round(min(1.0, confidence), 6), components


def _read_loci(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AutomaticLocusError(
                    f"invalid JSON at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise AutomaticLocusError(
                    f"locus at {path}:{line_number} must be an object"
                )
            locus_id = str(value.get("locus_id", ""))
            if not locus_id:
                raise AutomaticLocusError(f"missing locus_id at line {line_number}")
            if locus_id in seen:
                raise AutomaticLocusError(f"duplicate locus_id: {locus_id}")
            seen.add(locus_id)
            records.append(value)
    if not records:
        raise AutomaticLocusError("loci input is empty")
    return records


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def filter_loci_automatically(
    *,
    loci_path: Path,
    database_path: Path,
    output_dir: Path,
    threshold: float = 0.7,
    policy_id: str = "automatic-modern-locus-threshold-v1",
    approved_at: str = "",
) -> dict[str, Any]:
    if not 0.0 <= threshold <= 1.0:
        raise AutomaticLocusError("threshold must be between 0 and 1")
    if not policy_id.strip():
        raise AutomaticLocusError("policy_id must be non-empty")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    temporary_dir = output_dir.with_name(f".{output_dir.name}.tmp")
    if temporary_dir.exists():
        raise FileExistsError(f"temporary output already exists: {temporary_dir}")
    records = _read_loci(loci_path)
    database_sha256_before = _sha256_file(database_path)
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    approved: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    context_counts: Counter[str] = Counter()
    try:
        for record in records:
            issues: list[str] = []
            chunk_id = str(record.get("chunk_id", ""))
            row = connection.execute(
                """
                SELECT c.chunk_id, c.doc_id, c.pdf_page, c.text,
                       d.sha256, d.relevance_score
                FROM chunks AS c
                JOIN documents AS d ON d.doc_id = c.doc_id
                WHERE c.chunk_id = ?
                """,
                (chunk_id,),
            ).fetchone()
            if row is None:
                issues.append("chunk_not_found")
                body = ""
                actual_context = "compound_only"
                actual_context_terms: list[str] = []
                exact_term_match = False
                relevance = 0.0
            else:
                body = str(row["text"] or "")
                if str(record.get("doc_id", "")) != str(row["doc_id"]):
                    issues.append("doc_id_mismatch")
                try:
                    supplied_page = int(record.get("pdf_page"))
                except (TypeError, ValueError):
                    supplied_page = -1
                if supplied_page != int(row["pdf_page"]):
                    issues.append("pdf_page_mismatch")
                supplied_source_sha = str(record.get("source_sha256", "")).lower()
                if not _SHA256.fullmatch(supplied_source_sha):
                    issues.append("source_sha256_invalid")
                elif supplied_source_sha != str(row["sha256"] or "").lower():
                    issues.append("source_sha256_mismatch")
                supplied_chunk_sha = str(record.get("chunk_text_sha256", "")).lower()
                if supplied_chunk_sha != _sha256_text(body):
                    issues.append("chunk_text_sha256_mismatch")
                matched_terms = [str(value) for value in record.get("matched_terms", [])]
                exact_term_match = bool(_matched(body, matched_terms))
                if not exact_term_match:
                    issues.append("compound_term_not_found")
                actual_context, actual_context_terms = _context(body)
                if str(record.get("context_class", "")) != actual_context:
                    issues.append("context_class_mismatch")
                relevance = _relevance_factor(row["relevance_score"])
            source_integrity = not issues
            confidence, components = _confidence(
                exact_term_match=exact_term_match,
                context_class=actual_context,
                relevance_factor=relevance,
                source_integrity=source_integrity,
            )
            context_counts[actual_context] += 1
            decision = "approved" if confidence >= threshold else "discarded"
            if decision == "discarded":
                if issues:
                    reasons = issues
                elif actual_context == "compound_only":
                    reasons = ["no_burn_or_wound_context"]
                else:
                    reasons = ["confidence_below_threshold"]
                reason_counts.update(reasons)
            else:
                reasons = []
            enriched = {
                **record,
                "context_class": actual_context,
                "context_terms": actual_context_terms,
                "candidate_confidence": confidence,
                "confidence_components": components,
                "automatic_decision": decision,
                "automatic_decision_reasons": reasons,
                "automatic_approval_policy": policy_id,
                "automatic_approval_threshold": threshold,
                "human_reviewed": False,
                "review_status": decision,
                "evidence_status": (
                    "machine_approved_retrieval_locus_not_scientific_claim"
                    if decision == "approved"
                    else "discarded_from_automatic_pipeline"
                ),
                "approved_at": approved_at if decision == "approved" else "",
                "scientific_evidence_approved": False,
            }
            (approved if decision == "approved" else discarded).append(enriched)
    finally:
        connection.close()
    database_sha256_after = _sha256_file(database_path)
    if database_sha256_after != database_sha256_before:
        raise AutomaticLocusError("source database changed during automatic filtering")

    def jsonl(values: list[dict[str, Any]]) -> str:
        return "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in values
        )

    approved_payload = jsonl(approved)
    discarded_payload = jsonl(discarded)
    report = {
        "valid": True,
        "policy_id": policy_id,
        "threshold": threshold,
        "comparison": "greater_than_or_equal",
        "approval_scope": "retrieval_locus_only",
        "human_review_required": False,
        "human_reviewed": False,
        "scientific_evidence_approved": False,
        "database_sha256_before": database_sha256_before,
        "database_sha256_after": database_sha256_after,
        "source_database_unchanged": True,
        "input_loci": len(records),
        "approved_loci": len(approved),
        "discarded_loci": len(discarded),
        "context_counts": dict(sorted(context_counts.items())),
        "discard_reason_counts": dict(sorted(reason_counts.items())),
        "approved_sha256": _sha256_text(approved_payload),
        "discarded_sha256": _sha256_text(discarded_payload),
    }
    temporary_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir.mkdir()
    try:
        _atomic_write(temporary_dir / "approved_loci.jsonl", approved_payload)
        _atomic_write(temporary_dir / "discarded_loci.jsonl", discarded_payload)
        _atomic_write(
            temporary_dir / "automatic_loci_report.json",
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        os.replace(temporary_dir, output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Source-verify modern literature loci, discard confidence below a "
            "threshold, and machine-approve the retained retrieval loci"
        )
    )
    parser.add_argument("--loci", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument(
        "--policy-id", default="automatic-modern-locus-threshold-v1"
    )
    parser.add_argument("--approved-at", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = filter_loci_automatically(
        loci_path=args.loci,
        database_path=args.database,
        output_dir=args.output_dir,
        threshold=args.threshold,
        policy_id=args.policy_id,
        approved_at=args.approved_at,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
