from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


_TRADITIONAL_TRANSLATION = str.maketrans(
    {
        "医": "醫",
        "学": "學",
        "汤": "湯",
        "内": "內",
        "痈": "癰",
        "肿": "腫",
        "银": "銀",
        "药": "藥",
        "组": "組",
        "剂": "劑",
        "量": "量",
        "钱": "錢",
        "饮": "飲",
        "杨": "楊",
        "结": "結",
        "两": "兩",
        "类": "類",
        "频": "頻",
        "记": "記",
        "载": "載",
        "烧": "燒",
        "伤": "傷",
        "创": "創",
        "现": "現",
        "换": "換",
        "数": "數",
        "录": "錄",
        "疗": "療",
        "于": "於",
        "还": "還",
        "写": "寫",
        "与": "與",
        "为": "為",
        "处": "處",
    }
)


@dataclass(frozen=True)
class Concept:
    concept_id: str
    term: str
    weight: float


@dataclass(frozen=True)
class QueryPlan:
    original_question: str
    normalized_question: str
    title_hint: str | None
    concepts: tuple[Concept, ...]
    formula_variant_hint: str | None
    retrieval_query: str
    abstain: bool
    boundary_code: str | None
    boundary_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CONCEPT_RULES: tuple[tuple[str, tuple[str, ...], str, float], ...] = (
    ("formula_rendongtang", ("忍冬湯",), "忍冬湯", 12.0),
    ("indication_neiwaiyongzhong", ("內外癰腫",), "內外癰腫", 24.0),
    ("context_neiyong", ("內癰",), "內癰", 20.0),
    ("indication_weiwanyong", ("胃脘癰",), "胃脘癰", 28.0),
    ("indication_yangmeijiedu", ("楊梅結毒",), "楊梅結毒", 28.0),
    ("ingredient_tufuling", ("土茯苓",), "土茯苓", 18.0),
    ("ingredient_jinyinhua", ("金銀花",), "金銀花", 14.0),
    ("ingredient_gancao", ("甘草",), "甘草", 12.0),
    ("dose_four_liang", ("四兩",), "四兩", 7.0),
    ("dose_three_qian", ("三錢",), "三錢", 7.0),
    ("preparation_decoction", ("煎服", "煎煮", "水煎"), "煎", 7.0),
    ("preparation_alcohol", ("飲酒", "用酒", "酒者"), "酒", 12.0),
    ("frequency_daily", ("每日",), "每日", 12.0),
)

_BOUNDARY_REASON = {
    "NO_DIRECT_ANCIENT_BURN_CLAIM": (
        "《医学心悟》忍冬汤的古籍直接证据是内外痈肿、胃脘痈或杨梅结毒，"
        "不能改写为直接治疗烧伤。"
    ),
    "NO_ANCIENT_TOPICAL_BURN_ROUTE": (
        "古籍证据记载内服煎服法，不支持将现代烧伤创面外用反写为古籍事实。"
    ),
    "NO_MODERN_CLINICAL_DOSE_CONVERSION": (
        "古籍原量只能按原文保存；缺少版本、度量衡和临床验证时不得换算为烧伤治疗克数。"
    ),
}

_VARIANT_SUPPORT = {
    "neiyong_two_ingredient": (
        ("內外癰腫", 6.0),
        ("金銀花", 3.0),
        ("甘草", 3.0),
        ("土茯苓", -8.0),
    ),
    "yangmei_toxin": (
        ("楊梅結毒", 7.0),
        ("土茯苓", 6.0),
        ("內外癰腫", -5.0),
    ),
}


def traditionalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").translate(
        _TRADITIONAL_TRANSLATION
    )


def compact_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", traditionalize(text), flags=re.UNICODE).casefold()


def _boundary_decision(normalized: str) -> tuple[str | None, str | None]:
    has_formula = "忍冬湯" in normalized
    has_burn = any(term in normalized for term in ("燒傷", "湯火傷", "火傷", "創面"))
    if not (has_formula and has_burn):
        return None, None
    if any(term in normalized for term in ("換算", "克數", "現代克", "現代劑量")):
        code = "NO_MODERN_CLINICAL_DOSE_CONVERSION"
        return code, _BOUNDARY_REASON[code]
    if any(term in normalized for term in ("外敷", "外用", "塗敷")):
        code = "NO_ANCIENT_TOPICAL_BURN_ROUTE"
        return code, _BOUNDARY_REASON[code]
    if any(term in normalized for term in ("直接記載", "是否記載", "治療")):
        code = "NO_DIRECT_ANCIENT_BURN_CLAIM"
        return code, _BOUNDARY_REASON[code]
    return None, None


def _title_hint(question: str) -> str | None:
    match = re.search(r"《([^》]+)》", question)
    return match.group(1).strip() if match else None


def plan_question(question: str) -> QueryPlan:
    normalized = traditionalize(question)
    boundary_code, boundary_reason = _boundary_decision(normalized)
    concepts: list[Concept] = []
    for concept_id, triggers, term, weight in _CONCEPT_RULES:
        if any(trigger in normalized for trigger in triggers):
            concepts.append(Concept(concept_id, term, weight))

    if "能飲酒者" in normalized or ("能飲" in normalized and "酒" in normalized):
        concepts.extend(
            [
                Concept("preparation_can_drink", "能飲", 14.0),
                Concept("preparation_wine_decoction", "酒煎服", 14.0),
            ]
        )

    variant_hint: str | None = None
    if any(term in normalized for term in ("楊梅結毒", "土茯苓")):
        variant_hint = "yangmei_toxin"
    elif any(
        term in normalized
        for term in ("內外癰腫", "胃脘癰", "內癰", "二味", "兩味藥", "金銀花四兩", "甘草三錢")
    ):
        variant_hint = "neiyong_two_ingredient"

    unique: dict[str, Concept] = {}
    for concept in concepts:
        unique.setdefault(concept.concept_id, concept)
    concepts = list(unique.values())
    retrieval_terms = [
        concept.term
        for concept in sorted(concepts, key=lambda item: -item.weight)
        if len(concept.term) >= 2
    ]
    title = _title_hint(question)
    if title:
        retrieval_terms.append(title)
    retrieval_query = " ".join(dict.fromkeys(retrieval_terms)) or normalized

    return QueryPlan(
        original_question=question,
        normalized_question=normalized,
        title_hint=title,
        concepts=tuple(concepts),
        formula_variant_hint=variant_hint,
        retrieval_query=retrieval_query,
        abstain=boundary_code is not None,
        boundary_code=boundary_code,
        boundary_reason=boundary_reason,
    )


