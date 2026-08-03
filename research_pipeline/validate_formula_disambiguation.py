from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_graph.store import load_graph, write_json


def validate_formula_disambiguation(graph: Any, formula_name: str) -> dict[str, Any]:
    variants = sorted(
        (
            node
            for node in graph.nodes
            if node.entity_type == "FormulaVariant"
            and node.canonical_name == formula_name
        ),
        key=lambda node: (
            int(node.attributes.get("source_locator", {}).get("physical_page", 0)),
            node.node_id,
        ),
    )
    variant_ids = {node.node_id for node in variants}
    relevant_edges = [
        edge
        for edge in graph.edges
        if edge.subject_id in variant_ids or edge.object_id in variant_ids
    ]
    concept_ids = {
        edge.object_id
        for edge in relevant_edges
        if edge.subject_id in variant_ids and edge.predicate == "VARIANT_OF"
    }
    ingredient_edges = [
        edge
        for edge in relevant_edges
        if edge.subject_id in variant_ids and edge.predicate == "HAS_INGREDIENT"
    ]
    fingerprints = {
        str(node.attributes.get("composition_fingerprint", "")) for node in variants
    }
    pages = {
        int(node.attributes.get("source_locator", {}).get("physical_page", 0))
        for node in variants
    }
    issues: list[str] = []
    if len(variants) < 2:
        issues.append("fewer_than_two_formula_variants")
    if "" in fingerprints or len(fingerprints) != len(variants):
        issues.append("composition_fingerprints_not_unique")
    if 0 in pages or len(pages) != len(variants):
        issues.append("source_pages_not_unique")
    if len(concept_ids) != 1:
        issues.append("variants_do_not_share_one_formula_concept")
    if any(
        not any(edge.subject_id == node.node_id for edge in ingredient_edges)
        for node in variants
    ):
        issues.append("variant_without_ingredient_edge")
    if any(not edge.evidence_ids for edge in relevant_edges):
        issues.append("untraceable_formula_edge")

    examples = []
    for node in variants:
        locator = dict(node.attributes.get("source_locator", {}))
        node_edges = [edge for edge in relevant_edges if edge.subject_id == node.node_id]
        examples.append(
            {
                "node_id": node.node_id,
                "formula_name": node.canonical_name,
                "physical_page": locator.get("physical_page"),
                "page_id": locator.get("page_id"),
                "composition": list(node.attributes.get("composition", [])),
                "undosed_ingredients": list(
                    node.attributes.get("undosed_ingredients", [])
                ),
                "composition_fingerprint": node.attributes.get(
                    "composition_fingerprint", ""
                ),
                "semantic_confidence": node.attributes.get(
                    "semantic_confidence", 0.0
                ),
                "edge_ids": [edge.edge_id for edge in node_edges],
                "evidence_ids": sorted(
                    {
                        evidence_id
                        for edge in node_edges
                        for evidence_id in edge.evidence_ids
                    }
                ),
            }
        )
    return {
        "valid": not issues,
        "graph_version": graph.graph_version,
        "formula_name": formula_name,
        "variant_count": len(variants),
        "shared_formula_concept_ids": sorted(concept_ids),
        "unique_composition_fingerprints": len(fingerprints),
        "unique_source_pages": len(pages),
        "ingredient_edge_count": len(ingredient_edges),
        "issues": issues,
        "variants": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate same-name formula variants")
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--formula-name", default="忍冬汤")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = validate_formula_disambiguation(load_graph(args.graph), args.formula_name)
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
