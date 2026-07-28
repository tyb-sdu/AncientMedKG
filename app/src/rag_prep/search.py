from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .ids import is_valid_doi, sha256_file
from .inventory import list_pdfs
from .io_utils import read_jsonl


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*")
EN_STOP = {
    "a", "an", "and", "are", "for", "how", "in", "is", "of", "on", "or",
    "the", "to", "what", "with", "mechanism", "study", "effect", "effects",
}
ZH_STOP = {"什么", "如何", "研究", "作用", "机制", "相关", "文献", "有哪些", "是否"}
CONCEPTS = {
    "烧伤": ["烧伤", "烫伤", "burn", "scald"],
    "创面修复": ["创面修复", "创面愈合", "伤口愈合", "wound healing", "wound repair"],
    "创面愈合": ["创面愈合", "创面修复", "wound healing"],
    "忍冬": ["忍冬", "金银花", "lonicera", "honeysuckle"],
    "金银花": ["金银花", "忍冬", "lonicera", "honeysuckle"],
    "甘草": ["甘草", "glycyrrhiza", "licorice", "liquorice"],
    "绿原酸": ["绿原酸", "chlorogenic acid", "chlorogenic"],
    "甘草酸": ["甘草酸", "甘草甜素", "glycyrrhizin", "glycyrrhizic acid"],
    "炎症": ["炎症", "抗炎", "inflammation", "inflammatory"],
    "抗菌": ["抗菌", "抑菌", "antimicrobial", "antibacterial"],
    "水凝胶": ["水凝胶", "hydrogel"],
    "二度烧伤": ["second-degree burn", "second degree burns"],
    "烧伤创面": ["burn wound", "burn wound healing"],
    "烫伤": ["scald", "thermal burn"],
    "热损伤": ["thermal injury", "burn injury"],
    "红糖": ["brown sugar"],
    "胶原": ["collagen"],
    "氧化应激": ["oxidative stress"],
    "纳米酶": ["nanozyme", "nanozymes"],
    "针刺": ["needling therapy", "acupuncture"],
    "炎症小体": ["inflammasome"],
    "艾纳香": ["Blumea balsamifera"],
    "细胞外囊泡": ["extracellular vesicles"],
    "铜绿假单胞菌": ["Pseudomonas aeruginosa"],
    "生物膜": ["biofilm", "biofilms"],
    "切除性伤口": ["excision wound"],
    "糖尿病创面": ["diabetic wound", "diabetic wound healing"],
    "糖尿病伤口": ["diabetic wound", "diabetic wound healing"],
    "外泌体": ["exosome", "exosomes"],
    "免疫调节": ["immunomodulatory", "immunomodulation"],
    "甘草酸二钾": ["dipotassium glycyrrhizinate"],
    "大花忍冬": ["Lonicera macranthoides"],
    "异绿原酸": ["isochlorogenic acid"],
    "药代动力学": ["pharmacokinetics"],
    "组织分布": ["tissue distribution"],
    "阿霉素": ["doxorubicin"],
    "心肌损伤": ["myocardial injury"],
    "脱细胞脂肪": ["acellular fat extract"],
    "双网络": ["dual-network", "dual network"],
    "低共熔溶剂": ["natural deep eutectic solvent", "NADES"],
    "胶质瘤": ["glioma"],
    "系统综述": ["systematic review"],
    "金纳米酶": ["Au nanozyme", "gold nanozyme"],
    "炎症因子": ["inflammatory cytokines"],
    "三黄粉": ["San Huang Powder"],
    "光热": ["photothermal", "photothermally"],
    "壳聚糖": ["chitosan"],
    "明胶": ["gelatin"],
    "不良反应": ["adverse drug reactions"],
    "中成药": ["Chinese patent medicines"],
    "糖尿病足": ["diabetic foot ulcers"],
    "外用植物疗法": ["external phytotherapy", "topical phytotherapy"],
    "临床研究": ["clinical studies", "clinical evidence"],
    "积雪草": ["Centella asiatica"],
    "随机试验": ["randomized controlled trials", "randomized trial"],
}


