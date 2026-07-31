from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .build import BundleError, build_bundle_file
from .export_neo4j import export_neo4j
from .release import release_doctor
from .schema import load_schema
from .source_verify import verify_graph_sources
from .store import load_graph, write_graph, write_json
from .validate import validate_graph


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _build_command(args: argparse.Namespace) -> int:
    graph = build_bundle_file(args.input)
    report = validate_graph(graph, release=args.release)
    if not report["valid"] and not args.allow_invalid:
        _print_json(report)
        return 2
    manifest = write_graph(graph, args.output, validation_report=report)
    _print_json({"validation": report, "manifest": manifest})
    return 0 if report["valid"] else 2


def _validate_command(args: argparse.Namespace) -> int:
    graph = load_graph(args.graph)
    report = validate_graph(graph, release=args.release)
    if args.output:
        write_json(args.output, report)
    _print_json(report)
    return 0 if report["valid"] else 2


def _export_command(args: argparse.Namespace) -> int:
    graph = load_graph(args.graph)
    report = validate_graph(graph, release=not args.allow_unreleased)
    if not report["valid"]:
        _print_json(report)
        return 2
    manifest = export_neo4j(graph, args.output)
    _print_json(manifest)
    return 0


def _verify_sources_command(args: argparse.Namespace) -> int:
    graph = load_graph(args.graph)
    report = verify_graph_sources(
        graph,
        ancient_database=args.ancient_database,
        modern_database=args.modern_database,
    )
    if args.output:
        write_json(args.output, report)
    _print_json(report)
    return 0 if report["valid"] else 2


def _schema_command(args: argparse.Namespace) -> int:
    _print_json(load_schema())
    return 0


def _doctor_command(args: argparse.Namespace) -> int:
    report = release_doctor(
        args.graph,
        neo4j_dir=args.neo4j,
        ancient_database=args.ancient_database,
        modern_database=args.modern_database,
    )
    if args.output:
        write_json(args.output, report)
    _print_json(report)
    return 0 if report["valid"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_graph",
        description="Build and release the evidence-first AncientMedKG graph.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build", help="Build immutable JSONL graph files from an evidence bundle."
    )
    build_parser.add_argument("--input", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument(
        "--release",
        action="store_true",
        help="Apply release gates, including approved-review requirements.",
    )
    build_parser.add_argument(
        "--allow-invalid",
        action="store_true",
        help="Write an invalid draft for diagnostics; never use for a release.",
    )
    build_parser.set_defaults(handler=_build_command)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate a built graph and verify its manifest hashes."
    )
    validate_parser.add_argument("--graph", type=Path, required=True)
    validate_parser.add_argument("--output", type=Path)
    validate_parser.add_argument("--release", action="store_true")
    validate_parser.set_defaults(handler=_validate_command)

    export_parser = subparsers.add_parser(
        "export-neo4j", help="Create Neo4j CSV, Cypher, JSON-LD, and SHA manifest."
    )
    export_parser.add_argument("--graph", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument(
        "--allow-unreleased",
        action="store_true",
        help="Permit export of a structurally valid draft with pending reviews.",
    )
    export_parser.set_defaults(handler=_export_command)

    verify_parser = subparsers.add_parser(
        "verify-sources",
        help="Resolve graph evidence back to immutable ancient and modern SQLite rows.",
    )
    verify_parser.add_argument("--graph", type=Path, required=True)
    verify_parser.add_argument("--ancient-database", type=Path)
    verify_parser.add_argument("--modern-database", type=Path)
    verify_parser.add_argument("--output", type=Path)
    verify_parser.set_defaults(handler=_verify_sources_command)

    schema_parser = subparsers.add_parser("schema", help="Print the active graph schema.")
    schema_parser.set_defaults(handler=_schema_command)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Apply the aggregate graph, source, and Neo4j release gate.",
    )
    doctor_parser.add_argument("--graph", type=Path, required=True)
    doctor_parser.add_argument("--neo4j", type=Path, required=True)
    doctor_parser.add_argument("--ancient-database", type=Path)
    doctor_parser.add_argument("--modern-database", type=Path)
    doctor_parser.add_argument("--output", type=Path)
    doctor_parser.set_defaults(handler=_doctor_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (BundleError, FileExistsError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
