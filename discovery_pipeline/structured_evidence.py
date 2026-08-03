from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


class StructuredEvidenceError(ValueError):
    pass


STUDY_PATTERNS = (
    ("randomized_trial", (r"randomi[sz]ed", r"随机", r"double[- ]blind")),
    ("controlled_clinical", (r"clinical trial", r"临床试验", r"patients?")),
    ("animal", (r"\b(?:mice|mouse|rats?|rabbit|porcine)\b", r"小鼠", r"大鼠", r"兔", r"动物模型")),
    ("in_vitro", (r"in vitro", r"\b(?:cells?|fibroblasts?|keratinocytes?)\b", r"细胞")),
    ("systematic_review", (r"systematic review", r"meta-analysis", r"系统综述", r"荟萃分析")),
    ("computational", (r"molecular docking", r"network pharmacology", r"分子对接", r"网络药理")),
    ("analytical_chemistry", (r"\bHPLC\b", r"LC[- /]?MS", r"色谱", r"含量测定")),
)

OUTCOMES = {
    "wound_closure": ("wound closure", "closure rate", "创面闭合", "伤口闭合"),
    "wound_healing": ("wound healing", "wound repair", "创面愈合", "创面修复", "伤口愈合"),
    "inflammation": ("inflammation", "inflammatory", "炎症", "抗炎"),
    "oxidative_stress": ("oxidative stress", "reactive oxygen species", "ROS", "氧化应激"),
    "collagen_deposition": ("collagen deposition", "collagen", "胶原沉积", "胶原"),
    "angiogenesis": ("angiogenesis", "neovascularization", "血管生成", "新生血管"),
    "antibacterial": ("antibacterial", "antimicrobial", "抑菌", "抗菌"),
    "re_epithelialization": ("re-epithelialization", "reepithelialization", "再上皮化"),
    "scar": ("scar", "fibrosis", "瘢痕", "纤维化"),
    "pain": ("pain", "analges", "疼痛", "镇痛"),
}

TARGETS = {
    "HMGB1": (
        "HMGB1",
        "high-mobility group box 1",
        "high mobility group box 1",
        "高迁移率族蛋白B1",
    ),
    "NFKB1": ("NF-kappa B", "NF-κB", "NF-kB", "NFKB1", "核因子κB"),
    "NFE2L2": ("Nrf2", "NFE2L2"),
    "HMOX1": ("HO-1", "HMOX1", "heme oxygenase-1"),
    "TNF": ("TNF-alpha", "TNF-α", "TNFα", "肿瘤坏死因子"),
    "IL6": ("IL-6", "interleukin-6", "白细胞介素-6"),
    "IL1B": ("IL-1beta", "IL-1β", "IL1B", "白细胞介素-1β"),
    "VEGFA": ("VEGF", "VEGFA", "血管内皮生长因子"),
    "TGFB1": ("TGF-beta", "TGF-β", "TGFB1", "转化生长因子"),
    "MMP9": ("MMP-9", "MMP9"),
    "AKT1": ("AKT", "Akt", "AKT1"),
    "TLR4": ("TLR4", "TLR-4", "Toll-like receptor 4"),
    "NLRP3": ("NLRP3", "inflammasome", "炎症小体"),
}

PATHWAYS = {
    "pathway:nfkb": ("NF-kappa B pathway", "NF-κB pathway", "NF-kB signaling", "NF-κB信号"),
    "pathway:nrf2_ho1": ("Nrf2/HO-1", "Nrf2-HO-1", "Nrf2 signaling"),
    "pathway:pi3k_akt": ("PI3K/AKT", "PI3K-Akt", "PI3K/AKT信号"),
    "pathway:mapk": ("MAPK pathway", "MAPK signaling", "MAPK信号"),
    "pathway:tlr4_myd88": ("TLR4/MyD88", "TLR4-MYD88"),
    "pathway:tgfb_smad": ("TGF-beta/Smad", "TGF-β/Smad", "TGF-beta signaling"),
    "pathway:vegf": ("VEGF pathway", "VEGF signaling", "VEGF信号"),
    "pathway:nlrp3": ("NLRP3 inflammasome", "NLRP3炎症小体"),
}

