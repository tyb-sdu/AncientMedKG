from __future__ import annotations

import hashlib
import re


DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    text = str(doi).strip()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = text.strip().rstrip(".")
    return text.lower()


def is_valid_doi(doi: str | None) -> bool:
    """校验 DOI 语法与最低完整性，截断的期刊名不能作为稳定 ID。"""
    norm = normalize_doi(doi)
    if not norm:
        return False
    if not DOI_RE.fullmatch(norm):
        return False
    suffix = norm.split("/", 1)[1]
    if len(suffix) < 4:
        return False
    # 本数据集中曾出现的典型截断形式。
    if norm.startswith("10.1371/") and not re.fullmatch(
        r"10\.1371/journal\.[a-z0-9]+\.\d+", norm
    ):
        return False
    if norm.startswith("10.2147/") and "." not in suffix:
        return False
    if norm.startswith("10.3390/") and not re.search(r"\d", suffix):
        return False
    if norm.startswith("10.1016/") and suffix.startswith("j.") and suffix.count(".") < 2:
        return False
    return True


def doi_validation_reason(doi: str | None) -> str:
    norm = normalize_doi(doi)
    if not norm:
        return "missing"
    if not DOI_RE.fullmatch(norm):
        return "syntax_invalid"
    suffix = norm.split("/", 1)[1]
    if len(suffix) < 4:
        return "suffix_too_short"
    if not is_valid_doi(norm):
        return "publisher_pattern_incomplete"
    return ""


def make_doc_id(doi: str | None, sha256: str) -> str:
    """稳定 doc_id：优先规范化 DOI，否则使用完整 SHA-256。"""
    norm = normalize_doi(doi)
    if norm and is_valid_doi(norm):
        return f"doi:{norm}"
    if not sha256:
        raise ValueError("缺少 DOI 与 SHA-256，无法生成 doc_id")
    return f"sha256:{sha256.lower()}"


def make_chunk_id(doc_id: str, pdf_page: int, chunk_index: int) -> str:
    """稳定可复现 chunk_id。"""
    raw = f"{doc_id}|p{pdf_page}|c{chunk_index}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{digest}_p{pdf_page:04d}_c{chunk_index:03d}"


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
