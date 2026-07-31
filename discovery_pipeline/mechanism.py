from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Iterable


TARGET_EVIDENCE_TIERS = {
    "direct_binding": 1,
    "functional_experiment": 1,
    "high_throughput_experiment": 2,
    "curated_database": 3,
    "similarity_prediction": 4,
    "machine_learning_prediction": 4,
    "molecular_docking": 5,
}
HIGH_CONFIDENCE_TARGET_TYPES = {
    "direct_binding",
    "functional_experiment",
    "high_throughput_experiment",
}
DISEASE_EVIDENCE_CHANNELS = {
    "database_association",
    "transcriptome",
    "literature_experiment",
}
REVIEW_STATUSES = {"approved", "pending", "rejected"}


class MechanismInputError(ValueError):
    pass


def _log_combination(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeometric_survival(
    overlap: int,
    population_size: int,
    success_population: int,
    sample_size: int,
) -> float:
    if not all(
        isinstance(value, int)
        for value in (overlap, population_size, success_population, sample_size)
    ):
        raise MechanismInputError("hypergeometric inputs must be integers")
    if population_size <= 0:
        raise MechanismInputError("population_size must be positive")
    if not 0 <= success_population <= population_size:
        raise MechanismInputError("success_population is outside population")
    if not 0 <= sample_size <= population_size:
        raise MechanismInputError("sample_size is outside population")
    maximum = min(success_population, sample_size)
    if overlap <= 0:
        return 1.0
    if overlap > maximum:
        return 0.0
    denominator = _log_combination(population_size, sample_size)
    log_terms = [
        _log_combination(success_population, value)
        + _log_combination(population_size - success_population, sample_size - value)
        - denominator
        for value in range(overlap, maximum + 1)
        if sample_size - value <= population_size - success_population
    ]
    if not log_terms:
        return 0.0
    maximum_log = max(log_terms)
    probability = math.exp(maximum_log) * sum(
        math.exp(value - maximum_log) for value in log_terms
    )
    return min(1.0, probability)


def benjamini_hochberg(
    records: Iterable[tuple[str, float]],
) -> dict[str, float]:
    ordered = sorted(
        ((str(identifier), float(p_value)) for identifier, p_value in records),
        key=lambda value: (value[1], value[0]),
    )
    if any(not 0 <= p_value <= 1 for _, p_value in ordered):
        raise MechanismInputError("p-values must be between 0 and 1")
    identifiers = [identifier for identifier, _ in ordered]
    if len(identifiers) != len(set(identifiers)):
        raise MechanismInputError("multiple-testing identifiers must be unique")
    count = len(ordered)
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank_from_end in range(count, 0, -1):
        identifier, p_value = ordered[rank_from_end - 1]
        candidate = min(1.0, p_value * count / rank_from_end)
        running = min(running, candidate)
        adjusted[identifier] = running
    return adjusted


def _validate_disease_gene(record: dict[str, Any]) -> None:
    if not str(record.get("gene_symbol", "")).strip():
        raise MechanismInputError("disease gene requires gene_symbol")
    raw_channels = record.get("evidence_channels", [])
    if not isinstance(raw_channels, list):
        raise MechanismInputError("disease evidence_channels must be an array")
    channels = set(raw_channels)
    if not channels <= DISEASE_EVIDENCE_CHANNELS:
        raise MechanismInputError(
            f"invalid disease evidence channels for {record.get('gene_symbol')}: "
            f"{sorted(channels - DISEASE_EVIDENCE_CHANNELS)}"
        )
    review_status = record.get("review_status", "pending")
    if review_status not in REVIEW_STATUSES:
        raise MechanismInputError(
            f"invalid disease review_status for {record.get('gene_symbol')}: "
            f"{review_status!r}"
        )
    if review_status == "approved" and not record.get("evidence_ids"):
        raise MechanismInputError(
            f"approved disease gene {record.get('gene_symbol')} requires evidence_ids"
        )


def classify_disease_genes(
    records: Iterable[dict[str, Any]],
) -> tuple[set[str], set[str], dict[str, list[str]]]:
    high_confidence: set[str] = set()
    extended: set[str] = set()
    phases: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        _validate_disease_gene(record)
        gene = str(record["gene_symbol"]).upper()
        channels = set(record.get("evidence_channels", []))
        if record.get("review_status") != "approved":
            continue
        extended.add(gene)
        if "literature_experiment" in channels or len(channels) >= 2:
            high_confidence.add(gene)
        for phase in record.get("wound_phases", []):
            phases[gene].add(str(phase))
    return high_confidence, extended, {
        gene: sorted(values) for gene, values in phases.items()
    }


def disease_gene_audit(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    audit: list[dict[str, Any]] = []
    for record in records:
        _validate_disease_gene(record)
        channels = sorted(set(record.get("evidence_channels", [])))
        approved = record.get("review_status", "pending") == "approved"
        high_confidence = approved and (
            "literature_experiment" in channels or len(channels) >= 2
        )
        audit.append(
            {
                "gene_symbol": str(record["gene_symbol"]).upper(),
                "evidence_channels": channels,
                "review_status": record.get("review_status", "pending"),
                "used_in_high_confidence_set": high_confidence,
                "evidence_ids": record.get("evidence_ids", []),
            }
        )
    return audit


def classify_compound_targets(
    records: Iterable[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[dict[str, Any]]]:
    high_confidence: defaultdict[str, set[str]] = defaultdict(set)
    extended: defaultdict[str, set[str]] = defaultdict(set)
    audit: list[dict[str, Any]] = []
    for record in records:
        compound_id = str(record.get("compound_id", ""))
        gene = str(record.get("gene_symbol", "")).upper()
        evidence_type = str(record.get("evidence_type", ""))
        if not compound_id or not gene:
            raise MechanismInputError("compound target requires compound_id and gene_symbol")
        if evidence_type not in TARGET_EVIDENCE_TIERS:
            raise MechanismInputError(
                f"invalid target evidence type {evidence_type!r} for {compound_id}/{gene}"
            )
        review_status = record.get("review_status", "pending")
        if review_status not in REVIEW_STATUSES:
            raise MechanismInputError(
                f"invalid target review_status for {compound_id}/{gene}: "
                f"{review_status!r}"
            )
        approved = review_status == "approved"
        if approved and not record.get("evidence_ids"):
            raise MechanismInputError(
                f"approved target {compound_id}/{gene} requires evidence_ids"
            )
        if approved:
            extended[compound_id].add(gene)
            if evidence_type in HIGH_CONFIDENCE_TARGET_TYPES:
                high_confidence[compound_id].add(gene)
        audit.append(
            {
                "compound_id": compound_id,
                "gene_symbol": gene,
                "evidence_type": evidence_type,
                "evidence_tier": TARGET_EVIDENCE_TIERS[evidence_type],
                "review_status": review_status,
                "used_in_primary_mechanism": (
                    approved and evidence_type in HIGH_CONFIDENCE_TARGET_TYPES
                ),
                "evidence_ids": record.get("evidence_ids", []),
            }
        )
    return dict(high_confidence), dict(extended), audit


def connected_components(
    nodes: set[str],
    edges: Iterable[tuple[str, str]],
) -> list[list[str]]:
    adjacency: defaultdict[str, set[str]] = defaultdict(set)
    for left, right in edges:
        if left not in nodes or right not in nodes or left == right:
            continue
        adjacency[left].add(right)
        adjacency[right].add(left)
    remaining = set(nodes)
    components: list[list[str]] = []
    while remaining:
        root = min(remaining)
        queue = deque([root])
        component: set[str] = set()
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            queue.extend(sorted(adjacency[current] - component))
        remaining -= component
        components.append(sorted(component))
    return sorted(components, key=lambda value: (-len(value), value))


def pathway_enrichment(
    query_genes: set[str],
    background_genes: set[str],
    pathways: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    query = {value.upper() for value in query_genes} & {
        value.upper() for value in background_genes
    }
    background = {value.upper() for value in background_genes}
    if not query or not background:
        return []
    raw: list[dict[str, Any]] = []
    seen_pathway_ids: set[str] = set()
    for pathway in pathways:
        pathway_id = str(pathway.get("pathway_id", "")).strip()
        if not pathway_id:
            raise MechanismInputError("pathway requires pathway_id")
        if pathway_id in seen_pathway_ids:
            raise MechanismInputError(f"duplicate pathway_id: {pathway_id}")
        seen_pathway_ids.add(pathway_id)
        pathway_genes = {
            str(value).upper() for value in pathway.get("genes", [])
        } & background
        overlap_genes = sorted(query & pathway_genes)
        p_value = hypergeometric_survival(
            len(overlap_genes),
            len(background),
            len(pathway_genes),
            len(query),
        )
        raw.append(
            {
                "pathway_id": pathway_id,
                "name": pathway.get("name", ""),
                "source": pathway.get("source", ""),
                "overlap_genes": overlap_genes,
                "overlap_count": len(overlap_genes),
                "pathway_gene_count": len(pathway_genes),
                "query_gene_count": len(query),
                "background_gene_count": len(background),
                "p_value": p_value,
            }
        )
    adjusted = benjamini_hochberg(
        (value["pathway_id"], value["p_value"]) for value in raw
    )
    for value in raw:
        value["fdr"] = adjusted[value["pathway_id"]]
        value["significant_fdr_0_05"] = value["fdr"] < 0.05
    return sorted(raw, key=lambda value: (value["fdr"], value["p_value"], value["pathway_id"]))


def classify_ppi_edges(
    records: Iterable[dict[str, Any]],
) -> tuple[list[tuple[str, str, float]], list[dict[str, Any]]]:
    accepted: list[tuple[str, str, float]] = []
    audit: list[dict[str, Any]] = []
    for record in records:
        left = str(record.get("source", "")).upper().strip()
        right = str(record.get("target", "")).upper().strip()
        if not left or not right:
            raise MechanismInputError("PPI edge requires source and target genes")
        try:
            score = float(record.get("score"))
        except (TypeError, ValueError) as exc:
            raise MechanismInputError(
                f"PPI score must be numeric for {left}/{right}"
            ) from exc
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise MechanismInputError(
                f"PPI score must be finite and between 0 and 1 for {left}/{right}"
            )
        review_status = record.get("review_status", "pending")
        if review_status not in REVIEW_STATUSES:
            raise MechanismInputError(
                f"invalid PPI review_status for {left}/{right}: {review_status!r}"
            )
        database = str(record.get("database", "")).strip()
        if review_status == "approved" and (
            not database or not record.get("evidence_ids")
        ):
            raise MechanismInputError(
                f"approved PPI edge {left}/{right} requires database and evidence_ids"
            )
        used = review_status == "approved" and score >= 0.7
        if used:
            accepted.append((left, right, score))
        audit.append(
            {
                "source": left,
                "target": right,
                "score": score,
                "database": database,
                "review_status": review_status,
                "used_in_primary_mechanism": used,
                "exclusion_reason": (
                    "" if used else "not_approved_or_score_below_0.7"
                ),
                "evidence_ids": record.get("evidence_ids", []),
            }
        )
    return accepted, audit


def classify_pathways(
    records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    approved: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        pathway_id = str(record.get("pathway_id", "")).strip()
        if not pathway_id:
            raise MechanismInputError("pathway requires pathway_id")
        if pathway_id in seen:
            raise MechanismInputError(f"duplicate pathway_id: {pathway_id}")
        seen.add(pathway_id)
        review_status = record.get("review_status", "pending")
        if review_status not in REVIEW_STATUSES:
            raise MechanismInputError(
                f"invalid pathway review_status for {pathway_id}: {review_status!r}"
            )
        source = str(record.get("source", "")).strip()
        genes = [str(value).upper() for value in record.get("genes", [])]
        if review_status == "approved" and (
            not source or not record.get("evidence_ids") or not genes
        ):
            raise MechanismInputError(
                f"approved pathway {pathway_id} requires source, genes, and evidence_ids"
            )
        used = review_status == "approved"
        normalized = {**record, "pathway_id": pathway_id, "genes": genes}
        if used:
            approved.append(normalized)
        audit.append(
            {
                "pathway_id": pathway_id,
                "source": source,
                "review_status": review_status,
                "used_in_enrichment": used,
                "evidence_ids": record.get("evidence_ids", []),
            }
        )
    return approved, audit


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def analyze_mechanism(payload: dict[str, Any]) -> dict[str, Any]:
    disease_gene_records = [dict(value) for value in payload.get("disease_genes", [])]
    compound_target_records = [
        dict(value) for value in payload.get("compound_targets", [])
    ]
    disease_high, disease_extended, gene_phases = classify_disease_genes(
        disease_gene_records
    )
    target_high, target_extended, target_audit = classify_compound_targets(
        compound_target_records
    )
    disease_audit = disease_gene_audit(disease_gene_records)
    core_by_compound = {
        compound_id: sorted(target_high.get(compound_id, set()) & disease_high)
        for compound_id in sorted(set(target_high) | set(target_extended))
    }
    all_core = (
        set().union(*(set(values) for values in core_by_compound.values()))
        if core_by_compound
        else set()
    )
    ppi_edges, ppi_audit = classify_ppi_edges(payload.get("ppi_edges", []))
    modules = [
        component
        for component in connected_components(
            all_core,
            ((left, right) for left, right, _ in ppi_edges),
        )
        if len(component) >= 5
    ]
    approved_pathways, pathway_audit = classify_pathways(payload.get("pathways", []))
    enrichment = pathway_enrichment(
        all_core,
        {str(value).upper() for value in payload.get("background_genes", [])},
        approved_pathways,
    )
    significant_pathways = {
        value["pathway_id"] for value in enrichment if value["significant_fdr_0_05"]
    }
    compound_pathways: defaultdict[str, set[str]] = defaultdict(set)
    pathway_by_id = {
        str(value["pathway_id"]): {str(gene).upper() for gene in value.get("genes", [])}
        for value in approved_pathways
    }
    for compound_id, genes in core_by_compound.items():
        gene_set = set(genes)
        for pathway_id in significant_pathways:
            if gene_set & pathway_by_id[pathway_id]:
                compound_pathways[compound_id].add(pathway_id)
    complementarity = []
    compounds = sorted(core_by_compound)
    for index, left in enumerate(compounds):
        for right in compounds[index + 1 :]:
            left_targets = set(core_by_compound[left])
            right_targets = set(core_by_compound[right])
            left_pathways = compound_pathways[left]
            right_pathways = compound_pathways[right]
            complementarity.append(
                {
                    "compound_a": left,
                    "compound_b": right,
                    "target_jaccard": round(_jaccard(left_targets, right_targets), 6),
                    "unique_targets_a": sorted(left_targets - right_targets),
                    "unique_targets_b": sorted(right_targets - left_targets),
                    "pathway_jaccard": round(_jaccard(left_pathways, right_pathways), 6),
                    "unique_pathways_a": sorted(left_pathways - right_pathways),
                    "unique_pathways_b": sorted(right_pathways - left_pathways),
                    "interpretation": "coverage_complementarity_hypothesis_only",
                }
            )
    return {
        "schema_version": 1,
        "analysis_id": payload.get("analysis_id", ""),
        "scientific_boundary": (
            "Primary mechanism uses approved experimental compound targets and disease "
            "genes supported by experiments or at least two evidence channels. Curated "
            "and predicted targets remain an extended hypothesis set."
        ),
        "disease_gene_counts": {
            "high_confidence": len(disease_high),
            "extended": len(disease_extended),
        },
        "disease_gene_audit": disease_audit,
        "gene_wound_phases": gene_phases,
        "target_audit": target_audit,
        "core_targets_by_compound": core_by_compound,
        "extended_targets_by_compound": {
            key: sorted(value) for key, value in target_extended.items()
        },
        "ppi": {
            "minimum_score": 0.7,
            "edge_count": len(ppi_edges),
            "modules_minimum_5_nodes": modules,
            "audit": ppi_audit,
        },
        "enrichment": enrichment,
        "pathway_audit": pathway_audit,
        "complementarity": complementarity,
    }