POSITIVE_SIGNALS = (
    "improved", "accelerated", "promoted", "enhanced", "increased", "reduced",
    "decreased", "inhibited", "attenuated", "ameliorated", "促进", "改善", "加速",
    "提高", "降低", "减少", "抑制", "减轻",
)
NEGATIVE_SIGNALS = ("worsened", "increased toxicity", "adverse", "恶化", "毒性增加", "不良反应")
TARGET_RELATION_SIGNALS = (
    "via", "through", "mediated", "regulated", "activated", "inhibited", "targeted",
    "通过", "介导", "调控", "激活", "抑制", "靶向",
)
SAFETY_SIGNALS = {
    "hypertension": ("hypertension", "high blood pressure", "高血压"),
    "hypokalemia": ("hypokalemia", "hypokalaemia", "low potassium", "低钾血症"),
    "sodium_retention": (
        "sodium retention",
        "sodium and water retention",
        "fluid retention",
        "钠潴留",
        "水钠潴留",
    ),
    "pseudoaldosteronism": (
        "pseudoaldosteronism",
        "pseudohyperaldosteronism",
        "假性醛固酮增多症",
    ),
    "arrhythmia": ("arrhythmia", "cardiac arrhythmia", "心律失常"),
    "drug_interaction": (
        "drug interaction",
        "interact with diuretics",
        "interact with corticosteroids",
        "药物相互作用",
    ),
    "cytotoxicity": ("cytotoxicity", "cytotoxic", "细胞毒"),
    "general_toxicity": (
        "toxicity",
        "toxic",
        "adverse event",
        "side effect",
        "毒性",
        "不良反应",
        "副作用",
    ),
}
FORMULATIONS = {
    "hydrogel": ("hydrogel", "hydrogels", "水凝胶"),
    "wound_dressing": ("wound dressing", "wound dressings", "dressing", "创面敷料"),
    "liposome": ("liposome", "liposomes", "liposomal", "脂质体"),
    "nanofiber": ("nanofiber", "nanofibrous", "纳米纤维"),
    "film": ("wound film", "chitosan-based film", "创面薄膜"),
}
ROUTES = {
    "topical": ("topical", "externally applied", "外用", "外敷", "涂抹"),
    "oral": ("oral", "intragastric", "gavage", "口服", "灌胃"),
    "injection": ("injection", "intraperitoneal", "intravenous", "注射", "静脉"),
}
DOSE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mg/kg|mg·kg[-−]?1|mg/mL|μg/mL|ug/mL|mg|μg|ug|g/kg|%)",
    re.IGNORECASE,
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains(text: str, values: Iterable[str]) -> list[str]:
    normalized = _normalized(text)
    return sorted({value for value in values if _normalized(value) in normalized})


def _candidate_context(
    text: str,
    matched_terms: Iterable[str],
    *,
    radius: int = 900,
) -> tuple[str, list[dict[str, int]]]:
    windows: list[tuple[int, int]] = []
    for term in sorted({str(value).strip() for value in matched_terms if str(value).strip()}):
        latin_term = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+/_-]*", term))
        pattern = (
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
            if latin_term
            else re.escape(term)
        )
        flags = 0 if latin_term and len(term) <= 3 and term.isupper() else re.IGNORECASE
        for match in re.finditer(pattern, text, flags):
            windows.append((max(0, match.start() - radius), min(len(text), match.end() + radius)))
    if not windows:
        return text, [{"start": 0, "end": len(text)}]
    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    spans = [{"start": start, "end": end} for start, end in merged]
    return "\n[...candidate context...]\n".join(text[item["start"]:item["end"]] for item in spans), spans


