from __future__ import annotations

import copy
import math
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable


SCORE_DIMENSIONS = (
    "source_content",
    "formula_exposure",
    "burn_wound_evidence",
    "target_pathway_support",
    "synergy_complementarity",
    "safety_verifiability",
)
DEFAULT_WEIGHTS = {
    "source_content": 0.20,
    "formula_exposure": 0.15,
    "burn_wound_evidence": 0.25,
    "target_pathway_support": 0.20,
    "synergy_complementarity": 0.10,
    "safety_verifiability": 0.10,
}
GATE_IDS = ("C0", "C1", "C2", "C3", "C4", "C5")
GATE_STATUSES = {"pass", "pending", "fail", "not_assessed"}


class ScoringInputError(ValueError):
    pass


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    missing = set(SCORE_DIMENSIONS) - set(weights)
    extra = set(weights) - set(SCORE_DIMENSIONS)
    if missing or extra:
        raise ScoringInputError(
            f"weight dimensions mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    numeric = {key: float(weights[key]) for key in SCORE_DIMENSIONS}
    if any(not math.isfinite(value) for value in numeric.values()):
        raise ScoringInputError("weights must be finite")
    if any(value < 0 for value in numeric.values()):
        raise ScoringInputError("weights must be non-negative")
    total = sum(numeric.values())
    if total <= 0:
        raise ScoringInputError("weights must have a positive sum")
    return {key: numeric[key] / total for key in SCORE_DIMENSIONS}


def validate_candidate(candidate: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    candidate_id = str(candidate.get("candidate_id", ""))
    if not candidate_id:
        issues.append("candidate_id is required")
    gates = candidate.get("gates")
    if not isinstance(gates, dict):
        issues.append(f"{candidate_id}: gates must be an object")
    else:
        for gate_id in GATE_IDS:
            gate = gates.get(gate_id)
            if not isinstance(gate, dict):
                issues.append(f"{candidate_id}: missing gate {gate_id}")
                continue
            status = gate.get("status")
            if status not in GATE_STATUSES:
                issues.append(f"{candidate_id}: {gate_id} has invalid status {status!r}")
            if status in {"pass", "fail"} and not gate.get("evidence_ids"):
                issues.append(
                    f"{candidate_id}: {gate_id} {status} requires evidence_ids"
                )
    scores = candidate.get("scores")
    if not isinstance(scores, dict):
        issues.append(f"{candidate_id}: scores must be an object")
    else:
        for dimension in SCORE_DIMENSIONS:
            component = scores.get(dimension)
            if not isinstance(component, dict):
                issues.append(f"{candidate_id}: missing score {dimension}")
                continue
            value = component.get("value")
            if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                issues.append(
                    f"{candidate_id}: {dimension}.value must be between 0 and 1"
                )
            review_status = component.get("review_status")
            if review_status not in {"approved", "pending", "rejected"}:
                issues.append(
                    f"{candidate_id}: {dimension}.review_status is invalid"
                )
            if review_status == "approved" and not component.get("evidence_ids"):
                issues.append(
                    f"{candidate_id}: approved {dimension} requires evidence_ids"
                )
    return issues


def _weighted_score(
    candidate: dict[str, Any],
    weights: dict[str, float],
    *,
    burn_score_delta: float = 0.0,
) -> float:
    values = {
        dimension: float(candidate["scores"][dimension]["value"])
        for dimension in SCORE_DIMENSIONS
    }
    values["burn_wound_evidence"] = min(
        1.0, max(0.0, values["burn_wound_evidence"] + burn_score_delta)
    )
    return sum(values[dimension] * weights[dimension] for dimension in SCORE_DIMENSIONS)


def score_candidate(
    candidate: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    issues = validate_candidate(candidate)
    if issues:
        raise ScoringInputError("; ".join(issues))
    normalized_weights = normalize_weights(weights or DEFAULT_WEIGHTS)
    score = _weighted_score(candidate, normalized_weights)
    failed_gates = [
        gate_id
        for gate_id in GATE_IDS
        if candidate["gates"][gate_id]["status"] == "fail"
    ]
    unresolved_gates = [
        gate_id
        for gate_id in GATE_IDS
        if candidate["gates"][gate_id]["status"] in {"pending", "not_assessed"}
    ]
    rejected_scores = [
        dimension
        for dimension in SCORE_DIMENSIONS
        if candidate["scores"][dimension]["review_status"] == "rejected"
    ]
    pending_scores = [
        dimension
        for dimension in SCORE_DIMENSIONS
        if candidate["scores"][dimension]["review_status"] == "pending"
    ]
    if failed_gates or rejected_scores:
        ranking_status = "eliminated"
        tier = "eliminated"
    elif unresolved_gates or pending_scores:
        ranking_status = "provisional"
        tier = "provisional_unreleased"
    else:
        ranking_status = "reviewed"
        if score >= 0.75:
            tier = "tier_1"
        elif score >= 0.60:
            tier = "tier_2"
        else:
            tier = "reserve"
    return {
        "candidate_id": candidate["candidate_id"],
        "canonical_name": candidate.get("canonical_name", ""),
        "herb_ids": candidate.get("herb_ids", []),
        "score": round(score, 8),
        "tier": tier,
        "ranking_status": ranking_status,
        "failed_gates": failed_gates,
        "unresolved_gates": unresolved_gates,
        "rejected_scores": rejected_scores,
        "pending_scores": pending_scores,
        "weights": normalized_weights,
    }


def _weight_scenarios() -> list[tuple[str, dict[str, float], float]]:
    scenarios: list[tuple[str, dict[str, float], float]] = [
        ("baseline", normalize_weights(DEFAULT_WEIGHTS), 0.0)
    ]
    for dimension in SCORE_DIMENSIONS:
        for label, multiplier in (("minus20", 0.8), ("plus20", 1.2)):
            weights = dict(DEFAULT_WEIGHTS)
            weights[dimension] *= multiplier
            scenarios.append(
                (
                    f"weight_{dimension}_{label}",
                    normalize_weights(weights),
                    0.0,
                )
            )
    scenarios.append(
        ("burn_wound_score_minus0.10", normalize_weights(DEFAULT_WEIGHTS), -0.10)
    )
    scenarios.append(
        ("burn_wound_score_plus0.10", normalize_weights(DEFAULT_WEIGHTS), 0.10)
    )
    return scenarios


def sensitivity_analysis(candidates: Iterable[dict[str, Any]]) -> dict[str, Any]:
    candidate_list = [copy.deepcopy(value) for value in candidates]
    for candidate in candidate_list:
        issues = validate_candidate(candidate)
        if issues:
            raise ScoringInputError("; ".join(issues))
    rank_history: defaultdict[str, list[int]] = defaultdict(list)
    score_history: defaultdict[str, list[float]] = defaultdict(list)
    top_counts: Counter[str] = Counter()
    scenario_reports: list[dict[str, Any]] = []
    eligibility = {
        candidate["candidate_id"]: score_candidate(candidate)
        for candidate in candidate_list
    }
    for scenario_name, weights, burn_delta in _weight_scenarios():
        ranked = sorted(
            (
                {
                    "candidate_id": candidate["candidate_id"],
                    "score": _weighted_score(
                        candidate, weights, burn_score_delta=burn_delta
                    ),
                }
                for candidate in candidate_list
                if eligibility[candidate["candidate_id"]]["ranking_status"]
                != "eliminated"
            ),
            key=lambda value: (-value["score"], value["candidate_id"]),
        )
        for rank, item in enumerate(ranked, start=1):
            rank_history[item["candidate_id"]].append(rank)
            score_history[item["candidate_id"]].append(item["score"])
        if ranked:
            top_counts[ranked[0]["candidate_id"]] += 1
        scenario_reports.append(
            {
                "scenario": scenario_name,
                "weights": weights,
                "burn_wound_score_delta": burn_delta,
                "ranking": [
                    {
                        "candidate_id": value["candidate_id"],
                        "score": round(value["score"], 8),
                        "ranking_status": eligibility[value["candidate_id"]][
                            "ranking_status"
                        ],
                    }
                    for value in ranked
                ],
            }
        )
    scenario_count = len(scenario_reports)
    stability = []
    for candidate in candidate_list:
        candidate_id = candidate["candidate_id"]
        ranks = rank_history.get(candidate_id, [])
        scores = score_history.get(candidate_id, [])
        if not ranks:
            stability.append(
                {
                    "candidate_id": candidate_id,
                    "status": "eliminated",
                    "scenario_count": 0,
                }
            )
            continue
        stability.append(
            {
                "candidate_id": candidate_id,
                "status": eligibility[candidate_id]["ranking_status"],
                "scenario_count": len(ranks),
                "rank_min": min(ranks),
                "rank_max": max(ranks),
                "rank_mean": round(statistics.mean(ranks), 4),
                "score_min": round(min(scores), 8),
                "score_max": round(max(scores), 8),
                "top_rank_share": round(top_counts[candidate_id] / scenario_count, 6),
            }
        )
    return {
        "schema_version": 1,
        "scenario_count": scenario_count,
        "scientific_boundary": (
            "Sensitivity ranks are conditional calculations, not efficacy estimates. "
            "Candidates with pending inputs remain provisional in every scenario."
        ),
        "stability": stability,
        "scenarios": scenario_reports,
    }


def score_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = [dict(value) for value in payload.get("candidates", [])]
    if not candidates:
        raise ScoringInputError("candidates must be non-empty")
    candidate_ids = [str(value.get("candidate_id", "")) for value in candidates]
    duplicates = sorted(
        candidate_id
        for candidate_id, count in Counter(candidate_ids).items()
        if candidate_id and count > 1
    )
    if duplicates:
        raise ScoringInputError(f"duplicate candidate_id values: {duplicates}")
    weights = normalize_weights(
        {key: float(value) for key, value in payload.get("weights", DEFAULT_WEIGHTS).items()}
    )
    scored = [score_candidate(candidate, weights) for candidate in candidates]
    status_order = {"reviewed": 0, "provisional": 1, "eliminated": 2}
    scored.sort(
        key=lambda value: (
            status_order[value["ranking_status"]],
            -value["score"],
            value["candidate_id"],
        )
    )
    return {
        "schema_version": 1,
        "screening_id": payload.get("screening_id", ""),
        "weights": weights,
        "scientific_boundary": (
            "Only reviewed candidates can receive a final tier. Pending inputs remain "
            "provisional regardless of numerical score."
        ),
        "ranked_candidates": scored,
        "sensitivity": sensitivity_analysis(candidates),
    }
