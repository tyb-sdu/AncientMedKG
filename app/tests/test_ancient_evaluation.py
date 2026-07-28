from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_ancient_retrieval import load_questions, validate_question_schema


def test_ancient_question_set_has_fixed_evidence_labels() -> None:
    questions = load_questions(ROOT / "evaluation" / "ancient_questions_v1.json")
    assert len(questions) >= 50
    assert sum(item["expect_answer"] for item in questions) >= 40
    assert sum(not item["expect_answer"] for item in questions) >= 6
    assert validate_question_schema(questions) == []