def _glycyrrhizic_identity(text: str) -> dict[str, Any]:
    parent = bool(
        re.search(
            r"\b(?:glycyrrhizic acid|glycyrrhizin(?:ic acid)?|β-glycyrrhizin)\b|甘草酸",
            text,
            re.IGNORECASE,
        )
    )
    dipotassium = bool(
        re.search(r"\bdipotassium glycyrrhizin(?:ate|inate)\b|甘草酸二钾", text, re.IGNORECASE)
    )
    ammonium = bool(
        re.search(r"\bammonium glycyrrhizinate\b|甘草酸铵", text, re.IGNORECASE)
    )
    metabolite = bool(
        re.search(r"\bglycyrrhetinic acid\b|\benoxolone\b|甘草次酸", text, re.IGNORECASE)
    )
    forms = [
        name
        for name, matched in (
            ("parent", parent),
            ("dipotassium_salt", dipotassium),
            ("ammonium_salt", ammonium),
            ("glycyrrhetinic_metabolite", metabolite),
        )
        if matched
    ]
    return {
        "explicit_parent_mention": parent,
        "forms": forms,
        "identity_scope": forms[0] if len(forms) == 1 else "mixed" if forms else "extract_only_or_unspecified",
    }


def _direct_target_relations(
    candidate_id: str,
    text: str,
    identity: dict[str, Any],
) -> list[dict[str, str]]:
    if candidate_id != "compound:glycyrrhizic_acid" or not identity.get("explicit_parent_mention"):
        return []
    if not re.search(r"\bHMGB1\b|high[- ]mobility group box 1", text, re.IGNORECASE):
        return []
    relations: list[dict[str, str]] = []
    if re.search(r"direct binding|binds? (?:directly )?(?:to )?HMGB1|binding (?:to |with )?HMGB1", text, re.IGNORECASE):
        relations.append(
            {
                "target": "HMGB1",
                "predicate": "BINDS_TO",
                "evidence_scope": "source_reported_direct_binding",
            }
        )
    if re.search(r"inhibitor of HMGB1|inhibits? HMGB1|HMGB1 inhibitor", text, re.IGNORECASE):
        relations.append(
            {
                "target": "HMGB1",
                "predicate": "INHIBITS",
                "evidence_scope": "source_reported_functional_inhibition",
            }
        )
    return relations


def _study_type(text: str) -> tuple[str, list[str]]:
    for study_type, patterns in STUDY_PATTERNS:
        hits = [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]
        if hits:
            return study_type, hits
    return "unspecified", []


def _extract_fields(
    text: str,
    *,
    candidate_id: str = "",
    matched_terms: Iterable[str] = (),
) -> dict[str, Any]:
    scoped_text, context_spans = _candidate_context(text, matched_terms)
    study_type, study_signals = _study_type(scoped_text)
    outcomes = [key for key, terms in OUTCOMES.items() if _contains(scoped_text, terms)]
    targets = [key for key, terms in TARGETS.items() if _contains(scoped_text, terms)]
    pathways = [key for key, terms in PATHWAYS.items() if _contains(scoped_text, terms)]
    positive = _contains(scoped_text, POSITIVE_SIGNALS)
    negative = _contains(scoped_text, NEGATIVE_SIGNALS)
    relation = _contains(scoped_text, TARGET_RELATION_SIGNALS)
    safety = [key for key, terms in SAFETY_SIGNALS.items() if _contains(scoped_text, terms)]
    formulations = [key for key, terms in FORMULATIONS.items() if _contains(scoped_text, terms)]
    routes = [key for key, terms in ROUTES.items() if _contains(scoped_text, terms)]
    doses = [match.group(0) for match in DOSE_RE.finditer(scoped_text)][:8]
    identity = (
        _glycyrrhizic_identity(scoped_text)
        if candidate_id == "compound:glycyrrhizic_acid"
        else {
            "explicit_parent_mention": bool(tuple(matched_terms)),
            "forms": ["catalog_candidate"],
            "identity_scope": "catalog_candidate",
        }
    )
    direct_relations = _direct_target_relations(candidate_id, scoped_text, identity)
    if direct_relations and "HMGB1" not in targets:
        targets.append("HMGB1")
    metabolite_of = ""
    if candidate_id == "compound:glycyrrhetinic_acid" and re.search(
        r"metabol(?:ite|ized|ised|ism).{0,160}glycyrrhetinic acid|"
        r"glycyrrhiz(?:ic acid|in).{0,160}glycyrrhetinic acid",
        scoped_text,
        re.IGNORECASE | re.DOTALL,
    ):
        metabolite_of = "compound:glycyrrhizic_acid"
    direction = "mixed" if positive and negative else "beneficial" if positive else "harmful" if negative else "unspecified"
    return {
        "study_type": study_type,
        "study_type_signals": study_signals,
        "outcomes": outcomes,
        "targets": sorted(targets),
        "pathways": pathways,
        "direction": direction,
        "direction_signals": positive + negative,
        "target_relation_signals": relation,
        "safety_signals": safety,
        "formulations": formulations,
        "routes": routes,
        "doses": doses,
        "compound_identity": identity,
        "direct_target_relations": direct_relations,
        "metabolite_of": metabolite_of,
        "candidate_context_spans": context_spans,
        "candidate_context_sha256": _sha256_text(scoped_text),
    }