def normalize_search_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def chinese_search_tokens(text: str, min_n: int = 1, max_n: int = 2) -> str:
    """生成空格分隔的中文字符 n-gram，供 unicode61 FTS5 精确分词。"""
    sequences = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", text or "")
    tokens: list[str] = []
    for seq in sequences:
        for n in range(min_n, max_n + 1):
            tokens.extend(seq[i : i + n] for i in range(0, len(seq) - n + 1))
    return " ".join(tokens)


def _query_terms(question: str) -> list[str]:
    normalized = normalize_search_text(question)
    expanded = [normalized]
    for key, values in CONCEPTS.items():
        if key in question:
            expanded.extend(values)
    terms: list[str] = []
    for value in expanded:
        for token in TOKEN_RE.findall(normalize_search_text(value)):
            if len(token) > 1 and token not in EN_STOP and token not in terms:
                terms.append(token)
        cjk_runs = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", value)
        for run in cjk_runs:
            cleaned = run
            for stop in ZH_STOP:
                cleaned = cleaned.replace(stop, " ")
            for part in cleaned.split():
                ngrams = (
                    [part]
                    if len(part) <= 2
                    else [part[i : i + 2] for i in range(len(part) - 1)]
                )
                for token in ngrams:
                    if token and token not in terms:
                        terms.append(token)
    return terms[:40]


