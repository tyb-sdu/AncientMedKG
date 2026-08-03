from __future__ import annotations

from knowledge_graph.build import build_bundle
from knowledge_graph.validate import validate_graph
from research_pipeline.finalize_combined_release import merge_release_graphs


def _bundle(prefix: str, entity_type: str, predicate: str, target_type: str):
    source = f"source:{prefix}"
    left = f"left:{prefix}"
    right = f"right:{prefix}"
    evidence = f"evidence:{prefix}"
    return build_bundle(
        {
            "schema_version": "1.0.0",
            "bundle_id": prefix,
            "graph_version": f"{prefix}-v1",
            "sources": [
                {"key": source, "source_type": "test", "title": prefix}
            ],
            "entities": [
                {"key": left, "entity_type": entity_type, "canonical_name": left},
                {"key": right, "entity_type": target_type, "canonical_name": right},
            ],
            "evidence": [
                {
                    "key": evidence,
                    "source": source,
                    "locator": {"id": prefix},
                    "quote": prefix,
                    "evidence_grade": "E4",
                    "evidence_class": "modern_bridge",
                    "review": {"status": "approved"},
                }
            ],
            "assertions": [
                {
                    "subject": left,
                    "predicate": predicate,
                    "object": right,
                    "evidence": [evidence],
                    "evidence_grade": "E4",
                    "assertion_mode": "inferred",
                    "confidence": 0.8,
                    "review_status": "approved",
                }
            ],
        }
    )


def test_combined_release_merges_approved_graphs_without_treats() -> None:
    ancient = _bundle("ancient", "Disease", "HAS_TREATMENT_METHOD", "TreatmentMethod")
    modern = _bundle("modern", "Compound", "STUDIED_IN", "Study")
    combined = merge_release_graphs(ancient, modern, graph_version="combined-v1")
    assert validate_graph(combined, release=True)["valid"] is True
    assert len(combined.sources) == 2
    assert len(combined.nodes) == 4
    assert len(combined.edges) == 2
    assert not any(edge.predicate == "TREATS" for edge in combined.edges)
