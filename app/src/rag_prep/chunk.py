from __future__ import annotations

import re
from typing import Any

from .ids import make_chunk_id
from .text_utils import count_words_en, detect_language, section_hint
from .topics import score_topics


PARA_SPLIT = re.compile(r"\n\s*\n+")


def _target_range(language: str, cfg: dict[str, Any]) -> tuple[int, int]:
    c = cfg.get("chunking", {})
    if language == "zh":
        return int(c.get("zh_target_chars_min", 800)), int(c.get("zh_target_chars_max", 1500))
    return int(c.get("en_target_words_min", 600)), int(c.get("en_target_words_max", 1000))


def _size(text: str, language: str) -> int:
    if language == "zh":
        return len(text)
    return count_words_en(text)


def _hard_split(text: str, language: str, max_size: int) -> list[str]:
    """对超长无标点文本做硬切，保证不超过目标上限太多。"""
    text = text.strip()
    if not text:
        return []
    if _size(text, language) <= max_size:
        return [text]
    out: list[str] = []
    if language == "zh":
        step = max_size
        for i in range(0, len(text), step):
            piece = text[i : i + step].strip()
            if piece:
                out.append(piece)
        return out
    words = text.split()
    buf: list[str] = []
    for w in words:
        buf.append(w)
        if len(buf) >= max_size:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def _split_units(text: str, language: str) -> list[str]:
    parts = [p.strip() for p in PARA_SPLIT.split(text) if p.strip()]
    if not parts:
        return [text.strip()] if text.strip() else []

    max_unit = 1500 if language == "zh" else 1000
    units: list[str] = []
    for part in parts:
        if language == "zh":
            if len(part) <= max_unit:
                units.append(part)
                continue
            sents = [s for s in re.split(r"(?<=[。！？；])", part) if s.strip()]
        else:
            if count_words_en(part) <= max_unit:
                units.append(part)
                continue
            sents = [s for s in re.split(r"(?<=[.!?])\s+", part) if s.strip()]

        if not sents:
            units.extend(_hard_split(part, language, max_unit))
            continue

        buf = ""
        for s in sents:
            s = s.strip()
            if not buf:
                buf = s
                continue
            sep = "" if language == "zh" else " "
            candidate = buf + sep + s
            too_long = _size(candidate, language) > max_unit
            if too_long:
                units.extend(_hard_split(buf, language, max_unit))
                buf = s
            else:
                buf = candidate
        if buf:
            units.extend(_hard_split(buf, language, max_unit))
    return units


def _locate_units(text: str, units: list[str]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    search_from = 0
    for unit in units:
        idx = text.find(unit, search_from)
        if idx < 0:
            idx = search_from
        start = idx
        end = idx + len(unit)
        spans.append((start, end, unit))
        search_from = max(end, search_from)
    return spans


def _overlap_tail(text: str, language: str, ratio: float) -> tuple[str, int]:
    if ratio <= 0 or not text:
        return "", 0
    if language == "zh":
        n = max(1, int(len(text) * ratio))
        tail = text[-n:]
        return tail, len(tail)
    words = text.split()
    n = max(1, int(len(words) * ratio))
    tail = " ".join(words[-n:])
    return tail, len(tail)


def chunk_page_text(
    text: str,
    *,
    doc_id: str,
    pdf_page: int,
    language: str,
    title: str,
    year: str | int | None,
    doi: str,
    source_filename: str,
    sha256: str,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """同一 PDF 页内切块，禁止跨页。"""
    text = text or ""
    if not text.strip():
        return []

    lang = language or detect_language(text) or "en"
    lo, hi = _target_range(lang, cfg)
    overlap_ratio = float(cfg.get("chunking", {}).get("overlap_ratio", 0.08))
    min_chars = int(cfg.get("chunking", {}).get("min_chunk_chars", 80))

    units = _locate_units(text, _split_units(text, lang))
    chunks: list[dict[str, Any]] = []

    buf_units: list[str] = []
    buf_start = 0
    buf_end = 0
    pending_overlap_chars = 0
    overlap_prefix = ""

    def body_of(parts: list[str], prefix: str = "") -> str:
        core = "\n\n".join(parts).strip()
        if prefix and core:
            return (prefix + "\n\n" + core).strip()
        return (prefix or core).strip()

    def emit(parts: list[str], start: int, end: int, overlap_chars: int, prefix: str) -> None:
        body = body_of(parts, prefix)
        if not body:
            return
        # 短页保留；正常块低于 min_chars 且已有块则丢弃
        if len(body) < min_chars and chunks:
            return
        idx = len(chunks)
        topic = score_topics(body, title=title)
        chunks.append(
            {
                "chunk_id": make_chunk_id(doc_id, pdf_page, idx),
                "doc_id": doc_id,
                "pdf_page": pdf_page,
                "chunk_index": idx,
                "text": body,
                "title": title or "",
                "year": year if year not in (None, "") else "",
                "doi": doi or "",
                "source_filename": source_filename,
                "sha256": sha256,
                "section_hint": section_hint(body),
                "topic_tags": topic["topic_tags"],
                "relevance_score": topic["relevance_score"],
                "relevance_evidence": topic["relevance_evidence"],
                "char_start": start,
                "char_end": end,
                "overlap_chars": overlap_chars,
                "language": lang,
            }
        )

    for start, end, unit in units:
        if not buf_units:
            buf_units = [unit]
            buf_start = start
            buf_end = end
            continue

        trial_parts = buf_units + [unit]
        trial = body_of(trial_parts, overlap_prefix)
        if _size(trial, lang) <= hi:
            buf_units.append(unit)
            buf_end = end
            continue

        # 当前缓冲已够大则输出；否则仍并入（单段超长）
        cur = body_of(buf_units, overlap_prefix)
        if _size(cur, lang) >= lo or _size(unit, lang) > hi:
            emit(buf_units, buf_start, buf_end, pending_overlap_chars, overlap_prefix)
            overlap_prefix, pending_overlap_chars = _overlap_tail(
                chunks[-1]["text"] if chunks else "", lang, overlap_ratio
            )
            # 重叠文本不计入 char_start（char_* 对应本页原文区间）
            buf_units = [unit]
            buf_start = start
            buf_end = end
        else:
            buf_units.append(unit)
            buf_end = end

    if buf_units:
        emit(buf_units, buf_start, buf_end, pending_overlap_chars, overlap_prefix)

    # 整页过短：强制一页一块
    if not chunks and text.strip():
        topic = score_topics(text, title=title)
        chunks.append(
            {
                "chunk_id": make_chunk_id(doc_id, pdf_page, 0),
                "doc_id": doc_id,
                "pdf_page": pdf_page,
                "chunk_index": 0,
                "text": text.strip(),
                "title": title or "",
                "year": year if year not in (None, "") else "",
                "doi": doi or "",
                "source_filename": source_filename,
                "sha256": sha256,
                "section_hint": section_hint(text),
                "topic_tags": topic["topic_tags"],
                "relevance_score": topic["relevance_score"],
                "relevance_evidence": topic["relevance_evidence"],
                "char_start": 0,
                "char_end": len(text),
                "overlap_chars": 0,
                "language": lang,
            }
        )

    for c in chunks:
        c["pdf_page"] = int(pdf_page)
    return chunks
