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
ALLOWED_ROLES = {
    "direct_reference",
    "transfer_candidate",
    "negative_control",
    "safety_exclusion",
    "route_control",
}
COMPETING_ROLES = {"transfer_candidate", "negative_control"}
ALLOWED_ROUTES = {"internal", "external", "mixed", "unknown"}
ALLOWED_DOSAGE_FORMS = {
    "decoction",
    "powder",
    "ointment",
    "pill",
    "wine",
    "paste",
    "other",
    "unknown",
}
ALLOWED_COMPLEXITY = {"simple", "moderate", "complex", "unknown"}
DOCUMENTATION_PRECHECKS = (
    "formula_name_present",
    "composition_anchor_verified",
    "route_anchor_verified",
    "administration_route_resolved",
    "dosage_form_resolved",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormulaTransferScreeningError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FormulaTransferScreeningError("screening specification must be an object")
    return payload


def _validate_policy(
    payload: Mapping[str, Any],
) -> tuple[
    dict[str, float],
    float,
    float,
    float,
    dict[str, tuple[str, ...]],
    tuple[str, ...],
]:
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
    aliases = policy.get("heavy_metal_aliases")
    if not isinstance(aliases, Mapping) or not aliases:
        raise FormulaTransferScreeningError("heavy_metal_aliases must be a non-empty object")
    normalized_aliases: dict[str, tuple[str, ...]] = {}
    for family, terms in aliases.items():
        if not isinstance(terms, list) or not terms or not all(
            isinstance(term, str) and term.strip() for term in terms
        ):
            raise FormulaTransferScreeningError(
                f"heavy_metal_aliases.{family} must be a non-empty string list"
            )
        normalized_aliases[str(family)] = tuple(str(term).strip() for term in terms)
    documentation_prechecks = policy.get("documentation_prechecks")
    if not isinstance(documentation_prechecks, list) or set(
        documentation_prechecks
    ) != set(DOCUMENTATION_PRECHECKS):
        raise FormulaTransferScreeningError(
            "documentation_prechecks must contain exactly: "
            + ", ".join(DOCUMENTATION_PRECHECKS)
        )
    return (
        normalized,
        confidence_floor,
        exploratory_floor,
        high_priority_floor,
        normalized_aliases,
        DOCUMENTATION_PRECHECKS,
    )


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


def _supporting_locator(
    primary: Mapping[str, Any], evidence: Mapping[str, Any], label: str
) -> dict[str, Any]:
    anchor = str(evidence.get("source_anchor", "")).strip()
    if not anchor:
        raise FormulaTransferScreeningError(f"{label} requires source_anchor")
    return {
        "book_id": str(evidence.get("book_id", primary["book_id"])),
        "page_id": str(evidence.get("page_id", primary["page_id"])),
        "physical_page": int(evidence.get("physical_page", primary["physical_page"])),
        "source_anchor": anchor,
    }


def _score(components: Mapping[str, Any], weights: Mapping[str, float]) -> float:
    if set(components) != set(REQUIRED_COMPONENTS):
        raise FormulaTransferScreeningError(
            f"components must contain exactly: {', '.join(REQUIRED_COMPONENTS)}"
        )
    values = {name: float(components[name]) for name in REQUIRED_COMPONENTS}
    if any(not 0.0 <= value <= 1.0 for value in values.values()):
        raise FormulaTransferScreeningError("component scores must be in [0, 1]")
    return round(sum(values[name] * weights[name] for name in REQUIRED_COMPONENTS), 6)


def _hazard_hits(
    composition_anchor: str, aliases: Mapping[str, Sequence[str]]
) -> list[dict[str, str]]:
    normalized_anchor = _normalized_text(composition_anchor)
    hits: list[dict[str, str]] = []
    for family, terms in aliases.items():
        for term in terms:
            if _normalized_text(term) in normalized_anchor:
                hits.append({"family": family, "term": term})
    return hits


def screen_formula_transfer_candidates(
    *,
    specification: Mapping[str, Any],
    ancient_database: Path,
) -> dict[str, Any]:
    (
        weights,
        confidence_floor,
        exploratory_floor,
        high_priority_floor,
        heavy_metal_aliases,
        documentation_prechecks,
    ) = _validate_policy(specification)
    candidates = specification.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise FormulaTransferScreeningError("candidates must be a non-empty list")
    characteristics = specification.get("formula_characteristics")
    if not isinstance(characteristics, Mapping):
        raise FormulaTransferScreeningError("formula_characteristics must be an object")

    candidate_ids: list[str] = []
    for index, value in enumerate(candidates):
        if not isinstance(value, Mapping):
            raise FormulaTransferScreeningError(f"candidates[{index}] must be an object")
        candidate_ids.append(str(value.get("candidate_id", "")).strip())
    if set(characteristics) != set(candidate_ids):
        missing = sorted(set(candidate_ids) - set(characteristics))
        extra = sorted(set(characteristics) - set(candidate_ids))
        raise FormulaTransferScreeningError(
            f"formula_characteristics candidate mismatch; missing={missing}, extra={extra}"
        )

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

            feature = characteristics[candidate_id]
            if not isinstance(feature, Mapping):
                raise FormulaTransferScreeningError(
                    f"formula_characteristics.{candidate_id} must be an object"
                )
            route = str(feature.get("administration_route", "")).strip()
            dosage_form = str(feature.get("dosage_form", "")).strip()
            complexity = str(feature.get("preparation_complexity", "")).strip()
            if route not in ALLOWED_ROUTES:
                raise FormulaTransferScreeningError(f"invalid administration_route: {candidate_id}")
            if dosage_form not in ALLOWED_DOSAGE_FORMS:
                raise FormulaTransferScreeningError(f"invalid dosage_form: {candidate_id}")
            if complexity not in ALLOWED_COMPLEXITY:
                raise FormulaTransferScreeningError(f"invalid preparation_complexity: {candidate_id}")
            route_evidence = feature.get("route_evidence")
            composition_evidence = feature.get("composition_evidence")
            if not isinstance(route_evidence, Mapping) or not isinstance(
                composition_evidence, Mapping
            ):
                raise FormulaTransferScreeningError(
                    f"{candidate_id} requires route_evidence and composition_evidence"
                )
            route_locator = _supporting_locator(locator, route_evidence, f"{candidate_id}.route")
            composition_locator = _supporting_locator(
                locator, composition_evidence, f"{candidate_id}.composition"
            )
            _source_row(connection, route_locator)
            _source_row(connection, composition_locator)

            source_confidence = float(candidate.get("source_confidence", 0.0))
            transfer_score = _score(candidate.get("components", {}), weights)
            heavy_metal_hits = _hazard_hits(
                str(composition_locator["source_anchor"]), heavy_metal_aliases
            )
            source_gate_passed = source_confidence >= confidence_floor
            external_heavy_metal_excluded = (
                source_gate_passed and route == "external" and bool(heavy_metal_hits)
            )
            documentation_checks = {
                "formula_name_present": bool(
                    str(candidate.get("formula_name", "")).strip()
                ),
                "composition_anchor_verified": bool(
                    str(composition_locator.get("source_anchor", "")).strip()
                ),
                "route_anchor_verified": bool(
                    str(route_locator.get("source_anchor", "")).strip()
                ),
                "administration_route_resolved": route != "unknown",
                "dosage_form_resolved": dosage_form != "unknown",
            }
            documentation_precheck_passed = (
                source_gate_passed
                and not external_heavy_metal_excluded
                and all(documentation_checks[name] for name in documentation_prechecks)
            )
            internal_decoction = (
                documentation_precheck_passed
                and route == "internal"
                and dosage_form == "decoction"
            )
            eligible_transfer = internal_decoction and role in COMPETING_ROLES

            if not source_gate_passed:
                decision = "discarded"
                decision_reason = "source_confidence_below_floor"
            elif external_heavy_metal_excluded:
                decision = "excluded_external_heavy_metal"
                decision_reason = "external_formula_contains_predeclared_heavy_metal_term"
            elif not documentation_precheck_passed:
                decision = "excluded_unresolved_formula_administration"
                decision_reason = "formula_or_administration_documentation_unresolved"
            elif not internal_decoction:
                decision = "side_path_non_internal_decoction"
                decision_reason = "not_an_internal_decoction"
            elif role not in COMPETING_ROLES:
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
                    "formula_characteristics": {
                        "administration_route": route,
                        "dosage_form": dosage_form,
                        "preparation_complexity": complexity,
                        "heavy_metal_hits": heavy_metal_hits,
                        "documentation_checks": documentation_checks,
                    },
                    "stage_disposition": {
                        "source_gate": "passed" if source_gate_passed else "excluded",
                        "external_heavy_metal_gate": (
                            "excluded" if external_heavy_metal_excluded else "passed"
                        ),
                        "formula_administration_precheck": (
                            "not_reached"
                            if not source_gate_passed or external_heavy_metal_excluded
                            else "passed"
                            if documentation_precheck_passed
                            else "excluded"
                        ),
                        "internal_decoction_focus": (
                            "eligible_transfer"
                            if eligible_transfer
                            else "calibration_reference"
                            if internal_decoction
                            else "side_path"
                        ),
                    },
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
        (
            row
            for row in rows
            if row["stage_disposition"]["internal_decoction_focus"] == "eligible_transfer"
        ),
        key=lambda row: (-float(row["transfer_score"]), str(row["candidate_id"])),
    )
    for rank, row in enumerate(transfer, start=1):
        row["transfer_rank"] = rank
    selected = [row for row in transfer if row["decision"] == "high_priority_transfer"]
    target_id = str(specification.get("target_candidate_id", "")).strip()
    target = next((row for row in rows if row["candidate_id"] == target_id), None)
    if target is None or target["candidate_role"] not in COMPETING_ROLES:
        raise FormulaTransferScreeningError("target_candidate_id is not a transfer candidate")

    source_passed = sum(
        row["stage_disposition"]["source_gate"] == "passed" for row in rows
    )
    heavy_excluded = sum(
        row["decision"] == "excluded_external_heavy_metal" for row in rows
    )
    documentation_precheck_excluded = sum(
        row["decision"] == "excluded_unresolved_formula_administration"
        for row in rows
    )
    documentation_precheck_passed = (
        source_passed - heavy_excluded - documentation_precheck_excluded
    )
    internal_decoctions = sum(
        row["stage_disposition"]["internal_decoction_focus"]
        in {"eligible_transfer", "calibration_reference"}
        for row in rows
    )
    side_path = sum(
        row["decision"] == "side_path_non_internal_decoction" for row in rows
    )
    return {
        "schema_version": 3,
        "screening_id": str(specification.get("screening_id", "")).strip(),
        "policy": {
            "weights": weights,
            "source_confidence_floor": confidence_floor,
            "exploratory_floor": exploratory_floor,
            "high_priority_floor": high_priority_floor,
            "heavy_metal_aliases": heavy_metal_aliases,
            "documentation_prechecks": list(documentation_prechecks),
            "stage_order": [
                "source_gate",
                "external_heavy_metal_gate",
                "formula_administration_precheck",
                "internal_decoction_focus",
                "six_dimension_transfer_scoring",
            ],
            "direct_reference_policy": (
                "Direct burn formulas calibrate the scoring scale but do not compete "
                "with mechanism-transfer candidates."
            ),
        },
        "counts": {
            "total": len(rows),
            "source_confidence_passed": source_passed,
            "direct_references": len(direct),
            "external_heavy_metal_excluded": heavy_excluded,
            "after_external_heavy_metal_gate": source_passed - heavy_excluded,
            "formula_administration_precheck_excluded": (
                documentation_precheck_excluded
            ),
            "after_formula_administration_precheck": (
                documentation_precheck_passed
            ),
            "internal_decoctions": internal_decoctions,
            "eligible_transfer_candidates_and_controls": len(transfer),
            "high_priority_transfer": len(selected),
            "exploratory_transfer": sum(
                row["decision"] == "exploratory_transfer" for row in transfer
            ),
            "side_path_non_internal_decoction": side_path,
            "discarded_or_side_path": sum(
                row["decision"]
                in {
                    "discarded",
                    "excluded_external_heavy_metal",
                    "excluded_unresolved_formula_administration",
                    "side_path_non_internal_decoction",
                }
                for row in rows
            ),
        },
        "stages": [
            {
                "stage": "source_gate",
                "before": len(rows),
                "excluded": len(rows) - source_passed,
                "after": source_passed,
            },
            {
                "stage": "external_heavy_metal_gate",
                "before": source_passed,
                "excluded": heavy_excluded,
                "after": source_passed - heavy_excluded,
            },
            {
                "stage": "formula_administration_precheck",
                "before": source_passed - heavy_excluded,
                "excluded": documentation_precheck_excluded,
                "after": documentation_precheck_passed,
                "checks": list(documentation_prechecks),
            },
            {
                "stage": "internal_decoction_focus",
                "before": documentation_precheck_passed,
                "side_path": side_path,
                "after": internal_decoctions,
            },
            {
                "stage": "six_dimension_transfer_scoring",
                "before": len(transfer),
                "high_priority": len(selected),
                "exploratory": sum(
                    row["decision"] == "exploratory_transfer" for row in transfer
                ),
                "below_floor": sum(row["decision"] == "discarded" for row in transfer),
            },
        ],
        "all_candidates": rows,
        "direct_references": direct,
        "transfer_ranking": transfer,
        "selected_high_priority": [row["candidate_id"] for row in selected],
        "target_result": {
            "candidate_id": target["candidate_id"],
            "formula_name": target["formula_name"],
            "transfer_rank": target.get("transfer_rank"),
            "transfer_score": target["transfer_score"],
            "decision": target["decision"],
            "interpretation": (
                "The target ranks first after source verification, external heavy-metal "
                "exclusion, formula-and-administration documentation precheck, "
                "internal-decoction focusing, and predefined six-dimension scoring. "
                "This is a research-priority result, not proof of burn efficacy."
            ),
        },
        "scientific_boundary": (
            "The heavy-metal gate is a conservative screening rule for external historical "
            "preparations, not an individual toxicology conclusion. The final score orders "
            "traceable evidence for follow-up and does not establish efficacy, dose, safety, "
            "comparative effectiveness, or causality."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply staged safety and transfer screening to ancient formula candidates."
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
