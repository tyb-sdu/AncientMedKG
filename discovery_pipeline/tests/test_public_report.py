from __future__ import annotations

import json
import unittest
from pathlib import Path


REPORT = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "intake_baseline_v1.json"
)


class PublicReportTests(unittest.TestCase):
    def test_intake_baseline_is_sanitized_and_scientifically_bounded(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        candidates = report["chemical_identity"]["candidates"]
        self.assertEqual(len(candidates), 13)
        self.assertEqual(
            len({value["candidate_id"] for value in candidates}),
            13,
        )
        self.assertEqual(report["literature_intake"]["locus_count"], 2238)
        self.assertTrue(report["integrity"]["valid"])
        self.assertFalse(report["scientific_boundary"]["scientific_release_ready"])

        serialized = json.dumps(report, ensure_ascii=False).lower()
        for forbidden in (
            "/data2/",
            "c:\\users\\",
            "source_filename",
            "snippet",
            "query_url",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