def _semantic_confidence(locus_confidence: float, fields: dict[str, Any]) -> tuple[float, dict[str, float]]:
    components = {
        "verified_locus": min(0.55, locus_confidence * 0.55),
        "study_type": 0.12 if fields["study_type"] != "unspecified" else 0.0,
        "outcome": 0.12 if fields["outcomes"] else 0.0,
        "direction": 0.06 if fields["direction"] != "unspecified" else 0.0,
        "mechanism": 0.07 if fields["targets"] or fields["pathways"] else 0.0,
        "dose_or_route": 0.04 if fields["doses"] or fields["routes"] or fields["formulations"] else 0.0,
        "safety": 0.04 if fields["safety_signals"] else 0.0,
    }
    return round(min(0.95, sum(components.values())), 6), components


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            locus_id = str(row.get("locus_id", ""))
            if not locus_id or locus_id in seen:
                raise StructuredEvidenceError(f"missing or duplicate locus_id at {line_number}")
            seen.add(locus_id)
            rows.append(row)
    if not rows:
        raise StructuredEvidenceError("approved locus input is empty")
    return rows


def structure_modern_evidence(
    *,
    approved_loci_path: Path,
    database_path: Path,
    output_dir: Path,
    threshold: float = 0.7,
    policy_id: str = "automatic-modern-structure-v1",
) -> dict[str, Any]:
    if not 0.0 <= threshold <= 1.0:
        raise StructuredEvidenceError("threshold must be between 0 and 1")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    rows = _read_jsonl(approved_loci_path)
    database_sha_before = _sha256_file(database_path)
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    approved: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    study_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    pathway_counts: Counter[str] = Counter()
    safety_counts: Counter[str] = Counter()
    formulation_counts: Counter[str] = Counter()
    direct_relation_counts: Counter[str] = Counter()
    identity_scope_counts: Counter[str] = Counter()
    try:
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise StructuredEvidenceError("modern database quick_check failed")
        for locus in rows:
            source = connection.execute(
                """
                SELECT c.chunk_id, c.doc_id, c.pdf_page, c.text,
                       d.title, d.year, d.doi, d.source_filename, d.sha256
                FROM chunks c JOIN documents d USING(doc_id)
                WHERE c.chunk_id = ?
                """,
                (str(locus.get("chunk_id", "")),),
            ).fetchone()
            issues: list[str] = []
            if source is None:
                issues.append("source_chunk_missing")
                text = ""
            else:
                text = str(source["text"] or "")
                checks = {
                    "doc_id": str(source["doc_id"]),
                    "pdf_page": int(source["pdf_page"]),
                    "source_sha256": str(source["sha256"]),
                    "chunk_text_sha256": _sha256_text(text),
                }
                for field, actual in checks.items():
                    expected = locus.get(field)
                    equal = int(expected) == actual if field == "pdf_page" else str(expected or "") == str(actual or "")
                    if not equal:
                        issues.append(f"{field}_mismatch")
            candidate_id = str(locus.get("candidate_id", ""))
            fields = _extract_fields(
                text,
                candidate_id=candidate_id,
                matched_terms=locus.get("matched_terms", []),
            )
            try:
                locus_confidence = float(locus.get("candidate_confidence", 0.0))
            except (TypeError, ValueError):
                locus_confidence = 0.0
            if not math.isfinite(locus_confidence):
                locus_confidence = 0.0
            confidence, components = _semantic_confidence(locus_confidence, fields)
            if issues:
                confidence = 0.0
            reasons = list(issues)
            if fields["study_type"] == "unspecified":
                confidence = 0.0
                reasons.append("study_type_not_explicit")
            if not fields["outcomes"] and not fields["safety_signals"]:
                confidence = 0.0
                reasons.append("outcome_or_safety_not_explicit")
            if (
                candidate_id == "compound:glycyrrhizic_acid"
                and not fields["compound_identity"]["explicit_parent_mention"]
            ):
                confidence = 0.0
                reasons.append("glycyrrhizic_identity_not_explicit")
            if confidence < threshold:
                reasons.append("semantic_confidence_below_threshold")
            record = {
                **locus,
                "title": str(source["title"] or "") if source else "",
                "year": str(source["year"] or "") if source else "",
                "doi": str(source["doi"] or "") if source else "",
                "source_filename": str(source["source_filename"] or "") if source else "",
                "structured_fields": fields,
                "semantic_confidence": confidence,
                "semantic_confidence_components": components,
                "review_status": "approved" if confidence >= threshold else "discarded",
                "automatic_approval_policy": policy_id,
                "automatic_approval_threshold": threshold,
                "automatic_decision_reasons": sorted(set(reasons)),
                "human_reviewed": False,
            }
            if confidence >= threshold:
                approved.append(record)
                study_counts[fields["study_type"]] += 1
                outcome_counts.update(fields["outcomes"])
                target_counts.update(fields["targets"])
                pathway_counts.update(fields["pathways"])
                safety_counts.update(fields["safety_signals"])
                formulation_counts.update(fields["formulations"])
                direct_relation_counts.update(
                    value["predicate"] for value in fields["direct_target_relations"]
                )
                identity_scope_counts.update([fields["compound_identity"]["identity_scope"]])
            else:
                discarded.append(record)
    finally:
        connection.close()
    database_sha_after = _sha256_file(database_path)
    if database_sha_after != database_sha_before:
        raise StructuredEvidenceError("modern database changed during structuring")

    temporary = output_dir.with_name(f".{output_dir.name}.tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary output exists: {temporary}")
    temporary.mkdir(parents=True)
    try:
        for name, values in (
            ("approved_structured_evidence.jsonl", approved),
            ("discarded_structured_evidence.jsonl", discarded),
        ):
            payload = "".join(
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                for value in values
            )
            (temporary / name).write_text(payload, encoding="utf-8", newline="\n")
        report = {
            "valid": True,
            "policy_id": policy_id,
            "threshold": threshold,
            "comparison": "greater_than_or_equal",
            "human_review_required": False,
            "human_reviewed": False,
            "input_approved_loci": len(rows),
            "approved_structured_evidence": len(approved),
            "discarded_after_structuring": len(discarded),
            "database_sha256_before": database_sha_before,
            "database_sha256_after": database_sha_after,
            "source_database_unchanged": True,
            "study_type_counts": dict(sorted(study_counts.items())),
            "outcome_counts": dict(sorted(outcome_counts.items())),
            "target_counts": dict(sorted(target_counts.items())),
            "pathway_counts": dict(sorted(pathway_counts.items())),
            "safety_signal_counts": dict(sorted(safety_counts.items())),
            "formulation_counts": dict(sorted(formulation_counts.items())),
            "direct_target_relation_counts": dict(sorted(direct_relation_counts.items())),
            "compound_identity_scope_counts": dict(sorted(identity_scope_counts.items())),
        }
        (temporary / "structured_evidence_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Structure machine-approved modern loci")
    parser.add_argument("--approved-loci", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--policy-id", default="automatic-modern-structure-v1")
    args = parser.parse_args()
    report = structure_modern_evidence(
        approved_loci_path=args.approved_loci,
        database_path=args.database,
        output_dir=args.output_dir,
        threshold=args.threshold,
        policy_id=args.policy_id,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
