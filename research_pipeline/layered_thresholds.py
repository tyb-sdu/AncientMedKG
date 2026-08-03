from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_LAYERS = {
    "Q1_source_quality",
    "Q2_domain_relevance",
    "Q3_evidence_release",
    "Q4_mechanism_priority",
    "Q5_release_integrity",
}
REQUIRED_GATES = {
    "ancient_ocr_page_quality",
    "kanripo_page_quality",
    "ancient_direct_semantic",
    "ancient_transfer_semantic",
    "ancient_candidate_release",
    "modern_locus_release",
    "modern_structured_release",
    "compound_priority_tier_1",
    "compound_priority_tier_2",
    "ppi_primary_mechanism",
    "pathway_enrichment",
    "final_release_integrity",
}
SUPPORTED_OPERATORS = {">", ">=", "<", "<=", "hard_gate"}


class LayeredThresholdError(ValueError):
    pass


def load_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayeredThresholdError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LayeredThresholdError("threshold policy must be a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _gate_map(policy: dict[str, Any], issues: list[str]) -> dict[str, dict[str, Any]]:
    gates = policy.get("gates")
    if not isinstance(gates, list) or not gates:
        issues.append("gates_missing")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            issues.append(f"gates[{index}].not_an_object")
            continue
        gate_id = str(gate.get("gate_id", "")).strip()
        if not gate_id:
            issues.append(f"gates[{index}].gate_id_missing")
            continue
        if gate_id in result:
            issues.append(f"duplicate_gate_id:{gate_id}")
            continue
        result[gate_id] = gate
    return result


def _number(value: Any, name: str, issues: list[str]) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        issues.append(f"{name}.not_numeric")
        return 0.0
    numeric = float(value)
    if not 0.0 <= numeric <= 1.0:
        issues.append(f"{name}.outside_unit_interval")
    return numeric


def validate_policy(path: Path) -> dict[str, Any]:
    policy = load_policy(path)
    issues: list[str] = []
    if policy.get("decision_mode") != "fully_automatic":
        issues.append("decision_mode_must_be_fully_automatic")
    if policy.get("human_reviewed") is not False:
        issues.append("human_reviewed_must_be_false")

    gates = _gate_map(policy, issues)
    missing_gates = sorted(REQUIRED_GATES - set(gates))
    if missing_gates:
        issues.append("missing_gates:" + ",".join(missing_gates))
    extra_layers = {
        str(gate.get("layer", "")) for gate in gates.values()
    } - REQUIRED_LAYERS
    if extra_layers:
        issues.append("unsupported_layers:" + ",".join(sorted(extra_layers)))
    missing_layers = REQUIRED_LAYERS - {
        str(gate.get("layer", "")) for gate in gates.values()
    }
    if missing_layers:
        issues.append("missing_layers:" + ",".join(sorted(missing_layers)))

    thresholds: dict[str, float] = {}
    layer_counts: Counter[str] = Counter()
    for gate_id, gate in gates.items():
        operator = str(gate.get("operator", ""))
        if operator not in SUPPORTED_OPERATORS:
            issues.append(f"{gate_id}.unsupported_operator:{operator}")
        layer_counts[str(gate.get("layer", ""))] += 1
        hard_requirements = gate.get("hard_requirements")
        if not isinstance(hard_requirements, dict) or not hard_requirements:
            issues.append(f"{gate_id}.hard_requirements_missing")
        if not str(gate.get("pass_action", "")).strip():
            issues.append(f"{gate_id}.pass_action_missing")
        if not str(gate.get("fail_action", "")).strip():
            issues.append(f"{gate_id}.fail_action_missing")
        if operator == "hard_gate":
            if gate.get("threshold") is not None:
                issues.append(f"{gate_id}.hard_gate_threshold_must_be_null")
        else:
            thresholds[gate_id] = _number(
                gate.get("threshold"), f"{gate_id}.threshold", issues
            )

    direct = thresholds.get("ancient_direct_semantic", 0.0)
    transfer = thresholds.get("ancient_transfer_semantic", 0.0)
    tier_1 = thresholds.get("compound_priority_tier_1", 0.0)
    tier_2 = thresholds.get("compound_priority_tier_2", 0.0)
    if direct <= transfer:
        issues.append("direct_semantic_threshold_must_exceed_transfer_threshold")
    if tier_1 <= tier_2:
        issues.append("compound_tier_1_threshold_must_exceed_tier_2_threshold")
    transfer_layers = (
        gates.get("ancient_transfer_semantic", {})
        .get("hard_requirements", {})
        .get("minimum_semantic_layers")
    )
    if transfer_layers != 2:
        issues.append("transfer_channel_requires_two_semantic_layers")

    baseline = policy.get("observed_baseline")
    if not isinstance(baseline, dict):
        issues.append("observed_baseline_missing")
        baseline = {}
    ancient = baseline.get("ancient", {}) if isinstance(baseline, dict) else {}
    modern = baseline.get("modern", {}) if isinstance(baseline, dict) else {}
    try:
        ancient_candidate = ancient["candidate_graph"]
        ancient_released = ancient["released_graph"]
        ancient_discarded = ancient["discarded_at_release"]
        for measure in ("entities", "evidence", "relations"):
            if int(ancient_candidate[measure]) != (
                int(ancient_released[measure]) + int(ancient_discarded[measure])
            ):
                issues.append(f"ancient_count_not_conserved:{measure}")
        ancient_corpus = ancient["corpus"]
        if int(ancient_corpus["kanripo_candidate_page_anchors"]) != (
            int(ancient_corpus["kanripo_admitted_pages"])
            + int(ancient_corpus["kanripo_discarded_page_anchors"])
        ):
            issues.append("kanripo_page_anchor_count_not_conserved")
        if int(ancient_released["source_verified_evidence"]) != int(
            ancient_released["evidence"]
        ):
            issues.append("ancient_released_evidence_not_fully_source_verified")
        if int(ancient_released["automatic_treats_relations"]) != 0:
            issues.append("automatic_treats_relations_must_be_zero")

        modern_candidates = modern["domain_candidates"]
        modern_structured = modern["structured_release"]
        modern_released = modern["released_graph"]
        if int(modern_candidates["structuring_candidates"]) != (
            int(modern_structured["approved"])
            + int(modern_structured["discarded"])
        ):
            issues.append("modern_structured_count_not_conserved")
        if int(modern_released["evidence"]) != int(modern_structured["approved"]):
            issues.append("modern_graph_evidence_not_equal_to_approved_structured")
        if int(modern_released["source_verified_evidence"]) != int(
            modern_released["evidence"]
        ):
            issues.append("modern_released_evidence_not_fully_source_verified")
    except (KeyError, TypeError, ValueError):
        issues.append("observed_baseline_structure_invalid")

    threshold_values = sorted(set(thresholds.values()), reverse=True)
    if len(threshold_values) < 5:
        issues.append("numeric_thresholds_not_sufficiently_stratified")

    return {
        "valid": not issues,
        "path": str(path),
        "sha256": _sha256_file(path),
        "policy_id": policy.get("policy_id"),
        "version": policy.get("version"),
        "decision_mode": policy.get("decision_mode"),
        "human_reviewed": policy.get("human_reviewed"),
        "gate_count": len(gates),
        "layer_counts": dict(sorted(layer_counts.items())),
        "numeric_thresholds": threshold_values,
        "observed_baseline": baseline,
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Validate the stage-specific screening threshold policy"
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=root / "data" / "layered_thresholds_v1.json",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_policy(args.policy)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8", newline="\n")
    print(payload, end="")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
