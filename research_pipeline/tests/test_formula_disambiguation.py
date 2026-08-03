from __future__ import annotations

from types import SimpleNamespace

from research_pipeline.validate_formula_disambiguation import (
    validate_formula_disambiguation,
)


def _variant(node_id: str, page: int, fingerprint: str) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id,
        entity_type="FormulaVariant",
        canonical_name="忍冬汤",
        attributes={
            "source_locator": {"physical_page": page, "page_id": f"page:{page}"},
            "composition": [{"herb": f"herb:{page}"}],
            "composition_fingerprint": fingerprint,
            "semantic_confidence": 0.9,
        },
    )


def _edge(edge_id: str, subject: str, predicate: str, object_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        edge_id=edge_id,
        subject_id=subject,
        predicate=predicate,
        object_id=object_id,
        evidence_ids=(f"evidence:{edge_id}",),
    )


def test_two_same_name_variants_are_disambiguated_by_page_and_composition() -> None:
    graph = SimpleNamespace(
        graph_version="test-v1",
        nodes=(_variant("variant:138", 138, "a" * 64), _variant("variant:227", 227, "b" * 64)),
        edges=(
            _edge("variant-138-concept", "variant:138", "VARIANT_OF", "formula:rendongtang"),
            _edge("variant-227-concept", "variant:227", "VARIANT_OF", "formula:rendongtang"),
            _edge("variant-138-herb", "variant:138", "HAS_INGREDIENT", "herb:lonicera"),
            _edge("variant-227-herb", "variant:227", "HAS_INGREDIENT", "herb:smilax"),
        ),
    )
    report = validate_formula_disambiguation(graph, "忍冬汤")
    assert report["valid"] is True
    assert report["variant_count"] == 2
    assert [row["physical_page"] for row in report["variants"]] == [138, 227]
