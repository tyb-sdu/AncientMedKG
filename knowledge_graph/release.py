from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import GraphData
from .source_verify import verify_graph_sources
from .store import file_sha256, load_graph
from .validate import validate_graph


def verify_neo4j_export(graph: GraphData, export_dir: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []

    def add(code: str, message: str, file_name: str = "") -> None:
        issues.append({"code": code, "message": message, "file": file_name})

    manifest_path = export_dir / "neo4j_import_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "issues": [
                {
                    "code": "neo4j_manifest_unreadable",
                    "message": str(exc),
                    "file": str(manifest_path),
                }
            ],
        }

    if manifest.get("format_version") != "ancientmedkg-neo4j-import-v1":
        add("neo4j_format_invalid", "unexpected Neo4j export format")
    if manifest.get("graph_version") != graph.graph_version:
        add("neo4j_graph_version_mismatch", "graph_version does not match graph build")
    if manifest.get("schema_version") != graph.schema_version:
        add("neo4j_schema_version_mismatch", "schema_version does not match graph build")
    expected_fingerprint = graph.metadata.get("build_content_fingerprint", "")
    if (
        not expected_fingerprint
        or manifest.get("source_graph_content_fingerprint") != expected_fingerprint
    ):
        add(
            "neo4j_source_graph_fingerprint_mismatch",
            "Neo4j export is not bound to the loaded graph content fingerprint",
        )

    expected_counts = {
        "entity_nodes": len(graph.nodes),
        "source_nodes": len(graph.sources),
        "evidence_nodes": len(graph.evidence),
        "assertion_nodes": len(graph.edges),
        "direct_relationships": len(graph.edges),
        "provenance_relationships": len(graph.evidence)
        + sum(2 + len(edge.evidence_ids) for edge in graph.edges),
    }
    actual_counts = dict(manifest.get("counts", {}))
    for name, expected in expected_counts.items():
        if actual_counts.get(name) != expected:
            add(
                "neo4j_count_mismatch",
                f"{name}: expected {expected}, got {actual_counts.get(name)}",
            )

    files = manifest.get("files", {})
    if not isinstance(files, dict) or not files:
        add("neo4j_files_missing", "manifest has no exported files")
    else:
        for file_name, expected in files.items():
            path = export_dir / file_name
            if not path.is_file():
                add("neo4j_file_missing", "exported file is absent", file_name)
                continue
            actual_sha = file_sha256(path)
            if actual_sha != expected.get("sha256"):
                add(
                    "neo4j_file_sha256_mismatch",
                    f"expected {expected.get('sha256')}, got {actual_sha}",
                    file_name,
                )
            if path.stat().st_size != expected.get("bytes"):
                add(
                    "neo4j_file_size_mismatch",
                    f"expected {expected.get('bytes')}, got {path.stat().st_size}",
                    file_name,
                )
    return {
        "valid": not issues,
        "graph_version": graph.graph_version,
        "source_graph_content_fingerprint": expected_fingerprint,
        "issues": issues,
    }


def release_doctor(
    graph_dir: Path,
    *,
    neo4j_dir: Path,
    ancient_database: Path | None = None,
    modern_database: Path | None = None,
) -> dict[str, Any]:
    graph = load_graph(graph_dir)
    graph_validation = validate_graph(graph, release=True)
    source_verification = verify_graph_sources(
        graph,
        ancient_database=ancient_database,
        modern_database=modern_database,
    )
    neo4j_verification = verify_neo4j_export(graph, neo4j_dir)
    valid = all(
        report["valid"]
        for report in (
            graph_validation,
            source_verification,
            neo4j_verification,
        )
    )
    return {
        "valid": valid,
        "graph_version": graph.graph_version,
        "graph_content_fingerprint": graph.metadata.get(
            "build_content_fingerprint", ""
        ),
        "graph_validation": graph_validation,
        "source_verification": source_verification,
        "neo4j_verification": neo4j_verification,
    }