def _fts_expression(question: str) -> str:
    def quoted(items: list[str]) -> str:
        return " OR ".join('"' + t.replace('"', '""') + '"' for t in items)

    identifiers = [
        token
        for token in TOKEN_RE.findall(normalize_search_text(question))
        if re.search(r"[a-z]", token)
        and re.search(r"\d", token)
        and len(token) >= 6
    ]
    if identifiers:
        return " AND ".join(
            '"' + token.replace('"', '""') + '"' for token in identifiers
        )

    groups: list[list[str]] = []
    for key, values in CONCEPTS.items():
        if key not in question:
            continue
        terms: list[str] = []
        for value in values:
            terms.extend(TOKEN_RE.findall(normalize_search_text(value)))
            for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", value):
                terms.extend(
                    [run] if len(run) <= 2
                    else [run[i : i + 2] for i in range(len(run) - 1)]
                )
        groups.append(list(dict.fromkeys(t for t in terms if t)))
    if groups:
        return " AND ".join(f"({quoted(group)})" for group in groups)

    terms = _query_terms(question)
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _data_fingerprint(paths: dict[str, str]) -> str:
    h = hashlib.sha256()
    for key in ("documents_jsonl", "pages_jsonl", "chunks_jsonl"):
        path = Path(paths[key])
        stat = path.stat()
        h.update(f"{key}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return h.hexdigest()


def _connect(db_path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    path = Path(db_path)
    if readonly:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def build_index(
    cfg: dict[str, Any],
    logger,
    *,
    resume: bool = True,
    force: bool = False,
    doc_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    paths = cfg["paths"]
    db_path = Path(paths["database"])
    fingerprint = _data_fingerprint(paths)
    if db_path.exists() and resume and not force and not doc_id and not limit:
        try:
            with _connect(db_path, readonly=True) as conn:
                saved = conn.execute(
                    "SELECT value FROM metadata WHERE key='data_fingerprint'"
                ).fetchone()
                if saved and saved[0] == fingerprint:
                    counts = database_counts(conn)
                    logger.info("索引数据未变化，断点续跑跳过重建: %s", counts)
                    return {**counts, "resumed": True}
        except sqlite3.Error:
            pass

    docs = read_jsonl(paths["documents_jsonl"])
    pages = read_jsonl(paths["pages_jsonl"])
    chunks = read_jsonl(paths["chunks_jsonl"])
    if doc_id:
        docs = [d for d in docs if d["doc_id"] == doc_id]
    if limit:
        docs = docs[:limit]
    selected = {d["doc_id"] for d in docs}
    pages = [p for p in pages if p["doc_id"] in selected]
    chunks = [c for c in chunks if c["doc_id"] in selected]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".db.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    with _connect(tmp_path) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                year TEXT,
                doi TEXT,
                doi_status TEXT,
                source_filename TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL UNIQUE,
                page_count INTEGER,
                language TEXT,
                has_text_layer INTEGER,
                total_text_chars INTEGER,
                empty_page_count INTEGER,
                extraction_status TEXT,
                relevance_score INTEGER,
                topic_tags TEXT,
                field_notes TEXT
            );
            CREATE TABLE pages (
                doc_id TEXT NOT NULL,
                pdf_page INTEGER NOT NULL CHECK(pdf_page >= 1),
                page_label TEXT,
                text TEXT NOT NULL,
                language TEXT,
                text_char_count INTEGER,
                is_empty INTEGER NOT NULL DEFAULT 0,
                quality_flags TEXT,
                PRIMARY KEY(doc_id, pdf_page),
                FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                pdf_page INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                zh_tokens TEXT NOT NULL,
                section_hint TEXT,
                language TEXT,
                relevance_score INTEGER,
                topic_tags TEXT,
                char_start INTEGER,
                char_end INTEGER,
                overlap_chars INTEGER,
                FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
                FOREIGN KEY(doc_id, pdf_page) REFERENCES pages(doc_id, pdf_page)
            );
            CREATE INDEX idx_pages_doc ON pages(doc_id, pdf_page);
            CREATE INDEX idx_chunks_doc_page ON chunks(doc_id, pdf_page);
            CREATE INDEX idx_documents_doi ON documents(doi);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_id UNINDEXED,
                title,
                normalized_text,
                zh_tokens,
                topic_tags,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        conn.executemany(
            """INSERT INTO documents VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    d["doc_id"], d.get("title") or "", str(d.get("year") or ""),
                    d.get("doi") or "", d.get("doi_status") or "",
                    d["source_filename"], d["sha256"], int(d.get("page_count") or 0),
                    d.get("language") or "", int(bool(d.get("has_text_layer"))),
                    int(d.get("total_text_chars") or 0),
                    int(d.get("empty_page_count") or 0),
                    d.get("extraction_status") or "",
                    int(d.get("relevance_score") or 0),
                    json.dumps(d.get("topic_tags") or [], ensure_ascii=False),
                    json.dumps(d.get("field_notes") or [], ensure_ascii=False),
                )
                for d in docs
            ],
        )
        conn.executemany(
            "INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    p["doc_id"], int(p["pdf_page"]), p.get("page_label") or "",
                    p.get("text") or "", p.get("language") or "",
                    int(p.get("text_char_count") or 0), int(bool(p.get("is_empty"))),
                    json.dumps(p.get("quality_flags") or [], ensure_ascii=False),
                )
                for p in pages
            ],
        )
        chunk_rows = []
        fts_rows = []
        ncfg = cfg.get("search", {})
        min_n = int(ncfg.get("chinese_ngram_min", 1))
        max_n = int(ncfg.get("chinese_ngram_max", 2))
        title_by_doc = {d["doc_id"]: d.get("title") or "" for d in docs}
        for c in chunks:
            normalized = normalize_search_text(c.get("text") or "")
            zh_tokens = chinese_search_tokens(c.get("text") or "", min_n, max_n)
            tags = json.dumps(c.get("topic_tags") or [], ensure_ascii=False)
            chunk_rows.append(
                (
                    c["chunk_id"], c["doc_id"], int(c["pdf_page"]),
                    int(c.get("chunk_index") or 0), c.get("text") or "", normalized,
                    zh_tokens, c.get("section_hint") or "", c.get("language") or "",
                    int(c.get("relevance_score") or 0), tags,
                    int(c.get("char_start") or 0), int(c.get("char_end") or 0),
                    int(c.get("overlap_chars") or 0),
                )
            )
            title = title_by_doc[c["doc_id"]]
            fts_rows.append(
                (
                    c["chunk_id"], title, normalized,
                    chinese_search_tokens(f"{title} {c.get('text') or ''}", min_n, max_n),
                    " ".join(c.get("topic_tags") or []),
                )
            )
        conn.executemany(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", chunk_rows
        )
        conn.executemany(
            "INSERT INTO chunks_fts VALUES (?,?,?,?,?)", fts_rows
        )
        conn.executemany(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            [
                ("data_fingerprint", fingerprint),
                ("documents", str(len(docs))),
                ("pages", str(len(pages))),
                ("chunks", str(len(chunks))),
                ("schema_version", "1"),
            ],
        )
        conn.commit()
        counts = database_counts(conn)
    conn.close()
    os.replace(tmp_path, db_path)
    logger.info("SQLite FTS5 索引完成: %s", counts)
    return {**counts, "resumed": False}


def database_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "documents": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
        "pages": conn.execute("SELECT count(*) FROM pages").fetchone()[0],
        "chunks": conn.execute("SELECT count(*) FROM chunks").fetchone()[0],
        "fts_rows": conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0],
    }


def _snippet(text: str, question: str, size: int) -> str:
    normalized = normalize_search_text(text)
    positions = [
        normalized.find(normalize_search_text(term))
        for term in _query_terms(question)
        if normalize_search_text(term)
    ]
    positions = [p for p in positions if p >= 0]
    start = max(0, (min(positions) if positions else 0) - size // 3)
    end = min(len(text), start + size)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].replace("\n", " ") + suffix


def query_index(
    cfg: dict[str, Any], question: str, top_k: int | None = None
) -> list[dict[str, Any]]:
    if not question.strip():
        raise ValueError("查询不能为空")
    db_path = Path(cfg["paths"]["database"])
    if not db_path.exists():
        raise FileNotFoundError("rag.db 不存在，请先运行 index")
    top_k = top_k or int(cfg.get("search", {}).get("default_top_k", 10))
    top_k = min(top_k, int(cfg.get("search", {}).get("max_top_k", 100)))
    expression = _fts_expression(question)
    if not expression:
        return []
    with _connect(db_path, readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT c.*, d.title, d.year, d.doi, d.source_filename, d.sha256,
                   bm25(chunks_fts, 0.0, 4.0, 1.0, 1.8, 1.5) AS bm25_score
            FROM chunks_fts
            JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE chunks_fts MATCH ?
            ORDER BY bm25_score
            LIMIT ?
            """,
            (expression, max(top_k * 8, 50)),
        ).fetchall()
    qnorm = normalize_search_text(question)
    aliases = [
        normalize_search_text(alias)
        for key, values in CONCEPTS.items()
        if key in question
        for alias in values
    ]
    results: list[dict[str, Any]] = []
    for row in rows:
        title_norm = normalize_search_text(row["title"])
        text_norm = normalize_search_text(row["text"])
        exact = 1 if qnorm and qnorm in f"{title_norm} {text_norm}" else 0
        alias_title = any(alias and alias in title_norm for alias in aliases)
        alias_text = any(alias and alias in text_norm for alias in aliases)
        lexical = max(0.0, -float(row["bm25_score"]))
        score = (
            lexical
            + exact * 5.0
            + int(alias_title) * 20.0
            + int(alias_text) * 5.0
            + int(row["relevance_score"] or 0) / 1000
        )
        results.append(
            {
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "title": row["title"],
                "year": row["year"],
                "doi": row["doi"],
                "pdf_page": row["pdf_page"],
                "source_filename": row["source_filename"],
                "sha256": row["sha256"],
                "relevance": round(score, 6),
                "keyword_score": round(score, 6),
                "vector_score": None,
                "keyword_rank": None,
                "vector_rank": None,
                "fusion_score": None,
                "fusion_rank": None,
                "snippet": _snippet(
                    row["text"], question,
                    int(cfg.get("search", {}).get("snippet_chars", 360)),
                ),
            }
        )
    results.sort(key=lambda r: (-r["relevance"], r["doc_id"], r["pdf_page"]))
    results = results[:top_k]
    for rank, result in enumerate(results, 1):
        result["keyword_rank"] = rank
        result["fusion_rank"] = rank
    return results


def rrf_fuse(
    keyword_results: list[dict[str, Any]],
    vector_results: list[dict[str, Any]],
    *,
    top_k: int,
    rrf_k: int = 60,
    keyword_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> list[dict[str, Any]]:
    """按稳定 chunk_id 融合两条独立排名通道。"""
    merged: dict[str, dict[str, Any]] = {}
    for channel, rows in (("keyword", keyword_results), ("vector", vector_results)):
        for rank, row in enumerate(rows, 1):
            chunk_id = row["chunk_id"]
            target = merged.setdefault(chunk_id, dict(row))
            target[f"{channel}_rank"] = rank
            target[f"{channel}_score"] = row.get(f"{channel}_score")
            target["fusion_score"] = float(target.get("fusion_score") or 0.0)
            weight = keyword_weight if channel == "keyword" else vector_weight
            target["fusion_score"] += weight / (rrf_k + rank)
    results = sorted(
        merged.values(),
        key=lambda row: (
            -float(row.get("fusion_score") or 0.0),
            min(
                int(row.get("keyword_rank") or 10**9),
                int(row.get("vector_rank") or 10**9),
            ),
            row["chunk_id"],
        ),
    )[:top_k]
    for rank, row in enumerate(results, 1):
        row["fusion_score"] = round(float(row["fusion_score"]), 8)
        row["fusion_rank"] = rank
        row.setdefault("keyword_score", None)
        row.setdefault("vector_score", None)
        row.setdefault("keyword_rank", None)
        row.setdefault("vector_rank", None)
    return results


def _diversify_results(
    cfg: dict[str, Any],
    rows: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    cap = int(cfg.get("search", {}).get("max_chunks_per_document", 3))
    counts: Counter[tuple[str, str]] = Counter()
    selected = []
    for row in rows:
        corpus = str(row.get("corpus") or "modern")
        key = (corpus, row["doc_id"])
        if counts[key] >= cap:
            continue
        counts[key] += 1
        selected.append(row)
        if len(selected) >= top_k:
            break
    for rank, row in enumerate(selected, 1):
        row["fusion_rank"] = rank
    return selected


def query_modern_retrieval(
    cfg: dict[str, Any],
    question: str,
    retrieval: str,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    top_k = top_k or int(cfg.get("search", {}).get("default_top_k", 10))
    top_k = min(top_k, int(cfg.get("search", {}).get("max_top_k", 100)))
    candidate_count = min(
        max(top_k * 4, top_k),
        int(cfg.get("search", {}).get("max_top_k", 100)),
    )

    def diversify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = [dict(row, corpus=row.get("corpus") or "modern") for row in rows]
        return _diversify_results(cfg, rows, top_k)

    if retrieval == "keyword":
        return diversify(query_index(cfg, question, candidate_count))
    from .vector import query_vector

    if retrieval == "vector":
        return diversify(query_vector(cfg, question, candidate_count))
    if retrieval == "hybrid":
        candidates = max(
            top_k, int(cfg.get("search", {}).get("vector_candidates", 60))
        )
        keyword = query_index(cfg, question, candidates)
        vector = query_vector(cfg, question, candidates, candidate_k=candidates)
        fused = rrf_fuse(
            keyword,
            vector,
            top_k=candidates,
            rrf_k=int(cfg.get("search", {}).get("rrf_k", 60)),
            keyword_weight=float(
                cfg.get("search", {}).get("rrf_keyword_weight", 1.5)
            ),
            vector_weight=float(
                cfg.get("search", {}).get("rrf_vector_weight", 1.0)
            ),
        )
        return diversify(fused)
    if retrieval == "bge-vector":
        from .gpu_retrieval import query_bge_vector

        return diversify(query_bge_vector(cfg, question, candidate_count))
    if retrieval == "reranked-hybrid":
        from .gpu_retrieval import query_reranked_hybrid

        return [dict(row, corpus=row.get("corpus") or "modern") for row in query_reranked_hybrid(cfg, question, top_k)]
    if retrieval == "qwen-vector":
        from .qwen_retrieval import query_qwen_vector

        return diversify(query_qwen_vector(cfg, question, candidate_count))
    if retrieval == "qwen-reranked-hybrid":
        from .qwen_retrieval import query_qwen_reranked_hybrid

        return [dict(row, corpus=row.get("corpus") or "modern") for row in query_qwen_reranked_hybrid(cfg, question, top_k)]
    raise ValueError(f"未知检索模式: {retrieval}")


def query_retrieval(
    cfg: dict[str, Any],
    question: str,
    retrieval: str,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    top_k = top_k or int(cfg.get("search", {}).get("default_top_k", 10))
    top_k = min(top_k, int(cfg.get("search", {}).get("max_top_k", 100)))
    candidate_count = min(
        max(top_k * 4, top_k),
        int(cfg.get("search", {}).get("max_top_k", 100)),
    )

    def diversify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cap = int(cfg.get("search", {}).get("max_chunks_per_document", 3))
        counts: Counter[str] = Counter()
        selected = []
        for row in rows:
            if counts[row["doc_id"]] >= cap:
                continue
            counts[row["doc_id"]] += 1
            selected.append(row)
            if len(selected) >= top_k:
                break
        for rank, row in enumerate(selected, 1):
            row["fusion_rank"] = rank
        return selected

    if retrieval == "keyword":
        return diversify(query_index(cfg, question, candidate_count))
    from .vector import query_vector

    if retrieval == "vector":
        return diversify(query_vector(cfg, question, candidate_count))
    if retrieval == "hybrid":
        candidates = max(
            top_k, int(cfg.get("search", {}).get("vector_candidates", 60))
        )
        keyword = query_index(cfg, question, candidates)
        vector = query_vector(cfg, question, candidates, candidate_k=candidates)
        fused = rrf_fuse(
            keyword,
            vector,
            top_k=candidates,
            rrf_k=int(cfg.get("search", {}).get("rrf_k", 60)),
            keyword_weight=float(
                cfg.get("search", {}).get("rrf_keyword_weight", 1.5)
            ),
            vector_weight=float(
                cfg.get("search", {}).get("rrf_vector_weight", 1.0)
            ),
        )
        return diversify(fused)
    if retrieval == "bge-vector":
        from .gpu_retrieval import query_bge_vector

        return diversify(query_bge_vector(cfg, question, candidate_count))
    if retrieval == "reranked-hybrid":
        from .gpu_retrieval import query_reranked_hybrid

        return query_reranked_hybrid(cfg, question, top_k)
    if retrieval == "qwen-vector":
        from .qwen_retrieval import query_qwen_vector

        return diversify(query_qwen_vector(cfg, question, candidate_count))
    if retrieval == "qwen-reranked-hybrid":
        from .qwen_retrieval import query_qwen_reranked_hybrid

        return query_qwen_reranked_hybrid(cfg, question, top_k)
    raise ValueError(f"未知检索模式: {retrieval}")


def source_page(
    cfg: dict[str, Any], doc_id: str, page: int
) -> dict[str, Any] | None:
    with _connect(cfg["paths"]["database"], readonly=True) as conn:
        row = conn.execute(
            """
            SELECT p.*, d.title, d.year, d.doi, d.source_filename, d.sha256
            FROM pages p JOIN documents d ON d.doc_id=p.doc_id
            WHERE p.doc_id=? AND p.pdf_page=?
            """,
            (doc_id, page),
        ).fetchone()
    return dict(row) if row else None


def doctor(cfg: dict[str, Any], logger, *, deep: bool = False) -> dict[str, Any]:
    paths = cfg["paths"]
    checks: dict[str, Any] = {}
    pdfs = list_pdfs(paths["modern_pdf_dir"])
    checks["pdf_count"] = len(pdfs)
    docs_json = read_jsonl(paths["documents_jsonl"])
    pages_json = read_jsonl(paths["pages_jsonl"])
    chunks_json = read_jsonl(paths["chunks_jsonl"])
    checks["json_counts"] = {
        "documents": len(docs_json), "pages": len(pages_json), "chunks": len(chunks_json)
    }
    checks["duplicate_doc_ids"] = [
        k for k, n in Counter(d["doc_id"] for d in docs_json).items() if n > 1
    ]
    doi_groups: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for d in docs_json:
        if d.get("doi"):
            doi_groups[d["doi"]].add((d["sha256"], d.get("title") or ""))
    checks["doi_conflicts"] = {
        doi: sorted(group) for doi, group in doi_groups.items() if len(group) > 1
    }
    checks["invalid_or_incomplete_dois"] = [
        {"doc_id": d["doc_id"], "doi": d.get("doi")}
        for d in docs_json if d.get("doi") and not is_valid_doi(d["doi"])
    ]

    before = {
        r["source_filename"]: r["sha256"]
        for r in read_jsonl(paths["source_checksums_before"])
    }
    current = {p.name: sha256_file(str(p)) for p in pdfs}
    checks["sha256_changed"] = sorted(
        name for name in set(before) & set(current) if before[name] != current[name]
    )
    checks["sha256_missing"] = sorted(set(before) - set(current))
    checks["sha256_added"] = sorted(set(current) - set(before))

    with _connect(paths["database"], readonly=True) as conn:
        checks["database_counts"] = database_counts(conn)
        checks["sqlite_quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        checks["orphan_chunks"] = conn.execute(
            """SELECT count(*) FROM chunks c
               LEFT JOIN documents d ON d.doc_id=c.doc_id
               WHERE d.doc_id IS NULL"""
        ).fetchone()[0]
        checks["chunks_missing_page"] = conn.execute(
            """SELECT count(*) FROM chunks c
               LEFT JOIN pages p ON p.doc_id=c.doc_id AND p.pdf_page=c.pdf_page
               WHERE p.doc_id IS NULL"""
        ).fetchone()[0]
        checks["missing_page_numbers"] = conn.execute(
            "SELECT count(*) FROM pages WHERE pdf_page IS NULL OR pdf_page < 1"
        ).fetchone()[0]
        chunk_ids = {
            row[0] for row in conn.execute("SELECT chunk_id FROM chunks")
        }
        fts_ids = {
            row[0] for row in conn.execute("SELECT chunk_id FROM chunks_fts")
        }
        checks["fts_missing_chunks"] = len(chunk_ids - fts_ids)
        checks["fts_orphan_rows"] = len(fts_ids - chunk_ids)

    expected = checks["json_counts"]
    actual = checks["database_counts"]
    base_healthy = all(
        [
            checks["pdf_count"] == 584,
            expected == {k: actual[k] for k in ("documents", "pages", "chunks")},
            actual["chunks"] == actual["fts_rows"],
            not checks["duplicate_doc_ids"],
            not checks["doi_conflicts"],
            not checks["invalid_or_incomplete_dois"],
            not checks["sha256_changed"],
            not checks["sha256_missing"],
            not checks["sha256_added"],
            checks["sqlite_quick_check"] == "ok",
            checks["orphan_chunks"] == 0,
            checks["chunks_missing_page"] == 0,
            checks["missing_page_numbers"] == 0,
            checks["fts_missing_chunks"] == 0,
            checks["fts_orphan_rows"] == 0,
        ]
    )
    if deep:
        from .vector import vector_doctor

        checks["vector"] = vector_doctor(cfg)
        qwen_keys = {
            "qwen_vector_dir",
            "qwen_faiss_index",
            "qwen_vector_manifest",
            "qwen_vector_map",
            "qwen_embedding_model_dir",
            "qwen_reranker_model_dir",
        }
        if qwen_keys <= set(paths):
            from .qwen_retrieval import qwen_doctor

            checks["qwen_vector"] = qwen_doctor(cfg)
        freeze_path = (
            Path(paths["data_dir"]) / "freeze" / "modern_corpus_v1_manifest.json"
        )
        if freeze_path.is_file():
            frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
            frozen_db = frozen.get("artifacts", {}).get("data/rag.db", {}).get("sha256")
            checks["frozen_rag_db_sha256_matches"] = (
                bool(frozen_db) and frozen_db == sha256_file(paths["database"])
            )
        else:
            checks["frozen_rag_db_sha256_matches"] = False
    checks["healthy"] = base_healthy and (
        not deep
        or (
            checks.get("vector", {}).get("healthy") is True
            and (
                "qwen_vector" not in checks
                or checks["qwen_vector"].get("healthy") is True
            )
            and checks.get("frozen_rag_db_sha256_matches") is True
        )
    )
    logger.info("doctor: %s", json.dumps(checks, ensure_ascii=False))
    return checks
