from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_EVIDENCE = Path(__file__).parent / "data" / "rendongtang_evidence_v1.json"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _minimal_quote(text: str, evidence_terms: list[str]) -> str:
    spans: list[tuple[int, int]] = []
    missing: list[str] = []
    for term in evidence_terms:
        start = text.find(term)
        if start < 0:
            missing.append(term)
        else:
            spans.append((start, start + len(term)))
    if missing:
        raise ValueError(f"evidence terms are absent from source page: {missing}")
    if not spans:
        raise ValueError("at least one evidence term is required")
    return text[min(start for start, _ in spans) : max(end for _, end in spans)]


def _dose_parts(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)(兩|錢|分|厘|枚|片|升|合)", value.strip())
    return (match.group(1), match.group(2)) if match else (value.strip(), "")


def _disease_name(indication: str) -> str:
    for name in ("内外痈肿", "杨梅结毒", "胃脘痈"):
        if name in indication:
            return name
    return indication.strip(" ，。")


def build_kg_evidence_bundle(
    evidence_package: dict[str, Any],
    ancient_database: Path,
    *,
    evidence_input_sha256: str = "",
) -> dict[str, Any]:
    work = dict(evidence_package["work"])
    book_id = str(work["book_id"])
    with _open_read_only(ancient_database) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise ValueError(f"ancient database quick_check failed: {quick_check}")
        source = connection.execute(
            """
            SELECT book_id, title, filename, source_sha256
            FROM books WHERE book_id = ?
            """,
            (book_id,),
        ).fetchone()
        if source is None:
            raise ValueError(f"book_id not found in ancient database: {book_id}")
        if str(source["title"]) != str(work["title"]):
            raise ValueError(
                f"title mismatch for {book_id}: {source['title']!r} != {work['title']!r}"
            )

        locus_specs: list[tuple[str, dict[str, Any], str]] = []
        for instance in evidence_package["formula_instances"]:
            locus_specs.append(
                (
                    str(instance["formula_instance_id"]),
                    dict(instance["source_locus"]),
                    str(instance["review_status"]),
                )
            )
        for context in evidence_package.get("context_loci", []):
            locus_specs.append(
                (
                    str(context["context_id"]),
                    dict(context),
                    "text_verified_requires_final_image_signoff",
                )
            )

        pages: dict[int, dict[str, Any]] = {}
        for owner_id, locus, upstream_review in locus_specs:
            page = int(locus["physical_page"])
            row = connection.execute(
                """
                SELECT page_id, book_id, physical_page, text
                FROM pages WHERE book_id = ? AND physical_page = ?
                """,
                (book_id, page),
            ).fetchone()
            if row is None:
                raise ValueError(f"source page is missing: {book_id} page {page}")
            text = str(row["text"] or "")
            terms = [str(value) for value in locus["evidence_terms"]]
            pages[page] = {
                "owner_id": owner_id,
                "page_id": str(row["page_id"]),
                "physical_page": page,
                "text": text,
                "page_text_sha256": _sha256_text(text),
                "quote": _minimal_quote(text, terms),
                "evidence_terms": terms,
                "upstream_review_status": upstream_review,
            }

    source_key = "yixuexinwu_scan"
    research_source_key = "rendongtang_research_rules"
    graph_version = f"rendongtang-{evidence_package['version']}-draft"
    bundle: dict[str, Any] = {
        "schema_version": "1.0.0",
        "bundle_id": str(evidence_package["evidence_package_id"]),
        "graph_version": graph_version,
        "metadata": {
            "description": "忍冬汤同名异方与烧伤机制迁移的待复核真实证据草案",
            "parent_version": None,
            "review_gate": "image_double_signoff_required",
            "release_approved": False,
            "ancient_database_sha256": _sha256_file(ancient_database),
            "evidence_input_sha256": evidence_input_sha256,
            "scientific_boundary": evidence_package["claim_boundary"],
        },
        "sources": [
            {
                "key": source_key,
                "source_type": "ancient_pdf",
                "title": str(source["title"]),
                "file_name": str(source["filename"]),
                "file_sha256": str(source["source_sha256"]),
                "work_id": str(work["work_id"]),
                "edition_id": str(work["edition_id"]),
                "attributes": {
                    "book_id": book_id,
                    "source_status": str(work["source_status"]),
                },
            },
            {
                "key": research_source_key,
                "source_type": "curated_ontology",
                "title": "忍冬汤烧伤迁移证据规则V1",
                "file_name": "rendongtang_evidence_v1.json",
                "file_sha256": evidence_input_sha256,
                "attributes": {
                    "evidence_package_id": evidence_package["evidence_package_id"],
                    "status": "hypothesis_unvalidated",
                },
            },
        ],
        "entities": [],
        "evidence": [],
        "assertions": [],
    }
    entities: list[dict[str, Any]] = bundle["entities"]
    evidence: list[dict[str, Any]] = bundle["evidence"]
    assertions: list[dict[str, Any]] = bundle["assertions"]

    work_name = str(work["title"]).split("_", 1)[0]
    entities.extend(
        [
            {
                "key": "work",
                "entity_type": "ClassicWork",
                "canonical_name": work_name,
                "identity": {"work_id": work["work_id"]},
            },
            {
                "key": "edition",
                "entity_type": "Edition",
                "canonical_name": str(work["title"]),
                "identity": {"edition_id": work["edition_id"]},
            },
            {
                "key": "formula_concept",
                "entity_type": "FormulaConcept",
                "canonical_name": str(evidence_package["normalized_formula_name"]),
                "aliases": ["忍冬湯"],
            },
            {
                "key": "method_decoction_oral",
                "entity_type": "TreatmentMethod",
                "canonical_name": "水煎内服",
                "aliases": ["水煎頓服", "水煎服"],
            },
            {
                "key": "burn_phenotype",
                "entity_type": "BurnPhenotype",
                "canonical_name": "烧伤创面炎症与感染风险",
                "attributes": {
                    "evidence_channel": "mechanism_transfer_only",
                    "direct_ancient_term": False,
                },
            },
        ]
    )

    page_evidence_keys: dict[int, str] = {}
    passage_keys: dict[int, str] = {}
    for page, record in sorted(pages.items()):
        passage_key = f"passage_{page}"
        evidence_key = f"page_{page}"
        passage_keys[page] = passage_key
        page_evidence_keys[page] = evidence_key
        locator = {
            "book_id": book_id,
            "page_id": record["page_id"],
            "physical_page": page,
            "page_text_sha256": record["page_text_sha256"],
        }
        entities.append(
            {
                "key": passage_key,
                "entity_type": "Passage",
                "canonical_name": f"{work_name}第{page}页",
                "attributes": {"source_id": source_key, "locator": locator},
            }
        )
        evidence.append(
            {
                "key": evidence_key,
                "source": source_key,
                "locator": locator,
                "quote": record["quote"],
                "evidence_grade": "E1",
                "evidence_class": "direct_ancient",
                "review": {
                    "status": "pending",
                    "workflow": "image_double_signoff",
                },
                "attributes": {
                    "evidence_terms": record["evidence_terms"],
                    "upstream_review_status": record["upstream_review_status"],
                    "release_approved": False,
                },
            }
        )

    transfer_quote = str(evidence_package["claim_boundary"]["allowed_current_wording"])
    evidence.append(
        {
            "key": "transfer_hypothesis",
            "source": research_source_key,
            "locator": {
                "rule_id": evidence_package["transfer_scoring_template"]["score_id"],
                "evidence_package_id": evidence_package["evidence_package_id"],
            },
            "quote": transfer_quote,
            "evidence_grade": "E5",
            "evidence_class": "modern_bridge",
            "review": {"status": "pending", "workflow": "expert_transfer_review"},
            "attributes": {
                "status": evidence_package["transfer_scoring_template"]["status"],
                "required_control_paths": evidence_package["transfer_scoring_template"][
                    "required_control_paths"
                ],
                "direct_ancient_evidence": False,
            },
        }
    )

    def add_assertion(
        subject: str,
        predicate: str,
        object_key: str,
        evidence_keys: list[str],
        *,
        grade: str = "E1",
        mode: str = "explicit",
        confidence: float = 0.95,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        assertions.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": object_key,
                "evidence": evidence_keys,
                "evidence_grade": grade,
                "assertion_mode": mode,
                "confidence": confidence,
                "review_status": "pending",
                "attributes": attributes or {},
            }
        )

    first_evidence = page_evidence_keys[min(page_evidence_keys)]
    add_assertion("work", "HAS_EDITION", "edition", [first_evidence])
    for page in sorted(pages):
        add_assertion(
            "edition", "HAS_PASSAGE", passage_keys[page], [page_evidence_keys[page]]
        )

    herb_keys: dict[str, str] = {}
    variant_keys: dict[str, str] = {}
    disease_keys: dict[str, str] = {}
    neiyong_variant_key: str | None = None
    neiyong_disease_key: str | None = None
    for index, instance in enumerate(
        sorted(
            evidence_package["formula_instances"],
            key=lambda value: int(value["source_locus"]["physical_page"]),
        ),
        start=1,
    ):
        page = int(instance["source_locus"]["physical_page"])
        evidence_key = page_evidence_keys[page]
        passage_key = passage_keys[page]
        variant_key = f"formula_variant_{index}"
        variant_keys[str(instance["formula_instance_id"])] = variant_key
        disease_name = _disease_name(str(instance["indication_normalized"]))
        disease_key = disease_keys.setdefault(disease_name, f"disease_{len(disease_keys) + 1}")
        if not any(entity.get("key") == disease_key for entity in entities):
            entities.append(
                {
                    "key": disease_key,
                    "entity_type": "Disease",
                    "canonical_name": disease_name,
                    "aliases": [str(instance["indication_original"])],
                }
            )

        composition: list[dict[str, str]] = []
        for ingredient in instance["ingredients"]:
            name = str(ingredient["normalized_name"])
            herb_key = herb_keys.setdefault(name, f"herb_{len(herb_keys) + 1}")
            if not any(entity.get("key") == herb_key for entity in entities):
                entities.append(
                    {
                        "key": herb_key,
                        "entity_type": "Herb",
                        "canonical_name": name,
                        "aliases": [str(ingredient["name_original"])],
                    }
                )
            dose_value, dose_unit = _dose_parts(str(ingredient["dose_original"]))
            composition.append(
                {
                    "herb": name,
                    "dose_value": dose_value,
                    "dose_unit": dose_unit,
                    "dose_text_original": str(ingredient["dose_original"]),
                }
            )

        entities.append(
            {
                "key": variant_key,
                "entity_type": "FormulaVariant",
                "canonical_name": str(instance["normalized_name"]),
                "aliases": [str(instance["name_original"])],
                "identity": {"formula_instance_id": instance["formula_instance_id"]},
                "attributes": {
                    "formula_name": str(instance["name_original"]),
                    "composition": composition,
                    "source_locator": {
                        "source_id": source_key,
                        "book_id": book_id,
                        "page_id": pages[page]["page_id"],
                        "physical_page": page,
                    },
                    "indication_original": instance["indication_original"],
                    "preparation_original": instance["preparation_original"],
                    "administration_route": instance["administration_route"],
                    "direct_burn_evidence": False,
                    "upstream_review_status": instance["review_status"],
                    "release_approved": False,
                },
            }
        )
        add_assertion(variant_key, "VARIANT_OF", "formula_concept", [evidence_key])
        add_assertion(variant_key, "RECORDED_IN", passage_key, [evidence_key])
        add_assertion(disease_key, "RECORDED_IN", passage_key, [evidence_key])
        add_assertion(variant_key, "TREATS", disease_key, [evidence_key])
        add_assertion(
            disease_key,
            "HAS_TREATMENT_METHOD",
            "method_decoction_oral",
            [evidence_key],
        )
        add_assertion(
            "method_decoction_oral",
            "REPRESENTATIVE_FORMULA",
            variant_key,
            [evidence_key],
        )
        for ingredient, composition_item in zip(
            instance["ingredients"], composition, strict=True
        ):
            herb_key = herb_keys[str(ingredient["normalized_name"])]
            add_assertion(
                variant_key,
                "HAS_INGREDIENT",
                herb_key,
                [evidence_key],
                attributes={
                    "dose_value": composition_item["dose_value"],
                    "dose_unit": composition_item["dose_unit"],
                    "dose_text_original": composition_item["dose_text_original"],
                },
            )
            add_assertion(herb_key, "RECORDED_IN", passage_key, [evidence_key])

        if disease_name == "内外痈肿":
            neiyong_variant_key = variant_key
            neiyong_disease_key = disease_key

    for context in evidence_package.get("context_loci", []):
        if "胃脘癰" not in "".join(context["evidence_terms"]):
            continue
        page = int(context["physical_page"])
        disease_key = "disease_weiwanyong"
        entities.append(
            {
                "key": disease_key,
                "entity_type": "Disease",
                "canonical_name": "胃脘痈",
                "aliases": ["胃脘癰"],
            }
        )
        add_assertion(
            disease_key,
            "RECORDED_IN",
            passage_keys[page],
            [page_evidence_keys[page]],
        )
        if neiyong_variant_key:
            add_assertion(
                neiyong_variant_key,
                "TREATS",
                disease_key,
                [page_evidence_keys[page]],
            )

    if not neiyong_variant_key or not neiyong_disease_key:
        raise ValueError("the inner/external abscess Rendongtang variant was not resolved")
    transfer_evidence = [
        "transfer_hypothesis",
        page_evidence_keys[
            next(
                int(value["source_locus"]["physical_page"])
                for value in evidence_package["formula_instances"]
                if "内外痈肿" in str(value["indication_normalized"])
            )
        ],
    ]
    transfer_attributes = {
        "status": "hypothesis_unvalidated",
        "direct_ancient_evidence": False,
        "transfer_score_status": evidence_package["transfer_scoring_template"][
            "status"
        ],
    }
    add_assertion(
        neiyong_variant_key,
        "MECHANISM_TRANSFER",
        "burn_phenotype",
        transfer_evidence,
        grade="E5",
        mode="hypothesis",
        confidence=0.25,
        attributes=transfer_attributes,
    )
    add_assertion(
        neiyong_disease_key,
        "MECHANISM_TRANSFER",
        "burn_phenotype",
        transfer_evidence,
        grade="E5",
        mode="hypothesis",
        confidence=0.2,
        attributes=transfer_attributes,
    )
    return bundle


def build_bundle_file(
    evidence_path: Path, ancient_database: Path, output_path: Path
) -> dict[str, Any]:
    package = json.loads(evidence_path.read_text(encoding="utf-8"))
    bundle = build_kg_evidence_bundle(
        package,
        ancient_database,
        evidence_input_sha256=_sha256_file(evidence_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a traceable draft KG bundle from the Rendongtang evidence package"
    )
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle = build_bundle_file(args.evidence, args.database, args.output)
    summary = {
        "output": str(args.output),
        "bundle_id": bundle["bundle_id"],
        "graph_version": bundle["graph_version"],
        "sources": len(bundle["sources"]),
        "entities": len(bundle["entities"]),
        "evidence": len(bundle["evidence"]),
        "assertions": len(bundle["assertions"]),
        "review_status": "pending",
        "release_approved": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
