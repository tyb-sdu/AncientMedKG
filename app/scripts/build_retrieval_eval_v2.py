#!/usr/bin/env python
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_prep.config import load_config
from rag_prep.io_utils import read_jsonl
from rag_prep.search import normalize_search_text


SPECS: list[tuple[str, str, str, str, list[str]]] = [
    ("B01", "burn", "甘草提取物水凝胶治疗实验性二度烧伤的证据", "doi:10.3390/gels11100834", ["second-degree burns", "glycyrrhiza", "hydrogel"]),
    ("B02", "burn", "红糖负载胶原凝胶怎样加速烧伤创面愈合", "doi:10.1093/rb/rbag113", ["brown sugar", "burn wound healing", "collagen hydrogel"]),
    ("B03", "burn", "烧伤后氧化应激与创面迟缓愈合有什么关系", "doi:10.1093/burnst/tkaf040/8160266", ["oxidative stress", "post-burn", "wound healing"]),
    ("B04", "burn", "纳米酶促进烧伤创面修复的主要机制", "doi:10.2147/ijn.s608064", ["nanozymes", "burn wound healing", "mechanism"]),
    ("S01", "scald", "针刺疗法用于烫伤及其并发症有哪些证据", "doi:10.1111/iwj.70833", ["scald", "needling", "burn injury"]),
    ("S02", "scald", "热液烫伤后 NLRP3 炎症小体是否可作为干预靶点", "doi:10.1093/burnst/tkae020/7702767", ["NLRP3", "burns", "inflammasome"]),
    ("S03", "scald", "艾纳香来源细胞外囊泡能否促进热损伤创面愈合", "doi:10.3389/fcell.2026.1756718", ["Blumea balsamifera", "burn wound healing", "extracellular vesicles"]),
    ("S04", "scald", "部分厚度热损伤创面铜绿假单胞菌生物膜模型", "doi:10.1093/jbcr/iry043", ["Pseudomonas aeruginosa", "biofilms", "partial-thickness burn wounds"]),
    ("W01", "wound_healing", "金银花提取物对切除性伤口修复和炎症的影响", "sha256:a8d61ecec00642c9a740b6d9f56dc3f33e9d47c92b5a88ff8e4737ef5492ad7c", ["Lonicera japonica", "wound", "anti-inflammatory"]),
    ("W02", "wound_healing", "甘草酸与脱细胞脂肪提取物双网络支架促进愈合", "doi:10.1021/acspolymersau.5c00132", ["glycyrrhizic acid", "dual-network", "wound healing"]),
    ("W03", "wound_healing", "绿原酸外泌体复合凝胶修复糖尿病创面的机制", "doi:10.1186/s13062-026-00818-z", ["chlorogenic acid-loaded exosomes", "diabetic wound healing", "immunomodulatory"]),
    ("W04", "wound_healing", "甘草酸二钾通过调节炎症改善皮肤伤口", "doi:10.3390/ijms24043839", ["dipotassium glycyrrhizinate", "skin wound healing", "inflammatory"]),
    ("L01", "Lonicera", "忍冬属大花忍冬的抗炎活性成分和网络药理证据", "doi:10.1038/s41598-025-33416-6", ["Lonicera macranthoides", "anti-inflammatory", "network pharmacology"]),
    ("L02", "Lonicera", "金银花醇提物促进大鼠创面修复的动物实验", "sha256:a8d61ecec00642c9a740b6d9f56dc3f33e9d47c92b5a88ff8e4737ef5492ad7c", ["Lonicera japonica", "excision wound", "healing"]),
    ("L03", "Lonicera", "金银花中绿原酸口服后的药代动力学和组织分布", "doi:10.1155/2014/979414", ["chlorogenic acid", "Lonicerae Japonicae Flos", "pharmacokinetics"]),
    ("L04", "Lonicera", "金银花提取物对阿霉素心肌损伤的保护机制", "sha256:ec5e7484dafaec84251f946343e5f5b449e17573eb0cab31449585bcdf4bb121", ["金银花", "阿霉素", "心肌损伤"]),
    ("G01", "Glycyrrhiza", "甘草水凝胶对二度烧伤修复的组织学结果", "doi:10.3390/gels11100834", ["Glycyrrhiza glabra", "second-degree burns", "histological"]),
    ("G02", "Glycyrrhiza", "甘草提取物脂质囊泡敷料用于伤口愈合", "doi:10.3390/molecules29163811", ["Glycyrrhiza glabra", "ufasomes", "wound-healing"]),
    ("G03", "Glycyrrhiza", "甘草提取物的抗菌抗氧化与安全性评价", "doi:10.3390/plants13233265", ["Glycyrrhiza glabra", "antimicrobial", "safety"]),
    ("G04", "Glycyrrhiza", "甘草根 NADES 提取物外用和口服是否安全", "doi:10.3390/molecules30244704", ["NADES", "Glycyrrhiza roots", "safety"]),
    ("C01", "chlorogenic_acid", "绿原酸水凝胶如何兼顾 pH 监测和抗菌", "doi:10.3390/gels12060512", ["chlorogenic acid", "pH monitoring", "antibacterial"]),
    ("C02", "chlorogenic_acid", "绿原酸负载外泌体对糖尿病伤口的免疫调节", "doi:10.1186/s13062-026-00818-z", ["chlorogenic acid-loaded exosomes", "diabetic wounds", "immunomodulatory"]),
    ("C03", "chlorogenic_acid", "绿原酸调控 SRC MAPK 通路抑制胶质瘤细胞", "doi:10.2147/dddt.s296862", ["chlorogenic acid", "SRC", "MAPK"]),
    ("C04", "chlorogenic_acid", "绿原酸生物学功能和治疗潜力的系统综述", "doi:10.3390/nu16070924", ["chlorogenic acid", "systematic review", "therapeutic"]),
    ("Y01", "glycyrrhizin", "甘草酸自组装水凝胶促进正常和糖尿病小鼠皮肤愈合", "doi:10.3390/pharmaceutics15010027", ["glycyrrhizin-based hydrogels", "diabetic", "wound healing"]),
    ("Y02", "glycyrrhizin", "甘草酸二钾改善皮肤伤口时怎样调控炎症", "doi:10.3390/ijms24043839", ["dipotassium glycyrrhizinate", "inflammatory process", "wound healing"]),
    ("Y03", "glycyrrhizin", "甘草酸二钾对伤口愈合的实验研究结果", "doi:10.1590/acb360801", ["dipotassium glycyrrhizinate", "wound healing", "effects"]),
    ("Y04", "glycyrrhizin", "甘草酸抗菌活性的研究现状综述", "doi:10.3390/microorganisms12061155", ["antibacterial activities", "glycyrrhizin", "review"]),
    ("I01", "inflammation", "大花忍冬异绿原酸 C 的抗炎核心靶点", "doi:10.1038/s41598-025-33416-6", ["isochlorogenic acid C", "anti-inflammatory", "targets"]),
    ("I02", "inflammation", "金纳米酶水凝胶怎样可视化监测并治疗炎症", "doi:10.1016/j.mtbio.2024.100960", ["Au nanozyme", "hydrogel", "inflammation"]),
    ("I03", "inflammation", "金银花创面修复过程中 IL-10 的变化", "sha256:a8d61ecec00642c9a740b6d9f56dc3f33e9d47c92b5a88ff8e4737ef5492ad7c", ["IL-10", "Lonicera japonica", "wound"]),
    ("I04", "inflammation", "中药塌渍联合烧伤膏对炎症因子和创面愈合的影响", "doi:10.12669/pjms.41.6.10590", ["traditional Chinese medicine", "burn ointment", "inflammation"]),
    ("A01", "antimicrobial", "烧伤敷料的金属与天然产物抗菌策略进展", "doi:10.3390/ijms26094381", ["antimicrobial strategies", "burn wound dressings", "natural"]),
    ("A02", "antimicrobial", "烧伤愈合所用三黄粉提取物的抗菌和免疫调节作用", "doi:10.1155/2021/2900060", ["San Huang Powder", "antimicrobial", "burn wound"]),
    ("A03", "antimicrobial", "绿原酸嵌入凝胶对细菌生长的抑制表现", "doi:10.3390/gels12060512", ["chlorogenic acid", "antibacterial", "hydrogel"]),
    ("A04", "antimicrobial", "感染伤口光热纳米平台的多维抗菌机制", "doi:10.2147/ijn.s594688", ["photothermally", "antibacterial", "infected wound"]),
    ("H01", "hydrogel", "甘草提取物凝胶修复实验性烧伤的配方与疗效", "doi:10.3390/gels11100834", ["Glycyrrhiza glabra", "hydrogels", "burns"]),
    ("H02", "hydrogel", "甘草酸双网络可注射支架的协同愈合作用", "doi:10.1021/acspolymersau.5c00132", ["dual-network injectable hydrogel", "glycyrrhizic acid", "healing"]),
    ("H03", "hydrogel", "甘草提取物增强壳聚糖 PVA 明胶伤口敷料", "doi:10.3390/bioengineering12050439", ["licorice extract", "chitosan", "hydrogels"]),
    ("H04", "hydrogel", "甘草酸基水凝胶对糖尿病小鼠伤口的作用", "doi:10.3390/pharmaceutics15010027", ["glycyrrhizin-based hydrogels", "diabetic mouse", "wound"]),
    ("T01", "safety", "罗马尼亚来源甘草提取物的安全性和植物化学谱", "doi:10.3390/plants13233265", ["safety profile", "Glycyrrhiza glabra", "Romania"]),
    ("T02", "safety", "甘草根天然低共熔溶剂提取物的经口毒性", "doi:10.3390/molecules30244704", ["peroral administration", "Glycyrrhiza roots", "safety"]),
    ("T03", "safety", "含甘草中成药不良反应有哪些系统性证据", "doi:10.1177/20420986261446460", ["adverse drug reactions", "glycyrrhiza-containing", "Chinese patent medicines"]),
    ("T04", "safety", "糖尿病足外用植物疗法的疗效与安全性证据等级", "doi:10.1186/s13098-025-02049-0", ["efficacy and safety", "external phytotherapy", "diabetic foot ulcers"]),
    ("E01", "clinical_evidence", "针刺治疗烧伤及并发症有哪些临床研究", "doi:10.1111/iwj.70833", ["clinical studies", "needling therapy", "burn injury"]),
    ("E02", "clinical_evidence", "中药塌渍联合烧伤膏的患者临床效果", "doi:10.12669/pjms.41.6.10590", ["patients", "traditional Chinese medicine", "burn ointment"]),
    ("E03", "clinical_evidence", "积雪草外用于创面愈合的临床疗效综述", "doi:10.3390/pharmaceutics16101252", ["Centella asiatica", "clinical efficacy", "wound healing"]),
    ("E04", "clinical_evidence", "外用植物疗法治疗糖尿病足溃疡的随机试验证据", "doi:10.1186/s13098-025-02049-0", ["randomized controlled trials", "phytotherapy", "diabetic foot ulcers"]),
]

