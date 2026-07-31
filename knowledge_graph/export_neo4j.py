from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ids import canonical_json, sha256_text
from .model import GraphData
from .store import file_sha256


_STATIC_FILES = ("constraints.cypher", "example_queries.cypher")


def _write_csv(path: Path, header: list[str], rows: Iterable[list[Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    os.replace(temporary, path)


def _labels(*values: str) -> str:
    return ";".join(value for value in values if value)


def _jsonld(graph: GraphData) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for node in graph.nodes:
        records.append(
            {
                "@id": node.node_id,
                "@type": ["kg:Entity", f"kg:{node.entity_type}"],
                "name": node.canonical_name,
                "kg:layer": node.layer,
                "kg:aliases": list(node.aliases),
                "kg:externalIds": node.external_ids,
                "kg:attributes": node.attributes,
            }
        )
    for source in graph.sources:
        records.append(
            {
                "@id": source.source_id,
                "@type": "kg:SourceDocument",
                "name": source.title,
                "kg:sourceType": source.source_type,
                "kg:fileName": source.file_name,
                "kg:fileSha256": source.file_sha256,
                "kg:doi": source.doi,
                "kg:attributes": source.attributes,
            }
        )
    for evidence in graph.evidence:
        records.append(
            {
                "@id": evidence.evidence_id,
                "@type": "kg:EvidenceSpan",
                "kg:extractedFrom": {"@id": evidence.source_id},
                "kg:locator": evidence.locator,
                "kg:quote": evidence.quote,
                "kg:quoteSha256": evidence.quote_sha256,
                "kg:evidenceGrade": evidence.evidence_grade,
                "kg:evidenceClass": evidence.evidence_class,
                "kg:review": evidence.review,
            }
        )
    for edge in graph.edges:
        records.append(
            {
                "@id": edge.edge_id,
                "@type": "kg:Assertion",
                "kg:subject": {"@id": edge.subject_id},
                "kg:predicate": edge.predicate,
                "kg:object": {"@id": edge.object_id},
                "kg:supportedBy": [
                    {"@id": evidence_id} for evidence_id in edge.evidence_ids
                ],
                "kg:evidenceGrade": edge.evidence_grade,
                "kg:assertionMode": edge.assertion_mode,
                "kg:confidence": edge.confidence,
                "kg:reviewStatus": edge.review_status,
                "kg:attributes": edge.attributes,
            }
        )
    return {
        "@context": {
            "kg": "https://github.com/tyb-sdu/AncientMedKG/schema/",
            "name": "https://schema.org/name",
        },
        "@graph": records,
    }


def export_neo4j(graph: GraphData, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_files = {
        "nodes.csv",
        "source_nodes.csv",
        "evidence_nodes.csv",
        "assertion_nodes.csv",
        "relationships.csv",
        "provenance_relationships.csv",
        "graph.jsonld",
        "constraints.cypher",
        "example_queries.cypher",
        "neo4j_import_manifest.json",
    }
    conflicts = sorted(
        path.name for path in output_dir.iterdir() if path.name in expected_files
    )
    if conflicts:
        raise FileExistsError(
            f"refusing to overwrite Neo4j export files in {output_dir}: {conflicts}"
        )

    _write_csv(
        output_dir / "nodes.csv",
        [
            ":ID",
            "node_id",
            "canonical_name",
            "entity_type",
            "layer",
            "aliases_json",
            "external_ids_json",
            "attributes_json",
            ":LABEL",
        ],
        (
            [
                node.node_id,
                node.node_id,
                node.canonical_name,
                node.entity_type,
                node.layer,
                canonical_json(list(node.aliases)),
                canonical_json(node.external_ids),
                canonical_json(node.attributes),
                _labels("Entity", "CoreEntity" if node.layer != "EXT" else "ExtensionEntity", node.layer, node.entity_type),
            ]
            for node in graph.nodes
        ),
    )
    _write_csv(
        output_dir / "source_nodes.csv",
        [
            ":ID",
            "source_id",
            "source_type",
            "title",
            "file_name",
            "file_sha256",
            "doi",
            "year",
            "work_id",
            "edition_id",
            "attributes_json",
            ":LABEL",
        ],
        (
            [
                source.source_id,
                source.source_id,
                source.source_type,
                source.title,
                source.file_name,
                source.file_sha256,
                source.doi,
                source.year,
                source.work_id,
                source.edition_id,
                canonical_json(source.attributes),
                _labels("SourceDocument", source.source_type),
            ]
            for source in graph.sources
        ),
    )
    _write_csv(
        output_dir / "evidence_nodes.csv",
        [
            ":ID",
            "evidence_id",
            "source_id",
            "locator_json",
            "quote",
            "quote_sha256",
            "evidence_grade",
            "evidence_class",
            "review_json",
            "attributes_json",
            ":LABEL",
        ],
        (
            [
                evidence.evidence_id,
                evidence.evidence_id,
                evidence.source_id,
                canonical_json(evidence.locator),
                evidence.quote,
                evidence.quote_sha256,
                evidence.evidence_grade,
                evidence.evidence_class,
                canonical_json(evidence.review),
                canonical_json(evidence.attributes),
                _labels("EvidenceSpan", evidence.evidence_grade, evidence.evidence_class),
            ]
            for evidence in graph.evidence
        ),
    )
    _write_csv(
        output_dir / "assertion_nodes.csv",
        [
            ":ID",
            "assertion_id",
            "predicate",
            "evidence_grade",
            "assertion_mode",
            "confidence:double",
            "review_status",
            "attributes_json",
            ":LABEL",
        ],
        (
            [
                edge.edge_id,
                edge.edge_id,
                edge.predicate,
                edge.evidence_grade,
                edge.assertion_mode,
                edge.confidence,
                edge.review_status,
                canonical_json(edge.attributes),
                _labels("Assertion", edge.assertion_mode),
            ]
            for edge in graph.edges
        ),
    )
    _write_csv(
        output_dir / "relationships.csv",
        [
            ":START_ID",
            ":END_ID",
            "edge_id",
            "evidence_ids_json",
            "evidence_grade",
            "assertion_mode",
            "confidence:double",
            "review_status",
            "attributes_json",
            ":TYPE",
        ],
        (
            [
                edge.subject_id,
                edge.object_id,
                edge.edge_id,
                canonical_json(list(edge.evidence_ids)),
                edge.evidence_grade,
                edge.assertion_mode,
                edge.confidence,
                edge.review_status,
                canonical_json(edge.attributes),
                edge.predicate,
            ]
            for edge in graph.edges
        ),
    )
    provenance_rows: list[list[Any]] = []
    for evidence in graph.evidence:
        provenance_rows.append(
            [evidence.evidence_id, evidence.source_id, "", "EXTRACTED_FROM"]
        )
    for edge in graph.edges:
        provenance_rows.append(
            [edge.edge_id, edge.subject_id, "subject", "ASSERTS_ENTITY"]
        )
        provenance_rows.append(
            [edge.edge_id, edge.object_id, "object", "ASSERTS_ENTITY"]
        )
        for evidence_id in edge.evidence_ids:
            provenance_rows.append(
                [edge.edge_id, evidence_id, "support", "SUPPORTED_BY"]
            )
    _write_csv(
        output_dir / "provenance_relationships.csv",
        [":START_ID", ":END_ID", "role", ":TYPE"],
        provenance_rows,
    )

    jsonld_path = output_dir / "graph.jsonld"
    jsonld_path.write_text(
        json.dumps(_jsonld(graph), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    static_dir = Path(__file__).with_name("neo4j")
    for file_name in _STATIC_FILES:
        shutil.copyfile(static_dir / file_name, output_dir / file_name)

    data_files = sorted(expected_files - {"neo4j_import_manifest.json"})
    files = {
        file_name: {
            "sha256": file_sha256(output_dir / file_name),
            "bytes": (output_dir / file_name).stat().st_size,
        }
        for file_name in data_files
    }
    manifest = {
        "format_version": "ancientmedkg-neo4j-import-v1",
        "graph_version": graph.graph_version,
        "schema_version": graph.schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_graph_content_fingerprint": graph.metadata.get(
            "build_content_fingerprint", ""
        ),
        "source_graph_manifest_sha256": graph.metadata.get(
            "build_manifest_sha256", ""
        ),
        "counts": {
            "entity_nodes": len(graph.nodes),
            "source_nodes": len(graph.sources),
            "evidence_nodes": len(graph.evidence),
            "assertion_nodes": len(graph.edges),
            "direct_relationships": len(graph.edges),
            "provenance_relationships": len(provenance_rows),
        },
        "files": files,
    }
    manifest["content_fingerprint"] = sha256_text(
        json.dumps(files, sort_keys=True, separators=(",", ":"))
    )
    (output_dir / "neo4j_import_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest
