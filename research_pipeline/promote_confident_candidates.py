from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from knowledge_graph.ids import make_edge_id, sha256_text
from knowledge_graph.model import EvidenceRecord, GraphData, GraphEdge
from knowledge_graph.store import load_graph, write_graph, write_json
from knowledge_graph.validate import validate_graph


class AutomaticApprovalError(ValueError):
    pass


def _evidence_confidence(record: EvidenceRecord) -> float:
    raw = record.attributes.get("candidate_confidence")
    if raw is None:
        raise AutomaticApprovalError(
            f"evidence lacks candidate_confidence: {record.evidence_id}"
        )
    try:
        confidence = float(raw)
    except (TypeError, ValueError) as exc:
        raise AutomaticApprovalError(
            f"invalid candidate_confidence: {record.evidence_id}/{raw!r}"
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise AutomaticApprovalError(
            f"candidate_confidence outside 0-1: {record.evidence_id}/{confidence}"
        )
    return confidence


def promote_confident_candidates(
    *,
    input_graph_dir: Path,
    output_graph_dir: Path,
    output_report_path: Path,
    graph_version: str,
    threshold: float = 0.7,
    policy_id: str = "automatic-confidence-threshold-v1",
    approved_at: str = "",
) -> dict[str, Any]:
    if not 0.0 <= threshold <= 1.0:
        raise AutomaticApprovalError("threshold must be between 0 and 1")
    if not policy_id.strip():
        raise AutomaticApprovalError("policy_id must be non-empty")
    if not graph_version.strip():
        raise AutomaticApprovalError("graph_version must be non-empty")
    if output_report_path.exists():
        raise FileExistsError(f"refusing to overwrite {output_report_path}")

    source_graph = load_graph(input_graph_dir)
    source_validation = validate_graph(source_graph, release=False)
    source_errors = [
        value for value in source_validation["issues"] if value["severity"] == "error"
    ]
    if source_errors:
        raise AutomaticApprovalError(
            f"source candidate graph has {len(source_errors)} structural errors"
        )

    evidence_confidence = {
        value.evidence_id: _evidence_confidence(value)
        for value in source_graph.evidence
    }
    retained_evidence_ids = {
        evidence_id
        for evidence_id, confidence in evidence_confidence.items()
        if confidence >= threshold
    }
    approved_evidence = tuple(
        replace(
            value,
            review={
                "status": "approved",
                "reviewer": policy_id,
                "reviewed_at": approved_at,
                "human_reviewed": False,
                "approval_mode": "automatic_confidence_threshold",
                "threshold": threshold,
                "candidate_confidence": evidence_confidence[value.evidence_id],
            },
            attributes={
                **value.attributes,
                "automatic_approval": True,
                "automatic_approval_policy": policy_id,
                "automatic_approval_threshold": threshold,
                "human_reviewed": False,
            },
        )
        for value in source_graph.evidence
        if value.evidence_id in retained_evidence_ids
    )

    dropped_edge_reasons: Counter[str] = Counter()
    dropped_edge_predicates: Counter[str] = Counter()
    kept_edge_predicates: Counter[str] = Counter()
    approved_edges: list[GraphEdge] = []
    for edge in source_graph.edges:
        if edge.confidence < threshold:
            dropped_edge_reasons["edge_confidence_below_threshold"] += 1
            dropped_edge_predicates[edge.predicate] += 1
            continue
        supporting = tuple(
            sorted(
                evidence_id
                for evidence_id in edge.evidence_ids
                if evidence_id in retained_evidence_ids
            )
        )
        if not supporting:
            dropped_edge_reasons["no_retained_evidence"] += 1
            dropped_edge_predicates[edge.predicate] += 1
            continue
        attributes = {
            **edge.attributes,
            "automatic_approval": True,
            "automatic_approval_policy": policy_id,
            "automatic_approval_threshold": threshold,
            "human_reviewed": False,
        }
        approved_edges.append(
            replace(
                edge,
                edge_id=make_edge_id(
                    edge.subject_id,
                    edge.predicate,
                    edge.object_id,
                    supporting,
                    edge.assertion_mode,
                    attributes,
                ),
                evidence_ids=supporting,
                review_status="approved",
                attributes=attributes,
            )
        )
        kept_edge_predicates[edge.predicate] += 1

    retained_node_ids = {
        node_id
        for edge in approved_edges
        for node_id in (edge.subject_id, edge.object_id)
    }
    retained_source_ids = {value.source_id for value in approved_evidence}
    source_fingerprint = str(
        source_graph.metadata.get("build_content_fingerprint", "")
    )
    approved_graph = GraphData(
        schema_version=source_graph.schema_version,
        graph_version=graph_version,
        bundle_id=(
            "automatic-approved:"
            + sha256_text(
                json.dumps(
                    {
                        "source_graph_version": source_graph.graph_version,
                        "source_fingerprint": source_fingerprint,
                        "threshold": threshold,
                        "policy_id": policy_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )[:24]
        ),
        sources=tuple(
            value
            for value in source_graph.sources
            if value.source_id in retained_source_ids
        ),
        nodes=tuple(
            value for value in source_graph.nodes if value.node_id in retained_node_ids
        ),
        evidence=approved_evidence,
        edges=tuple(sorted(approved_edges, key=lambda value: value.edge_id)),
        metadata={
            "description": (
                "Automatically approved confidence-threshold derivative; "
                "not human expert review."
            ),
            "parent_version": source_graph.graph_version,
            "parent_content_fingerprint": source_fingerprint,
            "approval_policy": {
                "policy_id": policy_id,
                "mode": "automatic_confidence_threshold",
                "threshold": threshold,
                "comparison": "greater_than_or_equal",
                "human_reviewed": False,
                "approved_at": approved_at,
            },
        },
    )
    release_validation = validate_graph(approved_graph, release=True)
    if not release_validation["valid"]:
        errors = [
            value
            for value in release_validation["issues"]
            if value["severity"] == "error"
        ]
        raise AutomaticApprovalError(
            f"automatic approval graph failed release validation: {len(errors)} errors"
        )
    manifest = write_graph(
        approved_graph,
        output_graph_dir,
        validation_report=release_validation,
    )
    report = {
        "valid": True,
        "graph_version": graph_version,
        "bundle_id": approved_graph.bundle_id,
        "policy": approved_graph.metadata["approval_policy"],
        "source": {
            "graph_version": source_graph.graph_version,
            "content_fingerprint": source_fingerprint,
            "sources": len(source_graph.sources),
            "nodes": len(source_graph.nodes),
            "evidence": len(source_graph.evidence),
            "edges": len(source_graph.edges),
        },
        "approved": {
            "sources": len(approved_graph.sources),
            "nodes": len(approved_graph.nodes),
            "evidence": len(approved_graph.evidence),
            "edges": len(approved_graph.edges),
            "edge_predicates": dict(sorted(kept_edge_predicates.items())),
        },
        "discarded": {
            "evidence": len(source_graph.evidence) - len(approved_graph.evidence),
            "edges": len(source_graph.edges) - len(approved_graph.edges),
            "edge_reasons": dict(sorted(dropped_edge_reasons.items())),
            "edge_predicates": dict(sorted(dropped_edge_predicates.items())),
        },
        "release_validation_valid": True,
        "human_reviewed": False,
        "manifest_content_fingerprint": manifest["content_fingerprint"],
    }
    write_json(output_report_path, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discard evidence and edges below a confidence threshold and "
            "automatically approve the retained derivative graph"
        )
    )
    parser.add_argument("--input-graph", type=Path, required=True)
    parser.add_argument("--output-graph", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--graph-version", required=True)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument(
        "--policy-id", default="automatic-confidence-threshold-v1"
    )
    parser.add_argument("--approved-at", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = promote_confident_candidates(
        input_graph_dir=args.input_graph,
        output_graph_dir=args.output_graph,
        output_report_path=args.output_report,
        graph_version=args.graph_version,
        threshold=args.threshold,
        policy_id=args.policy_id,
        approved_at=args.approved_at,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