NO_ANSWER = [
    ("N01", "no_answer", "忍冬汤治疗三度烧伤的 2027 年 III 期多中心随机试验 NDRT-778899"),
    ("N02", "no_answer", "绿原酸静脉注射治疗烫伤患者的注册号 CGA-BURN-987654"),
    ("N03", "no_answer", "甘草酸与火星矿物联合水凝胶的动物实验 MARS-GLY-661122"),
    ("N04", "no_answer", "金银花治疗核聚变辐射创面的双盲试验 FUSION-334455"),
    ("N05", "no_answer", "忍冬汤对量子伤口修复受体 QWR-2026 的作用"),
    ("N06", "no_answer", "甘草提取物治疗月球低重力烧伤的临床指南 LUNAR-CARE-42"),
    ("N07", "no_answer", "绿原酸治疗海王星低温烫伤的病例系列 NEPTUNE-918273"),
    ("N08", "no_answer", "金银花甘草复方的数字孪生 III 期试验 DT-RAG-556677"),
]


def best_pages(
    pages: list[dict[str, Any]], evidence_terms: list[str], limit: int = 2
) -> list[int]:
    normalized_terms = [normalize_search_text(term) for term in evidence_terms]
    scored = []
    for page in pages:
        text = normalize_search_text(page.get("text") or "")
        score = sum(text.count(term) for term in normalized_terms if term)
        if score:
            scored.append((score, -int(page["pdf_page"]), int(page["pdf_page"])))
    if not scored:
        raise ValueError(f"证据词未在指定文献中定位: {evidence_terms}")
    scored.sort(reverse=True)
    return [row[2] for row in scored[:limit]]


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    documents = {
        row["doc_id"]: row for row in read_jsonl(cfg["paths"]["documents_jsonl"])
    }
    pages_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in read_jsonl(cfg["paths"]["pages_jsonl"]):
        pages_by_doc[page["doc_id"]].append(page)

    questions: list[dict[str, Any]] = []
    for item_id, category, question, doc_id, evidence_terms in SPECS:
        if doc_id not in documents:
            raise KeyError(f"未知 doc_id: {doc_id}")
        expected_pages = best_pages(pages_by_doc[doc_id], evidence_terms)
        doc = documents[doc_id]
        questions.append(
            {
                "id": item_id,
                "category": category,
                "question": question,
                "expect_answer": True,
                "expected_loci": [
                    {
                        "doc_id": doc_id,
                        "pdf_pages": expected_pages,
                        "title": doc.get("title") or "",
                        "doi": doc.get("doi") or "",
                    }
                ],
                "label_source": "curated_document_plus_original_page_evidence",
                "evidence_terms": evidence_terms,
            }
        )
    for item_id, category, question in NO_ANSWER:
        questions.append(
            {
                "id": item_id,
                "category": category,
                "question": question,
                "expect_answer": False,
                "expected_loci": [],
                "label_source": "synthetic_out_of_corpus_identifier",
                "evidence_terms": [],
            }
        )

    out = ROOT / "data" / "retrieval_questions_v2.json"
    out.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "questions": len(questions),
                "positive": len(SPECS),
                "no_answer": len(NO_ANSWER),
                "output": str(out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
