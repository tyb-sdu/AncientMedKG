from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from .automatic_loci import filter_loci_automatically
from .annotation import (
    finalize_annotation_adjudication,
    merge_annotation_reviews,
    prepare_annotation_batch,
    prepare_calibration_pilot,
    validate_annotation_batch,
)
from .compound_scoring import ScoringInputError, score_catalog
from .corpus_scan import scan_corpus
from .doctor import validate_discovery_intake
from .mechanism import MechanismInputError, analyze_mechanism
from .pubchem import PubChemResolutionError, resolve_catalog
from .reviewed_kg import build_reviewed_kg_bundle


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _resolve(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    result = resolve_catalog(
        _load(args.catalog),
        cache_dir=args.cache,
        delay_seconds=args.delay,
    )
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _scan(args: argparse.Namespace) -> int:
    result = scan_corpus(args.database, _load(args.catalog), args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _score(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    result = score_catalog(_load(args.input))
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _mechanism(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    result = analyze_mechanism(_load(args.input))
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _doctor(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    result = validate_discovery_intake(
        catalog_path=args.catalog,
        resolution_path=args.resolution,
        coverage_summary_path=args.coverage_summary,
        loci_path=args.loci,
        database_path=args.database,
        cache_dir=args.cache,
    )
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _prepare_review(args: argparse.Namespace) -> int:
    result = prepare_annotation_batch(
        loci_path=args.loci,
        coverage_summary_path=args.coverage_summary,
        catalog_path=args.catalog,
        output_dir=args.output,
        batch_size=args.batch_size,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _validate_review_batch(args: argparse.Namespace) -> int:
    result = validate_annotation_batch(
        args.manifest,
        parent_manifest_path=args.parent_manifest,
    )
    if args.output:
        _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _automatic_loci(args: argparse.Namespace) -> int:
    result = filter_loci_automatically(
        loci_path=args.loci,
        database_path=args.database,
        output_dir=args.output,
        threshold=args.threshold,
        policy_id=args.policy_id,
        approved_at=args.approved_at,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _prepare_calibration_pilot(args: argparse.Namespace) -> int:
    result = prepare_calibration_pilot(
        parent_manifest_path=args.parent_manifest,
        output_dir=args.output,
        batch_size=args.batch_size,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _merge_reviews(args: argparse.Namespace) -> int:
    result = merge_annotation_reviews(
        manifest_path=args.manifest,
        reviewer_a_path=args.reviewer_a,
        reviewer_b_path=args.reviewer_b,
        output_dir=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _finalize_review(args: argparse.Namespace) -> int:
    result = finalize_annotation_adjudication(
        batch_manifest_path=args.batch_manifest,
        agreement_report_path=args.agreement_report,
        adjudication_path=args.adjudication,
        output_dir=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _build_reviewed_kg(args: argparse.Namespace) -> int:
    bundle, result = build_reviewed_kg_bundle(
        finalization_report_path=args.finalization_report,
        catalog_path=args.catalog,
        modern_database_path=args.database,
        graph_version=args.graph_version,
        parent_version=args.parent_version,
    )
    _write(args.output, bundle)
    result["output"] = str(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        prog="python -m discovery_pipeline",
        description="Auditable compound screening and mechanism analysis.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser(
        "resolve-compounds", help="Resolve chemical identities using PubChem PUG REST."
    )
    resolve_parser.add_argument(
        "--catalog",
        type=Path,
        default=root / "data" / "compound_candidates_v1.json",
    )
    resolve_parser.add_argument("--output", type=Path, required=True)
    resolve_parser.add_argument("--cache", type=Path)
    resolve_parser.add_argument("--delay", type=float, default=0.2)
    resolve_parser.set_defaults(handler=_resolve)

    scan_parser = subparsers.add_parser(
        "scan-corpus", help="Create page-level compound evidence candidates from rag.db."
    )
    scan_parser.add_argument("--database", type=Path, required=True)
    scan_parser.add_argument(
        "--catalog",
        type=Path,
        default=root / "data" / "compound_candidates_v1.json",
    )
    scan_parser.add_argument("--output", type=Path, required=True)
    scan_parser.set_defaults(handler=_scan)

    automatic_loci_parser = subparsers.add_parser(
        "automatic-loci",
        help="Source-verify and threshold modern retrieval loci without a human gate.",
    )
    automatic_loci_parser.add_argument("--loci", type=Path, required=True)
    automatic_loci_parser.add_argument("--database", type=Path, required=True)
    automatic_loci_parser.add_argument("--output", type=Path, required=True)
    automatic_loci_parser.add_argument("--threshold", type=float, default=0.7)
    automatic_loci_parser.add_argument(
        "--policy-id", default="automatic-modern-locus-threshold-v1"
    )
    automatic_loci_parser.add_argument("--approved-at", default="")
    automatic_loci_parser.set_defaults(handler=_automatic_loci)

    score_parser = subparsers.add_parser(
        "score", help="Apply C0-C5 gates, R_compound, and sensitivity analysis."
    )
    score_parser.add_argument("--input", type=Path, required=True)
    score_parser.add_argument("--output", type=Path, required=True)
    score_parser.set_defaults(handler=_score)

    mechanism_parser = subparsers.add_parser(
        "mechanism", help="Build evidence-tiered targets, PPI modules, and enrichment."
    )
    mechanism_parser.add_argument("--input", type=Path, required=True)
    mechanism_parser.add_argument("--output", type=Path, required=True)
    mechanism_parser.set_defaults(handler=_mechanism)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Validate identity and corpus-intake artifacts end to end."
    )
    doctor_parser.add_argument(
        "--catalog",
        type=Path,
        default=root / "data" / "compound_candidates_v1.json",
    )
    doctor_parser.add_argument("--resolution", type=Path, required=True)
    doctor_parser.add_argument("--coverage-summary", type=Path, required=True)
    doctor_parser.add_argument("--loci", type=Path, required=True)
    doctor_parser.add_argument("--database", type=Path, required=True)
    doctor_parser.add_argument("--cache", type=Path)
    doctor_parser.add_argument("--output", type=Path, required=True)
    doctor_parser.set_defaults(handler=_doctor)

    prepare_parser = subparsers.add_parser(
        "prepare-review",
        help="Create a deterministic, stratified, blinded dual-review batch.",
    )
    prepare_parser.add_argument(
        "--catalog",
        type=Path,
        default=root / "data" / "compound_candidates_v1.json",
    )
    prepare_parser.add_argument("--coverage-summary", type=Path, required=True)
    prepare_parser.add_argument("--loci", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--batch-size", type=int, default=500)
    prepare_parser.add_argument(
        "--seed", default="rendongtang-dual-review-v1"
    )
    prepare_parser.set_defaults(handler=_prepare_review)

    pilot_parser = subparsers.add_parser(
        "prepare-calibration-pilot",
        help="Select a blinded, parent-verified calibration subset from a review batch.",
    )
    pilot_parser.add_argument("--parent-manifest", type=Path, required=True)
    pilot_parser.add_argument("--output", type=Path, required=True)
    pilot_parser.add_argument("--batch-size", type=int, default=50)
    pilot_parser.add_argument(
        "--seed", default="rendongtang-calibration-pilot-v1"
    )
    pilot_parser.set_defaults(handler=_prepare_calibration_pilot)

    validate_review_parser = subparsers.add_parser(
        "validate-review-batch",
        help="Rehash and independently validate a new blinded review batch.",
    )
    validate_review_parser.add_argument("--manifest", type=Path, required=True)
    validate_review_parser.add_argument("--parent-manifest", type=Path)
    validate_review_parser.add_argument("--output", type=Path)
    validate_review_parser.set_defaults(handler=_validate_review_batch)

    merge_parser = subparsers.add_parser(
        "merge-reviews",
        help="Validate two completed review sheets and create an adjudication queue.",
    )
    merge_parser.add_argument("--manifest", type=Path, required=True)
    merge_parser.add_argument("--reviewer-a", type=Path, required=True)
    merge_parser.add_argument("--reviewer-b", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    merge_parser.set_defaults(handler=_merge_reviews)

    finalize_parser = subparsers.add_parser(
        "finalize-review",
        help="Validate independent adjudication and release approved evidence records.",
    )
    finalize_parser.add_argument("--batch-manifest", type=Path, required=True)
    finalize_parser.add_argument("--agreement-report", type=Path, required=True)
    finalize_parser.add_argument("--adjudication", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.set_defaults(handler=_finalize_review)

    kg_parser = subparsers.add_parser(
        "build-reviewed-kg",
        help="Convert approved annotations into a source-verified draft KG overlay.",
    )
    kg_parser.add_argument(
        "--catalog",
        type=Path,
        default=root / "data" / "compound_candidates_v1.json",
    )
    kg_parser.add_argument("--finalization-report", type=Path, required=True)
    kg_parser.add_argument("--database", type=Path, required=True)
    kg_parser.add_argument("--graph-version", required=True)
    kg_parser.add_argument("--parent-version", default="")
    kg_parser.add_argument("--output", type=Path, required=True)
    kg_parser.set_defaults(handler=_build_reviewed_kg)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        FileExistsError,
        json.JSONDecodeError,
        MechanismInputError,
        OSError,
        PubChemResolutionError,
        ScoringInputError,
        ValueError,
    ) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
