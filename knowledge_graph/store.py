from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .ids import sha256_text
from .model import EvidenceRecord, GraphData, GraphEdge, GraphNode, SourceRecord


GRAPH_FILES = {
    "sources": "sources.jsonl",
    "nodes": "nodes.jsonl",
    "evidence": "evidence.jsonl",
    "edges": "edges.jsonl",
    "metadata": "graph_metadata.json",
}


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _jsonl_text(records: Iterable[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for value in records
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _json_text(value))


def write_graph(
    graph: GraphData,
    output_dir: Path,
    *,
    validation_report: Mapping[str, Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    protected_names = set(GRAPH_FILES.values()) | {
        "manifest.json",
        "validation_report.json",
    }
    conflicts = sorted(
        path.name for path in output_dir.iterdir() if path.name in protected_names
    )
    if conflicts:
        raise FileExistsError(
            f"refusing to overwrite graph build files in {output_dir}: {conflicts}"
        )

    _atomic_write_text(
        output_dir / GRAPH_FILES["sources"],
        _jsonl_text(value.to_dict() for value in graph.sources),
    )
    _atomic_write_text(
        output_dir / GRAPH_FILES["nodes"],
        _jsonl_text(value.to_dict() for value in graph.nodes),
    )
    _atomic_write_text(
        output_dir / GRAPH_FILES["evidence"],
        _jsonl_text(value.to_dict() for value in graph.evidence),
    )
    _atomic_write_text(
        output_dir / GRAPH_FILES["edges"],
        _jsonl_text(value.to_dict() for value in graph.edges),
    )
    _atomic_write_text(
        output_dir / GRAPH_FILES["metadata"],
        _json_text(graph.to_metadata_dict()),
    )
    _atomic_write_text(
        output_dir / "validation_report.json",
        _json_text(dict(validation_report)),
    )
    files = {
        file_name: {
            "sha256": file_sha256(output_dir / file_name),
            "bytes": (output_dir / file_name).stat().st_size,
        }
        for file_name in sorted(
            list(GRAPH_FILES.values()) + ["validation_report.json"]
        )
    }
    manifest = {
        "format_version": "ancientmedkg-graph-build-v1",
        "schema_version": graph.schema_version,
        "graph_version": graph.graph_version,
        "bundle_id": graph.bundle_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "sources": len(graph.sources),
            "nodes": len(graph.nodes),
            "evidence": len(graph.evidence),
            "edges": len(graph.edges),
        },
        "input_bundle_sha256": graph.metadata.get("input_bundle_sha256", ""),
        "parent_version": graph.metadata.get("parent_version"),
        "files": files,
    }
    manifest["content_fingerprint"] = sha256_text(
        json.dumps(files, sort_keys=True, separators=(",", ":"))
    )
    _atomic_write_text(output_dir / "manifest.json", _json_text(manifest))
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row must be an object in {path}:{line_number}")
            records.append(value)
    return records


def load_graph(input_dir: Path, *, verify_manifest: bool = True) -> GraphData:
    metadata = json.loads(
        (input_dir / GRAPH_FILES["metadata"]).read_text(encoding="utf-8")
    )
    if verify_manifest:
        manifest = json.loads(
            (input_dir / "manifest.json").read_text(encoding="utf-8")
        )
        for file_name, expected in manifest.get("files", {}).items():
            actual = file_sha256(input_dir / file_name)
            if actual != expected["sha256"]:
                raise ValueError(
                    f"graph file hash mismatch for {file_name}: "
                    f"expected {expected['sha256']}, got {actual}"
                )
    return GraphData(
        schema_version=str(metadata["schema_version"]),
        graph_version=str(metadata["graph_version"]),
        bundle_id=str(metadata["bundle_id"]),
        sources=tuple(
            SourceRecord.from_dict(value)
            for value in _read_jsonl(input_dir / GRAPH_FILES["sources"])
        ),
        nodes=tuple(
            GraphNode.from_dict(value)
            for value in _read_jsonl(input_dir / GRAPH_FILES["nodes"])
        ),
        evidence=tuple(
            EvidenceRecord.from_dict(value)
            for value in _read_jsonl(input_dir / GRAPH_FILES["evidence"])
        ),
        edges=tuple(
            GraphEdge.from_dict(value)
            for value in _read_jsonl(input_dir / GRAPH_FILES["edges"])
        ),
        metadata=dict(metadata.get("metadata", {})),
    )
