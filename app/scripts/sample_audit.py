#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""随机抽检不少于 20 篇文献，每篇不少于 3 页。"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_prep.config import load_config
from rag_prep.io_utils import read_jsonl


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    docs = read_jsonl(cfg["paths"]["documents_jsonl"])
    pages = read_jsonl(cfg["paths"]["pages_jsonl"])
    pages_by_doc: dict[str, list] = defaultdict(list)
    for p in pages:
        pages_by_doc[p["doc_id"]].append(p)

    def has_doi(d):
        return bool(d.get("doi"))

    def lang_of(d):
        return (d.get("language") or "").lower()

    pools = {
        "zh": [d for d in docs if lang_of(d) == "zh" and len(pages_by_doc[d["doc_id"]]) >= 3],
        "en": [d for d in docs if lang_of(d) == "en" and len(pages_by_doc[d["doc_id"]]) >= 3],
        "long": [
            d
            for d in docs
            if int(d.get("page_count") or 0) >= 15 and len(pages_by_doc[d["doc_id"]]) >= 3
        ],
        "short": [
            d
            for d in docs
            if 0 < int(d.get("page_count") or 0) <= 5 and len(pages_by_doc[d["doc_id"]]) >= 1
        ],
        "with_doi": [d for d in docs if has_doi(d) and len(pages_by_doc[d["doc_id"]]) >= 3],
        "no_doi": [d for d in docs if not has_doi(d) and len(pages_by_doc[d["doc_id"]]) >= 3],
        "needs_ocr": [
            d
            for d in docs
            if d.get("extraction_status") == "needs_ocr" or not d.get("has_text_layer")
        ],
        "garbled_or_empty": [
            d
            for d in docs
            if int(d.get("empty_page_count") or 0) > 0
            or d.get("extraction_status") == "needs_ocr"
        ],
    }

    rng = random.Random(20260727)
    selected: dict[str, dict] = {}

    def pick(name: str, n: int):
        cand = [d for d in pools[name] if d["doc_id"] not in selected]
        rng.shuffle(cand)
        for d in cand[:n]:
            selected[d["doc_id"]] = d

    pick("zh", 3)
    pick("en", 5)
    pick("long", 3)
    pick("short", 3)
    pick("with_doi", 3)
    pick("no_doi", 2)
    pick("needs_ocr", 2)
    pick("garbled_or_empty", 2)

    # 补足到至少 20
    rest = [d for d in docs if d["doc_id"] not in selected and pages_by_doc[d["doc_id"]]]
    rng.shuffle(rest)
    for d in rest:
        if len(selected) >= 20:
            break
        selected[d["doc_id"]] = d

    rows = []
    for did, d in selected.items():
        plist = sorted(pages_by_doc[did], key=lambda x: int(x["pdf_page"]))
        sample_pages = plist[: max(3, min(3, len(plist)))] if len(plist) >= 3 else plist
        # 确保至少抽 3 页（不足则全取）
        if len(plist) >= 3:
            idxs = sorted(rng.sample(range(len(plist)), 3))
            sample_pages = [plist[i] for i in idxs]
        page_summaries = []
        for p in sample_pages:
            text = p.get("text") or ""
            page_summaries.append(
                {
                    "pdf_page": p.get("pdf_page"),
                    "char_count": p.get("text_char_count"),
                    "is_empty": p.get("is_empty"),
                    "quality_flags": p.get("quality_flags"),
                    "text_preview": text[:240].replace("\n", " "),
                }
            )
        rows.append(
            {
                "doc_id": did,
                "title": d.get("title"),
                "year": d.get("year"),
                "doi": d.get("doi"),
                "source_filename": d.get("source_filename"),
                "language": d.get("language"),
                "page_count": d.get("page_count"),
                "total_text_chars": d.get("total_text_chars"),
                "empty_page_count": d.get("empty_page_count"),
                "has_text_layer": d.get("has_text_layer"),
                "extraction_status": d.get("extraction_status"),
                "relevance_score": d.get("relevance_score"),
                "topic_tags": d.get("topic_tags"),
                "sampled_pages": page_summaries,
            }
        )

    out = Path(cfg["paths"]["data_dir"]) / "sample_audit_20.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    md = Path(cfg["paths"]["data_dir"]) / "sample_audit_20.md"
    lines = ["# 抽检报告（20 篇）", ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"## {i}. {r['title'] or r['source_filename']}")
        lines.append(f"- doc_id: `{r['doc_id']}`")
        lines.append(f"- year/doi/lang: {r['year']} / {r['doi'] or '(无)'} / {r['language']}")
        lines.append(
            f"- pages/chars/status: {r['page_count']} / {r['total_text_chars']} / {r['extraction_status']}"
        )
        lines.append(f"- relevance: {r['relevance_score']} tags={r['topic_tags']}")
        for p in r["sampled_pages"]:
            lines.append(
                f"  - p{p['pdf_page']}: chars={p['char_count']} empty={p['is_empty']} "
                f"flags={p['quality_flags']}"
            )
            lines.append(f"    preview: {p['text_preview'][:180]}")
        lines.append("")
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"sampled={len(rows)}")
    print(f"json={out}")
    print(f"md={md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
