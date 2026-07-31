from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from knowledge_graph.build import build_bundle, build_bundle_file
from knowledge_graph.model import GraphData
from knowledge_graph.store import load_graph, write_graph
from knowledge_graph.validate import validate_graph


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "evidence_bundle.example.json"
)


class BuildValidateTests(unittest.TestCase):
    def test_public_example_passes_release_validation(self) -> None:
        graph = build_bundle_file(EXAMPLE)
        report = validate_graph(graph, release=True)
        self.assertTrue(report["valid"], report["issues"])
        self.assertEqual(report["metrics"]["key_path_traceability_rate"], 1.0)
        self.assertEqual(
            report["metrics"]["formula_variant_completeness_rate"], 1.0
        )

    def test_store_is_immutable_and_manifest_is_verified(self) -> None:
        graph = build_bundle_file(EXAMPLE)
        report = validate_graph(graph, release=True)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "graph"
            manifest = write_graph(graph, output, validation_report=report)
            self.assertEqual(manifest["counts"]["nodes"], len(graph.nodes))
            loaded = load_graph(output)
            self.assertEqual(loaded.nodes, graph.nodes)
            with self.assertRaises(FileExistsError):
                write_graph(graph, output, validation_report=report)
            nodes_path = output / "nodes.jsonl"
            nodes_path.write_text(
                nodes_path.read_text(encoding="utf-8") + "{}\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_graph(output)

    def test_quote_hash_mismatch_is_rejected_during_build(self) -> None:
        bundle = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        bundle["evidence"][0]["quote_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            path.write_text(
                json.dumps(bundle, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                build_bundle_file(path)

    def test_source_keys_are_resolved_inside_entity_locators(self) -> None:
        bundle = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        bundle["entities"][2]["attributes"]["source_id"] = "ancient_demo"
        bundle["entities"][6]["attributes"]["source_locator"]["source_id"] = (
            "ancient_demo"
        )
        graph = build_bundle(bundle)
        passage = next(
            value for value in graph.nodes if value.entity_type == "Passage"
        )
        variant = next(
            value for value in graph.nodes if value.entity_type == "FormulaVariant"
        )
        self.assertEqual(
            passage.attributes["source_id"], "kg:source:public-example-ancient"
        )
        self.assertEqual(
            variant.attributes["source_locator"]["source_id"],
            "kg:source:public-example-ancient",
        )

    def test_empty_graph_cannot_pass_release(self) -> None:
        graph = GraphData(
            schema_version="1.0.0",
            graph_version="empty",
            bundle_id="empty",
            sources=(),
            nodes=(),
            evidence=(),
            edges=(),
        )
        report = validate_graph(graph, release=True)
        self.assertFalse(report["valid"])
        self.assertIn(
            "empty_release_graph",
            {value["code"] for value in report["issues"]},
        )


if __name__ == "__main__":
    unittest.main()
