from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_pipeline.database_evidence import verify_database_evidence
from research_pipeline.validation import load_json, validate_asset_bundle


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
EVALUATION = ROOT / "evaluation"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate proposal-aligned research assets without modifying the RAG corpus"
    )
    parser.add_argument("--ontology", type=Path, default=DATA / "burn_ontology_v1.json")
    parser.add_argument(
        "--evidence", type=Path, default=DATA / "rendongtang_evidence_v1.json"
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=EVALUATION / "rendongtang_questions_v1.json",
    )
    parser.add_argument(
        "--compliance", type=Path, default=DATA / "proposal_compliance_v1.json"
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="Optional ancient SQLite database for read-only locus verification",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict[str, Any]:
    evidence = load_json(args.evidence)
    report = validate_asset_bundle(
        load_json(args.ontology),
        evidence,
        load_json(args.questions),
        load_json(args.compliance),
    )
    if args.database:
        database_report = verify_database_evidence(args.database, evidence)
        report["database_evidence"] = database_report
        if not database_report["valid"]:
            report["valid"] = False
            report["issues"].extend(
                {
                    "asset": "database_evidence",
                    "message": json.dumps(issue, ensure_ascii=False),
                }
                for issue in database_report["issues"]
            )
    return report


def main() -> int:
    args = parse_args()
    report = run(args)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
