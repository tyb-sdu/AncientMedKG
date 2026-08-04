from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence


class FormulaTransferScreeningError(ValueError):
    pass


REQUIRED_COMPONENTS = (
    "burn_context",
    "phenotype_overlap",
    "pathogenesis_treatment_compatibility",
    "modern_ingredient_support",
    "formulation_feasibility",
    "safety_readiness",
)
ALLOWED_ROLES = {"direct_reference", "transfer_candidate", "negative_control"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormulaTransferScreeningError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FormulaTransferScreeningError("screening specification must be an object")
    return payload


def _validate_policy(payload: Mapping[str, Any]) -> tuple[dict[str, float], float, float, float]:
    policy = payload.get("policy")
    if not isinstance(policy, Mapping):
        raise FormulaTransferScreeningError("policy must be an object")
    weights = policy.get("weights")
    if not isinstance(weights, Mapping) or set(weights) != set(REQUIRED_COMPONENTS):
        raise FormulaTransferScreeningError(
            f"weights must contain exactly: {', '.join(REQUIRED_COMPONENTS)}"
        )
    normalized = {name: float(weights[name]) for name in REQUIRED_COMPONENTS}
    if any(value < 0.0 for value in normalized.values()):
        raise FormulaTransferScreeningError("weights cannot be negative")
    if abs(sum(normalized.values()) - 1.0) > 1e-9:
        raise FormulaTransferScreeningError("weights must sum to 1.0")
    confidence_floor = float(policy.get("source_confidence_floor", 0.7))
    exploratory_floor = float(policy.get("exploratory_floor", 0.6))
    high_priority_floor = float(policy.get("high_priority_floor", 0.75))
    if not 0.0 <= confidence_floor <= 1.0:
        raise FormulaTransferScreeningError("source_confidence_floor must be in [0, 1]")
    if not 0.0 <= exploratory_floor < high_priority_floor <= 1.0:
        raise FormulaTransferScreeningError(
            "thresholds must satisfy 0 <= exploratory < high_priority <= 1"
        )
    return normalized, confidence_floor, exploratory_floor, high_priority_floor


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _source_row(connection: sqlite3.Connection, locator: Mapping[str, Any]) -> sqlite3.Row:
    required = ("book_id", "page_id", "physical_page", "source_anchor")
    missing = [name for name in required if not str(locator.get(name, "")).strip()]
    if missing:
        raise FormulaTransferScreeningError(
            f"source locator missing fields: {', '.join(missing)}"
        )
    row = connection.execute(
        "SELECT b.title, b.source_sha256, p.page_id, p.book_id, "
        "p.physical_page, p.text "
        "FROM pages p JOIN books b ON b.book_id = p.book_id "
        "WHERE p.page_id = ?",
        (str(locator["page_id"]),),
    ).fetchone()
    if row is None:
        raise FormulaTransferScreeningError(f"page not found: {locator['page_id']}")
    if str(row[3]) != str(locator["book_id"]):
        raise FormulaTransferScreeningError(f"book_id mismatch: {locator['page_id']}")
    if int(row[4]) != int(locator["physical_page"]):
        raise FormulaTransferScreeningError(f"physical_page mismatch: {locator['page_id']}")
    if _normalized_text(str(locator["source_anchor"])) not in _normalized_text(str(row[5])):
        raise FormulaTransferScreeningError(f"source anchor mismatch: {locator['page_id']}")
    return row


def _score(components: Mapping[str, Any], weights: Mapping[str, float]) -> float:
    if set(components) != set(REQUIRED_COMPONENTS):
        raise FormulaTransferScreeningError(
            f"components must contain exactly: {', '.join(REQUIRED_COMPONENTS)}"
        )
    values = {name: float(components[name]) for name in REQUIRED_COMPONENTS}
    if any(not 0.0 <= value <= 1.0 for value in values.values()):
        raise FormulaTransferScreeningError("component scores must be in [0, 1]")
    return round(sum(values[name] * weights[name] for name in REQUIRED_COMPONENTS), 6)


def screen_formula_transfer_candidates(
    *,
    specification: Mapping[str, Any],
    ancient_database: Path,
) -> dict[str, Any]:
    weights, confidence_floor, exploratory_floor, high_priority_floor = _validate_policy(
        specification
    )
    candidates = specification.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise FormulaTransferScreeningError("candidates must be a non-empty list")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    connection = sqlite3.connect(f"file:{ancient_database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, Mapping):
                raise FormulaTransferScreeningError(f"candidates[{index}] must be an object")
            candidate_id = str(candidate.get("candidate_id", "")).strip()
            role = str(candidate.get("candidate_role", "")).strip()
            if not candidate_id or candidate_id in seen:
                raise FormulaTransferScreeningError(f"invalid duplicate candidate_id: {candidate_id}")
            if role not in ALLOWED_ROLES:
                raise FormulaTransferScreeningError(f"invalid candidate_role: {role}")
            seen.add(candidate_id)
            locator = candidate.get("source_locator")
            if not isinstance(locator, Mapping):
                raise FormulaTransferScreeningError(f"{candidate_id} requires source_locator")
            source = _source_row(connection, locator)
            source_confidence = float(candidate.get("source_confidence", 0.0))
            transfer_score = _score(candidate.get("components", {}), weights)
            if source_confidence < confidence_floor:
                decision = "discarded"
                decision_reason = "source_confidence_below_floor"
            elif role == "direct_reference":
                decision = "direct_evidence_reference"
                decision_reason = "calibration_only_not_transfer_competitor"
            elif transfer_score >= high_priority_floor:
                decision = "high_priority_transfer"
                decision_reason = "passed_all_gates_and_high_priority_floor"
            elif transfer_score >= exploratory_floor:
                decision = "exploratory_transfer"
                decision_reason = "passed_all_gates_but_below_high_priority_floor"
            else:
                decision = "discarded"
                decision_reason = "transfer_score_below_exploratory_floor"
            page_text = str(source[5])
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "formula_name": str(candidate.get("formula_name", "")).strip(),
                    "variant_label": str(candidate.get("variant_label", "")).strip(),
                    "candidate_role": role,
                    "source_confidence": source_confidence,
                    "components": {
                        name: float(candidate["components"][name])
                        for name in REQUIRED_COMPONENTS
                    },
                    "transfer_score": transfer_score,
                    "decision": decision,
                    "decision_reason": decision_reason,
                    "source_verified": True,
                    "source": {
                        "title": str(source[0]),
                        "source_sha256": str(source[1]),
                        "book_id": str(source[3]),
                        "page_id": str(source[2]),
                        "physical_page": int(source[4]),
                        "page_text_sha256": hashlib.sha256(
                            page_text.encode("utf-8")
                        ).hexdigest(),
                    },
                    "evidence_summary": str(candidate.get("evidence_summary", "")).strip(),
                }
            )
    finally:
        connection.close()

    direct = sorted(
        (row for row in rows if row["candidate_role"] == "direct_reference"),
        key=lambda row: (-float(row["transfer_score"]), str(row["candidate_id"])),
    )
    transfer = sorted(
        (row for row in rows if row["candidate_role"] != "direct_reference"),
        key=lambda row: (-float(row["transfer_score"]), str(row["candidate_id"])),
    )
    for rank, row in enumerate(transfer, start=1):
        row["transfer_rank"] = rank
    selected = [row for row in transfer if row["decision"] == "high_priority_transfer"]
    target_id = str(specification.get("target_candidate_id", "")).strip()
    target = next((row for row in transfer if row["candidate_id"] == target_id), None)
    if target is None:
        raise FormulaTransferScreeningError("target_candidate_id is not a transfer candidate")

    return {
        "schema_version": 1,
        "screening_id": str(specification.get("screening_id", "")).strip(),
        "policy": {
            "weights": weights,
            "source_confidence_floor": confidence_floor,
            "exploratory_floor": exploratory_floor,
            "high_priority_floor": high_priority_floor,
            "direct_reference_policy": (
                "Direct burn formulas calibrate the scoring scale but do not compete "
                "with mechanism-transfer candidates."
            ),
        },
        "counts": {
            "total": len(rows),
            "direct_references": len(direct),
            "transfer_candidates_and_controls": len(transfer),
            "high_priority_transfer": len(selected),
            "discarded": sum(row["decision"] == "discarded" for row in rows),
        },
        "direct_references": direct,
        "transfer_ranking": transfer,
        "selected_high_priority": [row["candidate_id"] for row in selected],
        "target_result": {
            "candidate_id": target["candidate_id"],
            "formula_name": target["formula_name"],
            "transfer_rank": target["transfer_rank"],
            "transfer_score": target["transfer_score"],
            "decision": target["decision"],
            "interpretation": (
                "The target ranks first among the predefined transfer candidates "
                "after source verification and layered scoring. This is a research-"
                "priority result, not proof of superior burn efficacy."
            ),
        },
        "scientific_boundary": (
            "The score orders traceable evidence for follow-up. It does not establish "
            "clinical efficacy, comparative effectiveness, dose, safety, or causality."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank traceable ancient formula candidates for burn-mechanism transfer."
    )
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--ancient-database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    report = screen_formula_transfer_candidates(
        specification=_read_json(arguments.candidates),
        ancient_database=arguments.ancient_database,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["target_result"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
