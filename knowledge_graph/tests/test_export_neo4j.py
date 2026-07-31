from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from knowledge_graph.build import build_bundle_file
from knowledge_graph.export_neo4j import export_neo4j


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "evidence_bundle.example.json"
)


class Neo4jExportTests(unittest.TestCase):
    def test_export_contains_direct_and_reified_provenance(self) -> None:
        graph = build_bundle_file(EXAMPLE)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "neo4j"
            manifest = export_neo4j(graph, output)
            self.assertEqual(
                manifest["counts"]["direct_relationships"], len(graph.edges)
            )
            self.assertTrue((output / "graph.jsonld").is_file())
            self.assertTrue((output / "constraints.cypher").is_file())
            with (output / "relationships.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), len(graph.edges))
            self.assertIn("HAS_INGREDIENT", {row[":TYPE"] for row in rows})
            with (output / "provenance_relationships.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                provenance = list(csv.DictReader(handle))
            relation_types = {row[":TYPE"] for row in provenance}
            self.assertEqual(
                relation_types,
                {"EXTRACTED_FROM", "ASSERTS_ENTITY", "SUPPORTED_BY"},
            )
            with self.assertRaises(FileExistsError):
                export_neo4j(graph, output)


if __name__ == "__main__":
    unittest.main()
