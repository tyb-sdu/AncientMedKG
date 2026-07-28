from __future__ import annotations

import re
import unicodedata


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?")


def detect_language(text: str) -> str:
    """按有效字符占比识别 zh/en/mixed，避免英文摘要淹没中文正文。"""
    if not text or not text.strip():
        return ""
    cjk = len(CJK_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    total = cjk + latin
    if total == 0:
        return ""
    ratio = cjk / total
    if cjk >= 50 and latin >= 50 and 0.10 <= ratio <= 0.70:
        return "mixed"
    if cjk >= 20 and ratio >= 0.20:
        return "zh"
    if cjk >= 20 and latin >= 20 and ratio >= 0.05:
        return "mixed"
    if latin:
        return "en"
    return "zh" if cjk else ""


def detect_document_language(texts: list[str], title: str = "") -> str:
    """聚合全文识别文档主语言；标题用于短文档的补充证据。"""
    body = "\n".join(t for t in texts if t)
    sample = f"{title}\n{body}" if title else body
    cjk = len(CJK_RE.findall(sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    total = cjk + latin
    if total == 0:
        return ""
    ratio = cjk / total
    if cjk >= 200 and ratio >= 0.20:
        return "zh"
    if cjk >= 100 and latin >= 100 and ratio >= 0.05:
        return "mixed"
    return "en" if latin else "zh"


def count_words_en(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def is_garbled(text: str, threshold: float = 0.15) -> bool:
    """检测高比例替换字符/控制字符等乱码迹象。"""
    if not text:
        return False
    sample = text[:5000]
    if not sample:
        return False
    bad = 0
    for ch in sample:
        if ch == "\ufffd":
            bad += 1
            continue
        cat = unicodedata.category(ch)
        if cat in {"Cc", "Co", "Cs"} and ch not in "\n\r\t":
            bad += 1
    return (bad / max(len(sample), 1)) >= threshold


def clean_page_text(text: str) -> str:
    """轻量清洗：不删除否定词、剂量、单位、统计结果。"""
    if not text:
        return ""
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 常见连字
    text = (
        text.replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\u00ad", "")
    )
    # 保留段落边界：压缩行内多余空白，保留空行
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        lines.append(line)
    # 合并被硬换行打断的英文单词（仅 hyphen+换行）
    joined: list[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if cur.endswith("-") and i + 1 < len(lines) and lines[i + 1]:
            nxt = lines[i + 1]
            if re.match(r"^[A-Za-z]", nxt):
                cur = cur[:-1] + nxt
                i += 2
                joined.append(cur)
                continue
        joined.append(cur)
        i += 1

    # 压缩连续空行到最多一个
    out_lines: list[str] = []
    blank = 0
    for line in joined:
        if not line:
            blank += 1
            if blank <= 1:
                out_lines.append("")
        else:
            blank = 0
            out_lines.append(line)
    return "\n".join(out_lines).strip()


def section_hint(text: str) -> str:
    if not text:
        return ""
    first = text.strip().split("\n", 1)[0].strip()
    if not first:
        return ""
    # 标题样行：短、可能全大写/编号
    if len(first) <= 120:
        if re.match(
            r"^(abstract|introduction|methods?|results?|discussion|conclusion|"
            r"references|acknowledg|摘要|引言|方法|结果|讨论|结论|参考文献)",
            first,
            re.IGNORECASE,
        ):
            return first[:120]
        if re.match(r"^(\d+(\.\d+)*|[IVXLC]+)[\s\.、]", first):
            return first[:120]
    return first[:80]
