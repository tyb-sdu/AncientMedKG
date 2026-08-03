from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge_graph.build import build_bundle_file
from knowledge_graph.export_neo4j import export_neo4j
from knowledge_graph.release import release_doctor
from knowledge_graph.source_verify import verify_graph_sources
from knowledge_graph.store import load_graph, write_graph, write_json
from knowledge_graph.validate import validate_graph
from research_pipeline.build_ancient_candidate_kg import (
    build_ancient_candidate_bundle,
)
from research_pipeline.promote_confident_candidates import (
    promote_confident_candidates,
)


class AutomaticPipelineError(ValueError):
    pass


def _require_valid(report: dict[str, Any], stage: str) -> None:
    if not report.get("valid"):
        raise AutomaticPipelineError(f"{stage} failed its validation gate")


def run_automatic_ancient_kg(
    *,
    database_path: Path,
    ontology_path: Path,
    formula_lexicon_path: Path,
    output_root: Path,
    candidate_graph_version: str,
    approved_graph_version: str,
    threshold: float = 0.7,
    policy_id: str = "automatic-confidence-threshold-v1",
    approved_at: str = "",
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite {output_root}")
    temporary_root = output_root.with_name(f".{output_root.name}.tmp")
    if temporary_root.exists():
        raise FileExistsError(f"temporary output already exists: {temporary_root}")
    temporary_root.parent.mkdir(parents=True, exist_ok=True)
    temporary_root.mkdir()
    if not approved_at:
        approved_at = datetime.now(timezone.utc).date().isoformat()

    try:
        candidate_bundle_path = temporary_root / "candidate_bundle.json"
        candidate_manifest_path = temporary_root / "candidate_manifest.jsonl"
        candidate_graph_dir = temporary_root / "candidate_graph"
        approved_graph_dir = temporary_root / "approved_graph"
        approval_report_path = temporary_root / "automatic_approval.json"
        candidate_source_path = temporary_root / "candidate_source_verification.json"
        approved_source_path = temporary_root / "approved_source_verification.json"
        neo4j_dir = temporary_root / "neo4j"
        doctor_path = temporary_root / "release_doctor.json"

        _, candidate_build = build_ancient_candidate_bundle(
            database_path=database_path,
            ontology_path=ontology_path,
            formula_lexicon_path=formula_lexicon_path,
            output_bundle_path=candidate_bundle_path,
            output_manifest_path=candidate_manifest_path,
            graph_version=candidate_graph_version,
        )
        candidate_graph = build_bundle_file(candidate_bundle_path)
        candidate_validation = validate_graph(candidate_graph, release=False)
        _require_valid(candidate_validation, "candidate graph")
        candidate_manifest = write_graph(
            candidate_graph,
            candidate_graph_dir,
            validation_report=candidate_validation,
        )
        candidate_source = verify_graph_sources(
            load_graph(candidate_graph_dir), ancient_database=database_path
        )
        _require_valid(candidate_source, "candidate source verification")
        write_json(candidate_source_path, candidate_source)

        approval = promote_confident_candidates(
            input_graph_dir=candidate_graph_dir,
            output_graph_dir=approved_graph_dir,
            output_report_path=approval_report_path,
            graph_version=approved_graph_version,
            threshold=threshold,
            policy_id=policy_id,
            approved_at=approved_at,
        )
        approved_graph = load_graph(approved_graph_dir)
        approved_source = verify_graph_sources(
            approved_graph, ancient_database=database_path
        )
        _require_valid(approved_source, "approved source verification")
        write_json(approved_source_path, approved_source)
        neo4j_manifest = export_neo4j(approved_graph, neo4j_dir)
        doctor = release_doctor(
            approved_graph_dir,
            neo4j_dir=neo4j_dir,
            ancient_database=database_path,
        )
        _require_valid(doctor, "release doctor")
        write_json(doctor_path, doctor)

        summary = {
            "valid": True,
            "pipeline": "automatic-ancient-kg-v1",
            "human_review_required": False,
            "human_reviewed": False,
            "threshold": threshold,
            "policy_id": policy_id,
            "approved_at": approved_at,
            "database_sha256": candidate_build["database_sha256_before"],
            "source_database_unchanged": candidate_build[
                "source_database_unchanged"
            ],
            "candidate": {
                "graph_version": candidate_graph_version,
                "selected_pages": candidate_build["selected_pages"],
                "formula_candidates": candidate_build["formula_candidates"],
                "counts": candidate_manifest["counts"],
                "content_fingerprint": candidate_manifest["content_fingerprint"],
                "source_status_counts": candidate_source["status_counts"],
            },
            "approved": {
                "graph_version": approved_graph_version,
                "counts": approval["approved"],
                "discarded": approval["discarded"],
                "source_status_counts": approved_source["status_counts"],
                "content_fingerprint": approval[
                    "manifest_content_fingerprint"
                ],
            },
            "neo4j": {
                "counts": neo4j_manifest["counts"],
                "content_fingerprint": neo4j_manifest["content_fingerprint"],
            },
            "release_doctor_valid": True,
            "artifacts": {
                "candidate_bundle": "candidate_bundle.json",
                "candidate_manifest": "candidate_manifest.jsonl",
                "candidate_graph": "candidate_graph",
                "candidate_source_verification": "candidate_source_verification.json",
                "approved_graph": "approved_graph",
                "automatic_approval": "automatic_approval.json",
                "approved_source_verification": "approved_source_verification.json",
                "neo4j": "neo4j",
                "release_doctor": "release_doctor.json",
            },
        }
        write_json(temporary_root / "pipeline_report.json", summary)
        os.replace(temporary_root, output_root)
        return summary
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Build, threshold, source-verify, export, and release an ancient KG "
            "without a human review gate"
        )
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--ontology",
        type=Path,
        default=root / "data" / "burn_ontology_v1.json",
    )
    parser.add_argument(
        "--formula-lexicon",
        type=Path,
        default=root / "data" / "formula_herb_lexicon_v1.json",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--candidate-graph-version", required=True)
    parser.add_argument("--approved-graph-version", required=True)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument(
        "--policy-id", default="automatic-confidence-threshold-v1"
    )
    parser.add_argument("--approved-at", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_automatic_ancient_kg(
        database_path=args.database,
        ontology_path=args.ontology,
        formula_lexicon_path=args.formula_lexicon,
        output_root=args.output_root,
        candidate_graph_version=args.candidate_graph_version,
        approved_graph_version=args.approved_graph_version,
        threshold=args.threshold,
        policy_id=args.policy_id,
        approved_at=args.approved_at,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
