from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ANCHORS = (
    ("burn", "汤火", ("汤火", "湯火")),
    ("burn", "火烧", ("火烧", "火燒")),
    ("burn", "火疮", ("火疮", "火瘡")),
    ("burn", "灼伤", ("灼伤", "灼傷", "灼瘡")),
    ("burn", "烫伤", ("烫伤", "燙傷", "湯泡")),
    ("wound", "疮", ("疮", "瘡")),
    ("wound", "创", ("创", "創")),
    ("wound", "溃", ("溃", "潰")),
    ("wound", "腐肉", ("腐肉",)),
    ("wound", "生肌", ("生肌",)),
    ("wound", "止痛", ("止痛",)),
    ("wound", "消肿", ("消肿", "消腫")),
    ("syndrome", "热毒", ("热毒", "熱毒")),
    ("syndrome", "血瘀", ("血瘀", "瘀血")),
    ("therapy", "清热解毒", ("清热解毒", "清熱解毒")),
    ("therapy", "外敷", ("外敷", "塗敷", "涂敷")),
    ("therapy", "水煎", ("水煎",)),
    ("therapy", "酒煎", ("酒煎",)),
    ("formula", "忍冬汤", ("忍冬汤", "忍冬湯")),
    ("herb", "金银花", ("金银花", "金銀花", "忍冬花")),
    ("herb", "忍冬", ("忍冬",)),
    ("herb", "甘草", ("甘草",)),
    ("herb", "黄连", ("黄连", "黃連")),
    ("herb", "大黄", ("大黄", "大黃")),
    ("herb", "黄柏", ("黄柏", "黃柏")),
    ("safety", "禁忌", ("禁忌", "不可服", "勿服")),
    ("safety", "有毒", ("有毒", "毒性")),
    ("preparation", "研末", ("研末", "爲末", "为末")),
    ("preparation", "调敷", ("调敷", "調敷")),
    ("preparation", "煎服", ("煎服", "煎湯")),
)

NEGATIVE_QUESTIONS = (
    "哪部古籍记载了CRISPR编辑治疗烧伤？",
    "古籍是否记录了PD-1单抗治疗烫伤？",
    "古籍中哪一页描述了3D打印皮肤移植？",
    "古籍是否给出了随机双盲临床试验的P值？",
    "哪部古籍记载了mRNA疫苗修复创面？",
    "古籍是否推荐静脉注射纳米机器人治疗烧伤？",
    "古籍中是否出现了RNA测序分析？",
    "哪一页讨论了单细胞转录组烧伤研究？",
    "古籍是否报告了FAISS向量检索结果？",
    "古籍中是否记录了CT三维重建？",
    "古籍是否使用ELISA检测炎症因子？",
    "哪部古籍记载了Western blot实验？",
    "古籍中是否给出动物伦理审批号？",
    "古籍是否研究了CAR-T治疗创面？",
    "哪一页记录了现代抗生素耐药基因测序？",
    "古籍是否使用激光共聚焦显微镜？",
    "古籍是否报告了多中心前瞻性队列？",
    "古籍中是否出现了纳米酶催化动力学？",
    "古籍是否记录了现代生物信息学富集分析？",
    "哪部古籍描述了机器人自动换药？",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_extended_questions(
    database_path: Path,
    *,
    per_book: int = 10,
    minimum_positive: int = 150,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise ValueError(f"ancient database quick_check failed: {quick_check}")
        books = connection.execute(
            "SELECT book_id, title FROM books ORDER BY book_id"
        ).fetchall()
        pages_by_book: defaultdict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in connection.execute(
            "SELECT page_id, book_id, physical_page, text FROM pages "
            "ORDER BY book_id, physical_page"
        ):
            pages_by_book[str(row["book_id"])].append(row)
    finally:
        connection.close()

    positives: list[dict[str, Any]] = []
    by_book: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    for book in books:
        book_id = str(book["book_id"])
        title = str(book["title"])
        candidates: list[tuple[int, int, str, str, list[str]]] = []
        for anchor_index, (category, concept, surfaces) in enumerate(ANCHORS):
            matched_pages: list[int] = []
            matched_surfaces: set[str] = set()
            for page in pages_by_book[book_id]:
                text = str(page["text"] or "")
                found = [surface for surface in surfaces if surface in text]
                if found:
                    matched_pages.append(int(page["physical_page"]))
                    matched_surfaces.update(found)
            if matched_pages:
                candidates.append(
                    (
                        anchor_index,
                        len(matched_pages),
                        category,
                        concept,
                        sorted(matched_surfaces),
                    )
                )
        candidates.sort(key=lambda item: (item[0], item[1], item[3]))
        for _, _, category, concept, surfaces in candidates[:per_book]:
            matching_pages = [
                int(page["physical_page"])
                for page in pages_by_book[book_id]
                if any(surface in str(page["text"] or "") for surface in surfaces)
            ]
            question_id = "extended:" + _canonical_sha(
                [book_id, category, concept, surfaces]
            )[:20]
            positives.append(
                {
                    "id": question_id,
                    "category": category,
                    "question": f"请定位《{title}》中关于“{concept}”的原文页。",
                    "expect_answer": True,
                    "question_type": "source_anchored_locator",
                    "expected_loci": [
                        {
                            "doc_id": book_id,
                            "pdf_pages": matching_pages,
                            "evidence_terms": surfaces,
                        }
                    ],
                }
            )
            by_book[title] += 1
            by_category[category] += 1
    if len(positives) < minimum_positive:
        raise ValueError(
            f"only {len(positives)} positive questions; expected at least {minimum_positive}"
        )

    negatives = [
        {
            "id": f"extended-negative-{index:03d}",
            "category": "no_answer",
            "question": question,
            "expect_answer": False,
            "question_type": "out_of_corpus_boundary",
            "expected_loci": [],
        }
        for index, question in enumerate(NEGATIVE_QUESTIONS, start=1)
    ]
    questions = sorted(positives, key=lambda item: item["id"]) + negatives
    report = {
        "valid": True,
        "evaluation_version": 2,
        "benchmark_scope": "source-anchored coverage and same-name locator regression",
        "independence_note": (
            "Questions use a frozen domain anchor vocabulary and database source labels; "
            "they are not generated from retrieval rankings. This is a locator benchmark, "
            "not a blinded clinical semantic benchmark."
        ),
        "database_sha256": _sha256_file(database_path),
        "book_count": len(books),
        "positive_questions": len(positives),
        "no_answer_questions": len(negatives),
        "question_count": len(questions),
        "questions_by_book": dict(sorted(by_book.items())),
        "questions_by_category": dict(sorted(by_category.items())),
        "question_content_sha256": _canonical_sha(questions),
    }
    return questions, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the 22-book locator benchmark")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--per-book", type=int, default=10)
    parser.add_argument("--minimum-positive", type=int, default=150)
    args = parser.parse_args()
    if args.output.exists() or args.report.exists():
        raise FileExistsError("refusing to overwrite evaluation output")
    questions, report = build_extended_questions(
        args.database,
        per_book=args.per_book,
        minimum_positive=args.minimum_positive,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    temporary_report = args.report.with_suffix(args.report.suffix + ".tmp")
    temporary_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_report, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
