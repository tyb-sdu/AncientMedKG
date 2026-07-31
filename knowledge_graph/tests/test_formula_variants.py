from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from knowledge_graph.build import build_bundle
from knowledge_graph.ids import make_node_id
from knowledge_graph.validate import validate_graph


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "evidence_bundle.example.json"
)


class FormulaVariantTests(unittest.TestCase):
    def test_same_name_variants_have_distinct_source_bound_ids(self) -> None:
        attributes_page_138 = {
            "formula_name": "同名示例方",
            "composition": [
                {"herb": "甲药", "dose_value": "4", "dose_unit": "两"},
                {"herb": "乙药", "dose_value": "3", "dose_unit": "钱"},
            ],
            "source_locator": {"work_id": "示例古籍", "physical_page": 138},
        }
        attributes_page_227 = {
            "formula_name": "同名示例方",
            "composition": [
                {"herb": "丙药", "dose_value": "5", "dose_unit": "钱"},
                {"herb": "丁药", "dose_value": "3", "dose_unit": "钱"},
            ],
            "source_locator": {"work_id": "示例古籍", "physical_page": 227},
        }
        first = make_node_id(
            "FormulaVariant", "同名示例方", attributes=attributes_page_138
        )
        second = make_node_id(
            "FormulaVariant", "同名示例方", attributes=attributes_page_227
        )
        repeated = make_node_id(
            "FormulaVariant", "同名示例方", attributes=copy.deepcopy(attributes_page_138)
        )
        self.assertNotEqual(first, second)
        self.assertEqual(first, repeated)

    def test_mechanism_transfer_cannot_be_presented_as_e1_fact(self) -> None:
        bundle = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        bundle["entities"].append(
            {
                "key": "burn_outcome",
                "entity_type": "BurnPhenotype",
                "canonical_name": "创面闭合",
            }
        )
        bundle["assertions"].append(
            {
                "subject": "formula_variant",
                "predicate": "MECHANISM_TRANSFER",
                "object": "burn_outcome",
                "evidence": ["page_10"],
                "evidence_grade": "E1",
                "assertion_mode": "explicit",
                "confidence": 1.0,
                "review_status": "approved",
            }
        )
        report = validate_graph(build_bundle(bundle), release=True)
        codes = {value["code"] for value in report["issues"]}
        self.assertFalse(report["valid"])
        self.assertIn("predicate_grade_invalid", codes)
        self.assertIn("predicate_mode_invalid", codes)

    def test_ancient_burn_claim_requires_direct_term_flag(self) -> None:
        bundle = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        bundle["entities"].append(
            {
                "key": "burn_outcome",
                "entity_type": "BurnPhenotype",
                "canonical_name": "烧伤创面",
            }
        )
        bundle["assertions"].append(
            {
                "subject": "formula_variant",
                "predicate": "TREATS",
                "object": "burn_outcome",
                "evidence": ["page_10"],
                "evidence_grade": "E1",
                "assertion_mode": "explicit",
                "confidence": 1.0,
                "review_status": "approved",
            }
        )
        report = validate_graph(build_bundle(bundle), release=True)
        codes = {value["code"] for value in report["issues"]}
        self.assertIn("burn_transfer_misclassified", codes)

    def test_formula_composition_requires_matching_ingredient_edges(self) -> None:
        bundle = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        bundle["assertions"] = [
            assertion
            for assertion in bundle["assertions"]
            if not (
                assertion["predicate"] == "HAS_INGREDIENT"
                and assertion["object"] == "herb_b"
            )
        ]
        report = validate_graph(build_bundle(bundle), release=True)
        self.assertIn(
            "formula_ingredient_edges_mismatch",
            {value["code"] for value in report["issues"]},
        )


if __name__ == "__main__":
    unittest.main()
