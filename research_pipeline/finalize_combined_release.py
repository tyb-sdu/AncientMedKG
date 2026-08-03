from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable, TypeVar

from knowledge_graph.export_neo4j import export_neo4j
from knowledge_graph.model import GraphData
from knowledge_graph.release import release_doctor
from knowledge_graph.store import load_graph, write_graph, write_json
from knowledge_graph.validate import validate_graph


class CombinedReleaseError(ValueError):
    pass


T = TypeVar("T")


def _merge_unique(values: Iterable[T], identity: str) -> tuple[T, ...]:
    merged: dict[str, T] = {}
    for value in values:
        key = str(getattr(value, identity))
        prior = merged.setdefault(key, value)
        if prior != value:
            raise CombinedReleaseError(f"conflicting {identity}: {key}")
    return tuple(merged[key] for key in sorted(merged))


def merge_release_graphs(
    ancient: GraphData,
    modern: GraphData,
    *,
    graph_version: str,
) -> GraphData:
    merged = GraphData(
        schema_version=ancient.schema_version,
        graph_version=graph_version,
        bundle_id=f"combined:{ancient.bundle_id}:{modern.bundle_id}",
        sources=_merge_unique((*ancient.sources, *modern.sources), "source_id"),
        nodes=_merge_unique((*ancient.nodes, *modern.nodes), "node_id"),
        evidence=_merge_unique((*ancient.evidence, *modern.evidence), "evidence_id"),
        edges=_merge_unique((*ancient.edges, *modern.edges), "edge_id"),
        metadata={
            "description": "22-book ancient KG plus automatic modern evidence overlay.",
            "parents": [ancient.graph_version, modern.graph_version],
            "approval_policy": {
                "threshold": 0.7,
                "comparison": "greater_than_or_equal",
                "human_reviewed": False,
            },
            "scientific_boundary": (
                "Machine approval means reproducible threshold acceptance, not clinical "
                "recommendation or wet-lab validation. No automatic TREATS edge is allowed."
            ),
        },
    )
    if any(edge.predicate == "TREATS" for edge in merged.edges):
        raise CombinedReleaseError("automatic combined release must not contain TREATS")
    return merged


def finalize_combined_release(
    *,
    ancient_graph_dir: Path,
    modern_graph_dir: Path,
    ancient_database: Path,
    output_root: Path,
    graph_version: str,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite {output_root}")
    temporary = output_root.with_name(f".{output_root.name}.tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary output exists: {temporary}")
    temporary.mkdir(parents=True)
    try:
        graph = merge_release_graphs(
            load_graph(ancient_graph_dir),
            load_graph(modern_graph_dir),
            graph_version=graph_version,
        )
        validation = validate_graph(graph, release=True)
        if not validation["valid"]:
            errors = [item for item in validation["issues"] if item["severity"] == "error"]
            raise CombinedReleaseError(f"combined release validation failed: {errors[:5]}")
        graph_dir = temporary / "graph"
        neo4j_dir = temporary / "neo4j"
        manifest = write_graph(graph, graph_dir, validation_report=validation)
        neo4j = export_neo4j(graph, neo4j_dir)
        doctor = release_doctor(
            graph_dir,
            neo4j_dir=neo4j_dir,
            ancient_database=ancient_database,
        )
        if not doctor["valid"]:
            raise CombinedReleaseError(
                f"combined release doctor failed: {doctor.get('issues', [])[:5]}"
            )
        write_json(temporary / "release_doctor.json", doctor)
        node_types: dict[str, int] = {}
        for node in graph.nodes:
            node_types[node.entity_type] = node_types.get(node.entity_type, 0) + 1
        predicates: dict[str, int] = {}
        for edge in graph.edges:
            predicates[edge.predicate] = predicates.get(edge.predicate, 0) + 1
        report = {
            "valid": True,
            "graph_version": graph_version,
            "counts": {
                "sources": len(graph.sources),
                "nodes": len(graph.nodes),
                "evidence": len(graph.evidence),
                "edges": len(graph.edges),
            },
            "node_types": dict(sorted(node_types.items())),
            "predicates": dict(sorted(predicates.items())),
            "content_fingerprint": manifest["content_fingerprint"],
            "neo4j_content_fingerprint": neo4j["content_fingerprint"],
            "release_validation_valid": True,
            "release_doctor_valid": True,
            "automatic_treats_edges": 0,
            "human_review_required": False,
            "human_reviewed": False,
        }
        write_json(temporary / "combined_release_report.json", report)
        os.replace(temporary, output_root)
        return report
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize the combined automatic KG release")
    parser.add_argument("--ancient-graph", type=Path, required=True)
    parser.add_argument("--modern-graph", type=Path, required=True)
    parser.add_argument("--ancient-database", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--graph-version", required=True)
    args = parser.parse_args()
    report = finalize_combined_release(
        ancient_graph_dir=args.ancient_graph,
        modern_graph_dir=args.modern_graph,
        ancient_database=args.ancient_database,
        output_root=args.output_root,
        graph_version=args.graph_version,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
