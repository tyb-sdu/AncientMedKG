from __future__ import annotations

import json
import unittest
from pathlib import Path

from discovery_pipeline.mechanism import (
    MechanismInputError,
    analyze_mechanism,
    benjamini_hochberg,
    hypergeometric_survival,
    pathway_enrichment,
)


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "mechanism.example.json"
)


class MechanismTests(unittest.TestCase):
    def test_bh_is_monotone_and_bounded(self) -> None:
        adjusted = benjamini_hochberg([("a", 0.01), ("b", 0.04), ("c", 0.03)])
        self.assertTrue(all(0 <= value <= 1 for value in adjusted.values()))
        self.assertLessEqual(adjusted["a"], adjusted["c"])
        self.assertLessEqual(adjusted["c"], adjusted["b"])

    def test_hypergeometric_survival_bounds(self) -> None:
        probability = hypergeometric_survival(2, 100, 10, 10)
        self.assertGreater(probability, 0)
        self.assertLessEqual(probability, 1)
        self.assertEqual(hypergeometric_survival(0, 100, 10, 10), 1.0)

    def test_predictions_are_excluded_from_primary_mechanism(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        report = analyze_mechanism(payload)
        self.assertEqual(
            report["core_targets_by_compound"]["compound:example_a"],
            ["GENEA"],
        )
        self.assertIn(
            "GENEB",
            report["extended_targets_by_compound"]["compound:example_a"],
        )
        docking = next(
            value
            for value in report["target_audit"]
            if value["evidence_type"] == "molecular_docking"
        )
        self.assertFalse(docking["used_in_primary_mechanism"])

    def test_pending_ppi_and_pathway_are_excluded(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        payload["ppi_edges"][0]["review_status"] = "pending"
        payload["pathways"][0]["review_status"] = "pending"
        report = analyze_mechanism(payload)
        self.assertEqual(report["ppi"]["edge_count"], 0)
        self.assertEqual(report["enrichment"], [])
        self.assertFalse(report["ppi"]["audit"][0]["used_in_primary_mechanism"])
        self.assertFalse(report["pathway_audit"][0]["used_in_enrichment"])

    def test_enrichment_accounts_for_zero_overlap_pathways(self) -> None:
        result = pathway_enrichment(
            {"A"},
            {"A", "B", "C", "D"},
            [
                {"pathway_id": "one", "genes": ["A"]},
                {"pathway_id": "two", "genes": ["B"]},
            ],
        )
        self.assertEqual(len(result), 2)
        no_overlap = next(value for value in result if value["pathway_id"] == "two")
        self.assertEqual(no_overlap["overlap_count"], 0)
        self.assertEqual(no_overlap["p_value"], 1.0)

    def test_approved_ppi_requires_traceable_source(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        payload["ppi_edges"][0].pop("evidence_ids")
        with self.assertRaises(MechanismInputError):
            analyze_mechanism(payload)


if __name__ == "__main__":
    unittest.main()
