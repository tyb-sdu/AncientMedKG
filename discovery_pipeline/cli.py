from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from .compound_scoring import ScoringInputError, score_catalog
from .corpus_scan import scan_corpus
from .doctor import validate_discovery_intake
from .mechanism import MechanismInputError, analyze_mechanism
from .pubchem import PubChemResolutionError, resolve_catalog


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
