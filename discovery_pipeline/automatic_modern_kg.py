from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from knowledge_graph.build import build_bundle
from knowledge_graph.store import write_json
from knowledge_graph.validate import validate_graph


class AutomaticModernGraphError(ValueError):
    pass


OUTCOME_NAMES = {
    "wound_closure": "创面闭合",
    "wound_healing": "创面修复",
    "inflammation": "炎症反应",
    "oxidative_stress": "氧化应激",
    "collagen_deposition": "胶原沉积",
    "angiogenesis": "血管生成",
    "antibacterial": "抗菌效应",
    "re_epithelialization": "再上皮化",
    "scar": "瘢痕形成",
    "pain": "疼痛",
}
PHENOTYPE_NAMES = {
    "wound_closure": "烧伤创面闭合",
    "wound_healing": "烧伤创面修复",
    "inflammation": "烧伤炎症反应",
    "oxidative_stress": "烧伤氧化应激",
    "collagen_deposition": "创面基质重建",
    "angiogenesis": "创面血管生成",
    "antibacterial": "烧伤创面感染控制",
    "re_epithelialization": "烧伤创面再上皮化",
    "scar": "烧伤后瘢痕",
    "pain": "烧伤疼痛",
}
PATHWAY_NAMES = {
    "pathway:nfkb": "NF-kappa B signaling",
    "pathway:nrf2_ho1": "Nrf2/HO-1 signaling",
    "pathway:pi3k_akt": "PI3K/AKT signaling",
    "pathway:mapk": "MAPK signaling",
    "pathway:tlr4_myd88": "TLR4/MyD88 signaling",
    "pathway:tgfb_smad": "TGF-beta/Smad signaling",
    "pathway:vegf": "VEGF signaling",
    "pathway:nlrp3": "NLRP3 inflammasome",
}
SAFETY_NAMES = {
    "hypertension": "高血压",
    "hypokalemia": "低钾血症",
    "sodium_retention": "钠水潴留",
    "pseudoaldosteronism": "假性醛固酮增多症",
    "arrhythmia": "心律失常",
    "drug_interaction": "药物相互作用",
    "cytotoxicity": "细胞毒性",
    "general_toxicity": "一般毒性或不良反应",
}
FORMULATION_NAMES = {
    "hydrogel": "水凝胶",
    "wound_dressing": "创面敷料",
    "liposome": "脂质体",
    "nanofiber": "纳米纤维",
    "film": "创面薄膜",
}
ORIGINAL_STUDIES = {"randomized_trial", "controlled_clinical", "animal", "in_vitro"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if value.get("review_status") != "approved":
                    raise AutomaticModernGraphError("structured input contains non-approved row")
                result.append(value)
    if not result:
        raise AutomaticModernGraphError("structured evidence input is empty")
    return result


def _load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = {str(row["candidate_id"]): dict(row) for row in payload.get("candidates", [])}
    if not values or len(values) != len(payload.get("candidates", [])):
        raise AutomaticModernGraphError("compound catalog is empty or duplicated")
    return values


def _grade(study_type: str) -> tuple[str, str]:
    if study_type in ORIGINAL_STUDIES:
        return "E1", "experimental"
    if study_type == "systematic_review":
        return "E2", "authoritative_curated"
    if study_type == "computational":
        return "E4", "database_prediction"
    return "E4", "modern_bridge"


def build_automatic_modern_bundle(
    *,
    structured_evidence_path: Path,
    catalog_path: Path,
    database_path: Path,
    graph_version: str,
    policy_id: str = "automatic-modern-structure-v1",
    threshold: float = 0.7,
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = _read_jsonl(structured_evidence_path)
    catalog = _load_catalog(catalog_path)
    database_sha_before = _sha256_file(database_path)
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    sources: dict[str, dict[str, Any]] = {}
    entities: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    assertions: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    chain_counts: Counter[str] = Counter()
    chain_examples: list[dict[str, Any]] = []
    compound_records: Counter[str] = Counter()
    compound_sources: defaultdict[str, set[str]] = defaultdict(set)
    compound_targets: defaultdict[str, set[str]] = defaultdict(set)
    compound_pathways: defaultdict[str, set[str]] = defaultdict(set)
    compound_outcomes: defaultdict[str, set[str]] = defaultdict(set)
    compound_safety: defaultdict[str, set[str]] = defaultdict(set)
    compound_formulations: defaultdict[str, set[str]] = defaultdict(set)
    compound_direct_relations: defaultdict[str, Counter[str]] = defaultdict(Counter)

    def add_entity(key: str, value: dict[str, Any]) -> None:
        prior = entities.setdefault(key, value)
        if prior != value:
            raise AutomaticModernGraphError(f"entity collision: {key}")

    def add_edge(
        subject: str,
        predicate: str,
        object_key: str,
        evidence_key: str,
        *,
        grade: str,
        mode: str,
        confidence: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        identity = (subject, predicate, object_key, mode)
        row = assertions.get(identity)
        if row is None:
            row = {
                "subject": subject,
                "predicate": predicate,
                "object": object_key,
                "evidence": [],
                "evidence_grade": grade,
                "assertion_mode": mode,
                "confidence": confidence,
                "review_status": "approved",
                "attributes": {
                    "automatic_approval_policy": policy_id,
                    "automatic_approval_threshold": threshold,
                    "human_reviewed": False,
                    **(attributes or {}),
                },
            }
            assertions[identity] = row
        row["evidence"] = sorted({*row["evidence"], evidence_key})
        row["confidence"] = max(float(row["confidence"]), confidence)

    def ensure_compound(candidate_id: str) -> None:
        if candidate_id not in catalog:
            raise AutomaticModernGraphError(f"unknown compound: {candidate_id}")
        compound = catalog[candidate_id]
        attributes = {
            "candidate_role": str(compound.get("candidate_role", "")),
            "herb_ids": list(compound.get("herb_ids", [])),
        }
        for relation_field in ("metabolite_of", "salt_of"):
            if compound.get(relation_field):
                attributes[relation_field] = str(compound[relation_field])
        add_entity(
            candidate_id,
            {
                "key": candidate_id,
                "entity_type": "Compound",
                "canonical_name": str(compound["canonical_name"]),
                "aliases": [
                    value
                    for value in [compound.get("name_zh", ""), *compound.get("aliases", [])]
                    if value
                ],
                "identity": {"candidate_id": candidate_id},
                "external_ids": (
                    {"pubchem_cid": str(compound["expected_pubchem_cid"])}
                    if compound.get("expected_pubchem_cid")
                    else {}
                ),
                "attributes": attributes,
            },
        )

    try:
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise AutomaticModernGraphError("modern database quick_check failed")
        for record in sorted(records, key=lambda value: str(value["locus_id"])):
            confidence = float(record["semantic_confidence"])
            if confidence < threshold:
                raise AutomaticModernGraphError("approved structured confidence below threshold")
            candidate_id = str(record["candidate_id"])
            if candidate_id not in catalog:
                raise AutomaticModernGraphError(f"unknown compound: {candidate_id}")
            row = connection.execute(
                """
                SELECT c.chunk_id, c.doc_id, c.pdf_page, c.text,
                       d.title, d.year, d.doi, d.source_filename, d.sha256
                FROM chunks c JOIN documents d USING(doc_id)
                WHERE c.chunk_id = ?
                """,
                (str(record["chunk_id"]),),
            ).fetchone()
            if row is None:
                raise AutomaticModernGraphError(f"source chunk missing: {record['chunk_id']}")
            text = str(row["text"] or "")
            chunk_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if chunk_sha != str(record["chunk_text_sha256"]):
                raise AutomaticModernGraphError(f"chunk SHA differs: {record['locus_id']}")
            doc_id = str(row["doc_id"])
            source_key = f"source:{doc_id}"
            study_key = f"study:{doc_id}"
            evidence_key = f"evidence:{record['locus_id']}"
            sources[source_key] = {
                "key": source_key,
                "source_type": "modern_pdf",
                "title": str(row["title"] or ""),
                "file_name": str(row["source_filename"] or ""),
                "file_sha256": str(row["sha256"] or ""),
                "doi": str(row["doi"] or ""),
                "year": str(row["year"] or ""),
                "attributes": {"doc_id": doc_id},
            }
            fields = dict(record["structured_fields"])
            grade, evidence_class = _grade(str(fields["study_type"]))
            compound_records[candidate_id] += 1
            compound_sources[candidate_id].add(doc_id)
            compound_targets[candidate_id].update(fields.get("targets", []))
            compound_pathways[candidate_id].update(fields.get("pathways", []))
            compound_outcomes[candidate_id].update(fields.get("outcomes", []))
            compound_safety[candidate_id].update(fields.get("safety_signals", []))
            compound_formulations[candidate_id].update(fields.get("formulations", []))
            compound_direct_relations[candidate_id].update(
                value["predicate"] for value in fields.get("direct_target_relations", [])
            )
            evidence[evidence_key] = {
                "key": evidence_key,
                "source": source_key,
                "locator": {
                    "doc_id": doc_id,
                    "chunk_id": str(row["chunk_id"]),
                    "pdf_page": int(row["pdf_page"]),
                    "locus_id": str(record["locus_id"]),
                    "chunk_text_sha256": chunk_sha,
                },
                "quote": text,
                "evidence_grade": grade,
                "evidence_class": evidence_class,
                "review": {
                    "status": "approved",
                    "reviewer": policy_id,
                    "human_reviewed": False,
                    "mode": "automatic_confidence_threshold",
                    "confidence": confidence,
                },
                "attributes": {
                    "locus_id": str(record["locus_id"]),
                    "structured_fields": fields,
                    "semantic_confidence": confidence,
                },
            }
            ensure_compound(candidate_id)
            add_entity(
                study_key,
                {
                    "key": study_key,
                    "entity_type": "Study",
                    "canonical_name": str(row["title"] or doc_id),
                    "identity": {"doc_id": doc_id},
                    "external_ids": ({"doi": str(row["doi"])} if row["doi"] else {}),
                    "attributes": {
                        "doc_id": doc_id,
                        "year": str(row["year"] or ""),
                    },
                },
            )
            add_edge(
                candidate_id,
                "STUDIED_IN",
                study_key,
                evidence_key,
                grade=grade,
                mode="explicit",
                confidence=confidence,
                attributes={"claim_scope": "exact compound mention in source study"},
            )

            outcome_keys: list[str] = []
            for outcome in fields.get("outcomes", []):
                outcome_key = f"outcome:{outcome}"
                phenotype_key = f"phenotype:{outcome}"
                outcome_keys.append(outcome_key)
                add_entity(
                    outcome_key,
                    {
                        "key": outcome_key,
                        "entity_type": "Outcome",
                        "canonical_name": OUTCOME_NAMES[outcome],
                        "identity": {"outcome_id": outcome},
                        "attributes": {"outcome_id": outcome},
                    },
                )
                add_entity(
                    phenotype_key,
                    {
                        "key": phenotype_key,
                        "entity_type": "BurnPhenotype",
                        "canonical_name": PHENOTYPE_NAMES[outcome],
                        "identity": {"phenotype_id": outcome},
                    },
                )
                add_edge(
                    study_key,
                    "REPORTS_OUTCOME",
                    outcome_key,
                    evidence_key,
                    grade=grade,
                    mode="explicit",
                    confidence=confidence,
                    attributes={"direction": fields.get("direction", "unspecified")},
                )
                add_edge(
                    candidate_id,
                    "ASSOCIATED_WITH",
                    outcome_key,
                    evidence_key,
                    grade="E4",
                    mode="inferred",
                    confidence=confidence,
                    attributes={"not_a_treatment_claim": True},
                )
                chain_counts["compound_study_outcome"] += 1

            target_keys: list[str] = []
            direct_relations = {
                (str(value["target"]), str(value["predicate"])): dict(value)
                for value in fields.get("direct_target_relations", [])
            }
            direct_targets = {target for target, _ in direct_relations}
            target_relation_supported = bool(
                fields.get("target_relation_signals") or direct_relations
            )
            for target in fields.get("targets", []):
                target_key = f"target:{target}"
                target_keys.append(target_key)
                add_entity(
                    target_key,
                    {
                        "key": target_key,
                        "entity_type": "Target",
                        "canonical_name": target,
                        "identity": {"gene_symbol": target},
                        "external_ids": {"gene_symbol": target},
                    },
                )
                if target_relation_supported and target not in direct_targets:
                    add_edge(
                        candidate_id,
                        "TARGETS",
                        target_key,
                        evidence_key,
                        grade="E4",
                        mode="inferred",
                        confidence=confidence,
                        attributes={"semantics": "mechanistic_modulation_not_direct_binding"},
                    )
            for (target, predicate), relation in sorted(direct_relations.items()):
                target_key = f"target:{target}"
                relation_grade = {
                    "E1": "E2",
                    "E2": "E2",
                    "E3": "E3",
                    "E4": "E4",
                    "E5": "E5",
                }[grade]
                add_edge(
                    candidate_id,
                    predicate,
                    target_key,
                    evidence_key,
                    grade=relation_grade,
                    mode="explicit",
                    confidence=confidence,
                    attributes={
                        "evidence_scope": relation.get("evidence_scope", "source_reported"),
                        "primary_binding_assay_confirmed": False,
                        "scientific_boundary": (
                            "The source explicitly reports this relation; the cited primary "
                            "binding assay remains a separate verification step."
                        ),
                    },
                )

            pathway_keys: list[str] = []
            for pathway in fields.get("pathways", []):
                pathway_key = pathway
                pathway_keys.append(pathway_key)
                add_entity(
                    pathway_key,
                    {
                        "key": pathway_key,
                        "entity_type": "Pathway",
                        "canonical_name": PATHWAY_NAMES[pathway],
                        "identity": {"pathway_id": pathway},
                    },
                )
                for target_key in target_keys:
                    if target_relation_supported:
                        add_edge(
                            target_key,
                            "PARTICIPATES_IN",
                            pathway_key,
                            evidence_key,
                            grade="E4",
                            mode="inferred",
                            confidence=confidence,
                        )
                for outcome in fields.get("outcomes", []):
                    add_edge(
                        pathway_key,
                        "ASSOCIATED_WITH",
                        f"phenotype:{outcome}",
                        evidence_key,
                        grade="E4",
                        mode="inferred",
                        confidence=confidence,
                    )
            if target_relation_supported and target_keys and pathway_keys and outcome_keys:
                chain_counts["compound_target_pathway_phenotype"] += 1
                if len(chain_examples) < 25:
                    chain_examples.append(
                        {
                            "compound": candidate_id,
                            "study": study_key,
                            "targets": target_keys,
                            "pathways": pathway_keys,
                            "outcomes": outcome_keys,
                            "doc_id": doc_id,
                            "pdf_page": int(row["pdf_page"]),
                            "chunk_id": str(row["chunk_id"]),
                            "confidence": confidence,
                        }
                    )
            for signal in fields.get("safety_signals", []):
                safety_key = f"safety:{signal}"
                add_entity(
                    safety_key,
                    {
                        "key": safety_key,
                        "entity_type": "SafetySignal",
                        "canonical_name": SAFETY_NAMES.get(signal, signal),
                        "identity": {"signal_id": signal},
                    },
                )
                add_edge(
                    study_key,
                    "HAS_SAFETY_SIGNAL",
                    safety_key,
                    evidence_key,
                    grade=grade,
                    mode="explicit",
                    confidence=confidence,
                )
                add_edge(
                    candidate_id,
                    "HAS_SAFETY_SIGNAL",
                    safety_key,
                    evidence_key,
                    grade=grade,
                    mode="explicit",
                    confidence=confidence,
                    attributes={
                        "routes": list(fields.get("routes", [])),
                        "doses": list(fields.get("doses", [])),
                        "compound_specific_context": True,
                    },
                )
            for formulation in fields.get("formulations", []):
                formulation_key = f"formulation:{formulation}"
                add_entity(
                    formulation_key,
                    {
                        "key": formulation_key,
                        "entity_type": "Formulation",
                        "canonical_name": FORMULATION_NAMES.get(formulation, formulation),
                        "identity": {"formulation_id": formulation},
                    },
                )
                add_edge(
                    candidate_id,
                    "FORMULATED_AS",
                    formulation_key,
                    evidence_key,
                    grade=grade,
                    mode="explicit",
                    confidence=confidence,
                    attributes={
                        "routes": list(fields.get("routes", [])),
                        "not_a_treatment_claim": True,
                    },
                )
            metabolite_of = str(fields.get("metabolite_of", ""))
            if metabolite_of:
                ensure_compound(metabolite_of)
                add_edge(
                    candidate_id,
                    "METABOLITE_OF",
                    metabolite_of,
                    evidence_key,
                    grade=grade,
                    mode="explicit",
                    confidence=confidence,
                )
    finally:
        connection.close()
    database_sha_after = _sha256_file(database_path)
    if database_sha_after != database_sha_before:
        raise AutomaticModernGraphError("modern database changed during graph build")
    bundle = {
        "schema_version": "1.0.0",
        "bundle_id": "automatic-modern:" + hashlib.sha256(
            (graph_version + _sha256_file(structured_evidence_path)).encode("utf-8")
        ).hexdigest()[:24],
        "graph_version": graph_version,
        "metadata": {
            "description": "Machine-approved modern study, outcome, target and pathway overlay.",
            "approval_policy": {
                "policy_id": policy_id,
                "threshold": threshold,
                "comparison": "greater_than_or_equal",
                "human_reviewed": False,
            },
            "scientific_boundary": (
                "TARGETS means text-supported mechanistic modulation, not direct binding. "
                "BINDS_TO and INHIBITS preserve an explicit source statement but do not "
                "replace primary-assay confirmation. ASSOCIATED_WITH and FORMULATED_AS "
                "are not clinical treatment recommendations."
            ),
            "database_sha256": database_sha_before,
        },
        "sources": [sources[key] for key in sorted(sources)],
        "entities": [entities[key] for key in sorted(entities)],
        "evidence": [evidence[key] for key in sorted(evidence)],
        "assertions": [assertions[key] for key in sorted(assertions)],
    }
    graph = build_bundle(bundle)
    validation = validate_graph(graph, release=True)
    if not validation["valid"]:
        errors = [item for item in validation["issues"] if item["severity"] == "error"]
        raise AutomaticModernGraphError(f"modern graph release validation failed: {errors[:5]}")
    report = {
        "valid": True,
        "graph_version": graph_version,
        "sources": len(graph.sources),
        "nodes": len(graph.nodes),
        "evidence": len(graph.evidence),
        "edges": len(graph.edges),
        "chain_counts": dict(sorted(chain_counts.items())),
        "chain_examples": chain_examples,
        "compound_summary": {
            candidate_id: {
                "approved_evidence_records": compound_records[candidate_id],
                "source_documents": len(compound_sources[candidate_id]),
                "targets": sorted(compound_targets[candidate_id]),
                "pathways": sorted(compound_pathways[candidate_id]),
                "outcomes": sorted(compound_outcomes[candidate_id]),
                "safety_signals": sorted(compound_safety[candidate_id]),
                "formulations": sorted(compound_formulations[candidate_id]),
                "direct_target_relations": dict(
                    sorted(compound_direct_relations[candidate_id].items())
                ),
            }
            for candidate_id in sorted(compound_records)
        },
        "release_validation_valid": True,
        "source_database_unchanged": True,
        "database_sha256": database_sha_before,
    }
    return bundle, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build machine-approved modern KG overlay")
    parser.add_argument("--structured-evidence", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-bundle", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--graph-version", required=True)
    parser.add_argument("--policy-id", default="automatic-modern-structure-v1")
    parser.add_argument("--threshold", type=float, default=0.7)
    args = parser.parse_args()
    if args.output_bundle.exists() or args.output_report.exists():
        raise FileExistsError("refusing to overwrite modern graph output")
    bundle, report = build_automatic_modern_bundle(
        structured_evidence_path=args.structured_evidence,
        catalog_path=args.catalog,
        database_path=args.database,
        graph_version=args.graph_version,
        policy_id=args.policy_id,
        threshold=args.threshold,
    )
    args.output_bundle.parent.mkdir(parents=True, exist_ok=True)
    args.output_bundle.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_json(args.output_report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
