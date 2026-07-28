from __future__ import annotations

import re
from typing import Any


# 主题 -> 关键词/模式。分数依据命中关键词，可解释。
TOPIC_PATTERNS: dict[str, list[str]] = {
    "burn": [r"\bburns?\b", r"\bburn[- ]injur", r"烧伤", r"烧烫伤", r"热烧伤"],
    "scald": [r"\bscalds?\b", r"烫伤", r"汤火伤"],
    "wound_healing": [
        r"wound[- ]heal",
        r"\bwounds?\b",
        r"创面",
        r"伤口愈合",
        r"愈合",
        r"溃疡",
    ],
    "Lonicera": [r"\blonicera\b", r"lonicerae"],
    "honeysuckle": [r"\bhoneysuckle\b", r"blue honeysuckle"],
    "忍冬": [r"忍冬"],
    "金银花": [r"金银花", r"银花"],
    "Glycyrrhiza": [r"\bglycyrrhiza\b", r"glycyrrhizae"],
    "licorice": [r"\blicorice\b", r"\blicorice\b", r"\bliquorice\b"],
    "甘草": [r"甘草", r"炙甘草"],
    "chlorogenic_acid": [
        r"chlorogenic acid",
        r"\bchlorogenic\b",
        r"绿原酸",
        r"\bcga\b",
    ],
    "glycyrrhizin": [
        r"\bglycyrrhizin\b",
        r"glycyrrhizic acid",
        r"glycyrrhizinate",
        r"甘草酸",
        r"甘草甜素",
    ],
    "inflammation": [
        r"\binflammat",
        r"\bnf-?κ?b\b",
        r"炎症",
        r"抗炎",
        r"炎性",
    ],
    "oxidative_stress": [
        r"oxidative stress",
        r"\bantioxidant",
        r"氧化应激",
        r"抗氧化",
        r"\bros\b",
    ],
    "antimicrobial": [
        r"\bantimicrobial\b",
        r"\bantibacterial\b",
        r"抗菌",
        r"抑菌",
        r"抗感染",
    ],
    "hydrogel": [r"\bhydrogel", r"水凝胶"],
    "drug_delivery": [
        r"drug delivery",
        r"controlled release",
        r"缓释",
        r"给药系统",
        r"载药",
    ],
    "network_pharmacology": [
        r"network pharmacology",
        r"网络药理",
    ],
    "knowledge_graph": [
        r"knowledge graph",
        r"知识图谱",
    ],
    "clinical_evidence": [
        r"\bclinical trial\b",
        r"randomized",
        r"随机对照",
        r"临床试验",
        r"\brct\b",
        r"患者",
    ],
    "safety": [
        r"\bsafety\b",
        r"\btoxicity\b",
        r"不良反应",
        r"毒性",
        r"安全性",
    ],
}


# 核心主题权重更高
CORE_WEIGHTS = {
    "burn": 18,
    "scald": 16,
    "wound_healing": 14,
    "Lonicera": 12,
    "honeysuckle": 10,
    "忍冬": 12,
    "金银花": 12,
    "Glycyrrhiza": 10,
    "licorice": 10,
    "甘草": 10,
    "chlorogenic_acid": 12,
    "glycyrrhizin": 12,
}
DEFAULT_WEIGHT = 6


def _compile() -> dict[str, list[re.Pattern[str]]]:
    compiled: dict[str, list[re.Pattern[str]]] = {}
    for tag, pats in TOPIC_PATTERNS.items():
        compiled[tag] = [re.compile(p, re.IGNORECASE) for p in pats]
    return compiled


_COMPILED = _compile()


def score_topics(text: str, title: str = "") -> dict[str, Any]:
    """基于关键词命中给出可解释的 relevance_score 与 topic_tags。"""
    hay = f"{title}\n{text}" if title else (text or "")
    if not hay.strip():
        return {
            "topic_tags": ["off_topic"],
            "relevance_score": 0,
            "relevance_evidence": {"reason": "empty_text", "hits": []},
        }

    hits: list[dict[str, Any]] = []
    tags: list[str] = []
    score = 0
    for tag, patterns in _COMPILED.items():
        matched_terms: list[str] = []
        count = 0
        for pat in patterns:
            found = pat.findall(hay)
            if found:
                matched_terms.append(pat.pattern)
                count += len(found)
        if count:
            weight = CORE_WEIGHTS.get(tag, DEFAULT_WEIGHT)
            # 标题命中加权
            title_bonus = 0
            if title:
                for pat in patterns:
                    if pat.search(title):
                        title_bonus = max(title_bonus, 8)
                        break
            add = min(weight + title_bonus + min(count, 5), 30)
            score += add
            tags.append(tag)
            hits.append(
                {
                    "tag": tag,
                    "match_count": count,
                    "patterns": matched_terms[:5],
                    "weight_added": add,
                }
            )

    score = max(0, min(100, score))
    if not tags:
        tags = ["off_topic"]
        hits.append({"tag": "off_topic", "match_count": 0, "patterns": [], "weight_added": 0})
        score = min(score, 10)

    # 若仅有边缘主题且无烧伤/创面/核心药，适当压低
    core_present = any(
        t in tags
        for t in (
            "burn",
            "scald",
            "wound_healing",
            "Lonicera",
            "honeysuckle",
            "忍冬",
            "金银花",
            "Glycyrrhiza",
            "licorice",
            "甘草",
            "chlorogenic_acid",
            "glycyrrhizin",
        )
    )
    if not core_present and "off_topic" not in tags:
        tags.append("off_topic")
        score = min(score, 35)

    return {
        "topic_tags": tags,
        "relevance_score": int(score),
        "relevance_evidence": {"hits": hits, "core_present": core_present},
    }