def _database_path(cfg: dict[str, Any]) -> Path:
    value = cfg.get("paths", {}).get("ancient_database")
    if not value:
        raise FileNotFoundError("paths.ancient_database is not configured")
    return Path(value)


def _snippet(text: str, terms: list[str], width: int = 360) -> str:
    compact = traditionalize(text)
    positions = [compact.find(term) for term in terms if term and term in compact]
    start = max(0, min(positions) - width // 3) if positions else 0
    return text[start : start + width]


def query_curated_lexical(
    cfg: dict[str, Any],
    plan: QueryPlan,
    *,
    top_k: int = 100,
) -> list[dict[str, Any]]:
    if plan.abstain:
        return []
    db_path = _database_path(cfg)
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT p.page_id, p.book_id, p.physical_page, p.pdf_page_label, p.text,
                   p.reading_direction, p.average_confidence, p.low_confidence,
                   b.title, b.filename, b.source_sha256
            FROM pages p
            JOIN books b USING(book_id)
            """
        ).fetchall()

    title_hint = compact_text(plan.title_hint or "")
    terms = [concept.term for concept in plan.concepts]
    results: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["text"] or "")
        normalized_text = traditionalize(text)
        normalized_title = compact_text(str(row["title"] or ""))
        title_match = bool(title_hint and title_hint in normalized_title)
        hits = [
            concept
            for concept in plan.concepts
            if concept.term and concept.term in normalized_text
        ]
        if not hits:
            continue
        score = sum(concept.weight for concept in hits)
        score += 60.0 if title_match else 0.0
        support_hits: list[str] = []
        for term, weight in _VARIANT_SUPPORT.get(
            plan.formula_variant_hint or "", ()
        ):
            if term in normalized_text:
                score += weight
                support_hits.append(term)
        coverage = len(hits) / max(1, len(plan.concepts))
        score += coverage * 10.0
        results.append(
            {
                "corpus": "ancient",
                "record_type": "page",
                "chunk_id": row["page_id"],
                "doc_id": row["book_id"],
                "title": row["title"],
                "year": "",
                "doi": "",
                "pdf_page": row["physical_page"],
                "page_label": row["pdf_page_label"],
                "source_filename": row["filename"],
                "sha256": row["source_sha256"],
                "snippet": _snippet(text, terms),
                "keyword_score": None,
                "vector_score": None,
                "fusion_score": None,
                "reranker_score": None,
                "research_score": round(score, 6),
                "research_hits": [concept.term for concept in hits],
                "variant_support_hits": support_hits,
                "title_scope_match": title_match,
                "reading_direction": row["reading_direction"],
                "average_confidence": row["average_confidence"],
                "low_confidence": int(row["low_confidence"]),
            }
        )
    results.sort(
        key=lambda item: (
            -float(item["research_score"]),
            item["doc_id"],
            int(item["pdf_page"]),
        )
    )
    for rank, item in enumerate(results[:top_k], start=1):
        item["research_rank"] = rank
    return results[:top_k]


BaselineQuery = Callable[
    [dict[str, Any], str, str, int],
    list[dict[str, Any]],
]


def query_specialized(
    cfg: dict[str, Any],
    question: str,
    retrieval: str,
    *,
    top_k: int = 10,
    baseline_query: BaselineQuery | None = None,
) -> tuple[list[dict[str, Any]], QueryPlan]:
    plan = plan_question(question)
    if plan.abstain:
        return [], plan

    curated = query_curated_lexical(cfg, plan, top_k=max(100, top_k))
    baseline: list[dict[str, Any]] = []
    if baseline_query is not None:
        baseline = baseline_query(cfg, plan.retrieval_query, retrieval, max(top_k, 10))

    merged: dict[str, dict[str, Any]] = {
        str(item["chunk_id"]): dict(item) for item in curated
    }
    for rank, item in enumerate(baseline, start=1):
        key = str(item["chunk_id"])
        target = merged.setdefault(key, dict(item))
        target["baseline_rank"] = rank
        for field in (
            "keyword_score",
            "vector_score",
            "fusion_score",
            "reranker_score",
        ):
            if item.get(field) is not None:
                target[field] = item[field]

    for item in merged.values():
        research_score = float(item.get("research_score") or 0.0)
        baseline_rank = int(item.get("baseline_rank") or 0)
        baseline_bonus = 2.0 / baseline_rank if baseline_rank else 0.0
        item["planned_score"] = round(research_score + baseline_bonus, 8)

    results = sorted(
        merged.values(),
        key=lambda item: (
            -float(item["planned_score"]),
            int(item.get("baseline_rank") or 10**9),
            str(item["doc_id"]),
            int(item["pdf_page"]),
        ),
    )[:top_k]
    for rank, item in enumerate(results, start=1):
        item["fusion_rank"] = rank
        item["planned_rank"] = rank
    return results, plan
