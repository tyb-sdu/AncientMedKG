from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from discovery_pipeline.compound_scoring import ScoringInputError, score_catalog


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "compound_scoring.example.json"
)


class CompoundScoringTests(unittest.TestCase):
    def test_reviewed_candidate_precedes_provisional_candidate(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        report = score_catalog(payload)
        first, second = report["ranked_candidates"]
        self.assertEqual(first["candidate_id"], "compound:example_a")
        self.assertEqual(first["ranking_status"], "reviewed")
        self.assertEqual(first["tier"], "tier_1")
        self.assertEqual(second["ranking_status"], "provisional")
        self.assertEqual(second["tier"], "provisional_unreleased")
        self.assertEqual(report["sensitivity"]["scenario_count"], 15)

    def test_failed_gate_eliminates_high_scoring_candidate(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        candidate = payload["candidates"][0]
        candidate["gates"]["C2"] = {
            "status": "fail",
            "evidence_ids": ["ev:failed"],
        }
        report = score_catalog(payload)
        eliminated = next(
            value
            for value in report["ranked_candidates"]
            if value["candidate_id"] == "compound:example_a"
        )
        self.assertEqual(eliminated["tier"], "eliminated")
        self.assertIn("C2", eliminated["failed_gates"])
        for scenario in report["sensitivity"]["scenarios"]:
            self.assertNotIn(
                "compound:example_a",
                {value["candidate_id"] for value in scenario["ranking"]},
            )

    def test_duplicate_candidate_and_nonfinite_weight_are_rejected(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        payload["candidates"].append(dict(payload["candidates"][0]))
        with self.assertRaises(ScoringInputError):
            score_catalog(payload)

        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        payload["weights"] = {
            "source_content": math.inf,
            "formula_exposure": 0.15,
            "burn_wound_evidence": 0.25,
            "target_pathway_support": 0.20,
            "synergy_complementarity": 0.10,
            "safety_verifiability": 0.10,
        }
        with self.assertRaises(ScoringInputError):
            score_catalog(payload)


if __name__ == "__main__":
    unittest.main()
