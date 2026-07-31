from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    source_type: str
    title: str
    file_name: str = ""
    file_sha256: str = ""
    doi: str = ""
    year: str = ""
    work_id: str = ""
    edition_id: str = ""
    attributes: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "SourceRecord":
        return cls(
            source_id=str(data["source_id"]),
            source_type=str(data["source_type"]),
            title=str(data["title"]),
            file_name=str(data.get("file_name", "")),
            file_sha256=str(data.get("file_sha256", "")),
            doi=str(data.get("doi", "")),
            year=str(data.get("year", "")),
            work_id=str(data.get("work_id", "")),
            edition_id=str(data.get("edition_id", "")),
            attributes=dict(data.get("attributes", {})),
        )

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    entity_type: str
    canonical_name: str
    layer: str
    aliases: tuple[str, ...] = ()
    external_ids: JsonDict = field(default_factory=dict)
    attributes: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "GraphNode":
        return cls(
            node_id=str(data["node_id"]),
            entity_type=str(data["entity_type"]),
            canonical_name=str(data["canonical_name"]),
            layer=str(data["layer"]),
            aliases=tuple(str(value) for value in data.get("aliases", [])),
            external_ids=dict(data.get("external_ids", {})),
            attributes=dict(data.get("attributes", {})),
        )

    def to_dict(self) -> JsonDict:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_id: str
    locator: JsonDict
    quote: str
    quote_sha256: str
    evidence_grade: str
    evidence_class: str
    review: JsonDict
    attributes: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "EvidenceRecord":
        return cls(
            evidence_id=str(data["evidence_id"]),
            source_id=str(data["source_id"]),
            locator=dict(data.get("locator", {})),
            quote=str(data.get("quote", "")),
            quote_sha256=str(data.get("quote_sha256", "")),
            evidence_grade=str(data["evidence_grade"]),
            evidence_class=str(data["evidence_class"]),
            review=dict(data.get("review", {})),
            attributes=dict(data.get("attributes", {})),
        )

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    subject_id: str
    predicate: str
    object_id: str
    evidence_ids: tuple[str, ...]
    evidence_grade: str
    assertion_mode: str
    confidence: float
    review_status: str
    attributes: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonDict) -> "GraphEdge":
        return cls(
            edge_id=str(data["edge_id"]),
            subject_id=str(data["subject_id"]),
            predicate=str(data["predicate"]),
            object_id=str(data["object_id"]),
            evidence_ids=tuple(str(value) for value in data.get("evidence_ids", [])),
            evidence_grade=str(data["evidence_grade"]),
            assertion_mode=str(data["assertion_mode"]),
            confidence=float(data["confidence"]),
            review_status=str(data["review_status"]),
            attributes=dict(data.get("attributes", {})),
        )

    def to_dict(self) -> JsonDict:
        data = asdict(self)
        data["evidence_ids"] = list(self.evidence_ids)
        return data


@dataclass(frozen=True)
class GraphData:
    schema_version: str
    graph_version: str
    bundle_id: str
    sources: tuple[SourceRecord, ...]
    nodes: tuple[GraphNode, ...]
    evidence: tuple[EvidenceRecord, ...]
    edges: tuple[GraphEdge, ...]
    metadata: JsonDict = field(default_factory=dict)

    def to_metadata_dict(self) -> JsonDict:
        return {
            "schema_version": self.schema_version,
            "graph_version": self.graph_version,
            "bundle_id": self.bundle_id,
            "metadata": self.metadata,
        }
