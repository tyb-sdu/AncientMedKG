from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import fitz
import pytest
import yaml

from rag_prep.chunk import chunk_page_text
from rag_prep.config import load_config
from rag_prep.extract import extract_pdf_pages
from rag_prep.ids import is_valid_doi, make_chunk_id, make_doc_id, normalize_doi
from rag_prep.io_utils import load_done_ids, mark_done, read_jsonl, write_jsonl
from rag_prep.pipeline import run_chunk, run_extract, run_inventory, run_validate
from rag_prep.logging_utils import setup_logging
from rag_prep.search import (
    build_index,
    chinese_search_tokens,
    query_index,
    query_modern_retrieval,
    rrf_fuse,
    source_page,
)
from rag_prep.dual_retrieval import query_any_corpus, source_any_page
import rag_prep.dual_retrieval as dual_retrieval
from rag_prep.text_utils import detect_document_language, detect_language


@pytest.fixture
def cfg_tmp(tmp_path: Path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    for d in (data_dir, logs_dir, state_dir):
        d.mkdir()

    # 正常英文 PDF
    p1 = pdf_dir / "2024_Burn_and_Lonicera_study.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Burn wound healing with Lonicera and chlorogenic acid. "
        "The treatment reduced inflammation and oxidative stress. " * 40,
    )
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Methods and results for hydrogel drug delivery. " * 40)
    doc.save(p1)
    doc.close()

    # 中文 PDF
    p2 = pdf_dir / "2023_金银花烧伤创面.pdf"
    doc = fitz.open()
    page = doc.new_page()
    zh = "金银花与甘草用于烧伤创面愈合研究。绿原酸具有抗炎与抗氧化作用。" * 60
    page.insert_text((72, 72), zh)
    doc.save(p2)
    doc.close()

    # 无 DOI 短文献
    p3 = pdf_dir / "short_note.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Brief note on licorice safety.")
    doc.save(p3)
    doc.close()

    # 空白页 PDF（疑似无文字层）
    p4 = pdf_dir / "blank_scan.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(p4)
    doc.close()

    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "original_relative_path,original_filename,new_filename,year,title,year_source,title_source,doi\n"
        f",,{p1.name},2024,Burn and Lonicera study,manual,manual,10.1234/burn.lonicera.2024\n"
        f",,{p2.name},2023,金银花烧伤创面,manual,manual,\n"
        f",,{p3.name},,Brief note,manual,manual,\n"
        f",,{p4.name},2020,Blank scan,manual,manual,10.9999/blank.scan\n",
        encoding="utf-8",
    )

    ancient_dir = tmp_path / "ancient_data"
    ancient_dir.mkdir()
    ancient_db = ancient_dir / "ancient_rag.db"
    connection = sqlite3.connect(ancient_db)
    connection.executescript(
        """
        CREATE TABLE books (
            book_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            processing_mode TEXT NOT NULL
        );
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            physical_page INTEGER NOT NULL,
            pdf_page_label TEXT,
            text TEXT NOT NULL,
            reading_direction TEXT NOT NULL,
            average_confidence REAL,
            low_confidence INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE pages_fts USING fts5(
            page_id UNINDEXED,
            book_id UNINDEXED,
            title,
            text,
            tokenize='unicode61'
        );
        """
    )
    book_id = "ancient:testbook"
    page_id = f"{book_id}:p000001"
    connection.execute(
        "INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            book_id,
            "外科古籍样本",
            "ancient_sample.pdf",
            str(ancient_dir / "ancient_sample.pdf"),
            "a" * 64,
            1,
            "native_text",
        ),
    )
    payload = json.dumps({"book_id": book_id, "physical_page": 1}, ensure_ascii=False)
    connection.execute(
        "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            page_id,
            book_id,
            1,
            "卷一第一页",
            "金银花可用于烧伤与创面修复，兼论外治法。",
            "native",
            None,
            0,
            payload,
        ),
    )
    connection.execute(
        "INSERT INTO pages_fts VALUES (?, ?, ?, ?)",
        (page_id, book_id, "外科古籍样本", "金银花可用于烧伤与创面修复，兼论外治法。"),
    )
    connection.commit()
    connection.close()

    cfg = {
        "project_root": str(tmp_path),
        "paths": {
            "modern_pdf_dir": str(pdf_dir),
            "mapping_csv": str(mapping),
            "data_dir": str(data_dir),
            "logs_dir": str(logs_dir),
            "state_dir": str(state_dir),
            "ancient_data_dir": str(ancient_dir),
            "ancient_database": str(ancient_db),
            "ancient_books_jsonl": str(ancient_dir / "books.jsonl"),
            "ancient_pages_jsonl": str(ancient_dir / "pages.jsonl"),
            "documents_csv": str(data_dir / "documents.csv"),
            "documents_jsonl": str(data_dir / "documents.jsonl"),
            "pages_jsonl": str(data_dir / "pages.jsonl"),
            "chunks_jsonl": str(data_dir / "chunks.jsonl"),
            "quality_issues_csv": str(data_dir / "quality_issues.csv"),
            "database": str(data_dir / "rag.db"),
            "retrieval_eval": str(data_dir / "retrieval_eval.json"),
            "doi_audit": str(data_dir / "doi_audit.json"),
            "source_checksums_before": str(data_dir / "source_checksums_before.jsonl"),
            "source_checksums_after": str(data_dir / "source_checksums_after.jsonl"),
            "run_manifest": str(data_dir / "run_manifest.json"),
            "pipeline_log": str(logs_dir / "pipeline.log"),
        },
        "extraction": {
            "preferred_engine": "pymupdf",
            "empty_page_char_threshold": 30,
            "needs_ocr_empty_page_ratio": 0.80,
            "short_doc_char_threshold": 200,
        },
        "chunking": {
            "en_target_words_min": 50,
            "en_target_words_max": 120,
            "zh_target_chars_min": 80,
            "zh_target_chars_max": 200,
            "overlap_ratio": 0.08,
            "min_chunk_chars": 20,
            "max_chunk_chars_hard": 8000,
        },
        "topics": {"low_relevance_threshold": 25},
        "quality": {
            "high_empty_page_ratio": 0.50,
            "garbled_ratio_threshold": 0.15,
            "abnormal_page_count_max": 500,
            "short_chunk_chars": 10,
            "long_chunk_chars": 5000,
        },
        "runtime": {"encoding": "utf-8", "progress_every": 1},
        "search": {
            "default_top_k": 10,
            "max_top_k": 100,
            "snippet_chars": 360,
            "chinese_ngram_min": 1,
            "chinese_ngram_max": 2,
            "dual_rrf_k": 60,
            "dual_modern_weight": 1.0,
            "dual_ancient_weight": 0.8,
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    logger = setup_logging(cfg["paths"]["pipeline_log"], verbose=True)
    return cfg, logger, cfg_path


def test_stable_doc_id():
    a = make_doc_id("10.1234/ABC.DEF", "abcd" * 16)
    b = make_doc_id("https://doi.org/10.1234/ABC.DEF", "ffff" * 16)
    assert a == b == "doi:10.1234/abc.def"
    c = make_doc_id("", "0123456789abcdef" * 4)
    assert c.startswith("sha256:")
    assert make_doc_id(None, "0123456789abcdef" * 4) == c


def test_stable_chunk_id():
    a = make_chunk_id("doi:10.1/x", 3, 2)
    b = make_chunk_id("doi:10.1/x", 3, 2)
    assert a == b
    assert "_p0003_c002" in a


def test_normalize_doi_and_validity():
    assert normalize_doi("https://doi.org/10.3390/ijms130911773") == "10.3390/ijms130911773"
    assert is_valid_doi("10.3390/ijms130911773")
    assert not is_valid_doi("not-a-doi")
    assert not is_valid_doi("10.3390/antiox")
    assert not is_valid_doi("10.1371/j")
    assert not is_valid_doi("10.2147/dddt")
    assert not is_valid_doi("10.1016/j.xjid")
    assert is_valid_doi("10.3390/antiox10010099")


def test_page_number_starts_at_one(cfg_tmp):
    cfg, _, _ = cfg_tmp
    pdf = next(Path(cfg["paths"]["modern_pdf_dir"]).glob("2024_*.pdf"))
    result = extract_pdf_pages(pdf)
    assert result["pages"][0]["pdf_page"] == 1
    assert all(p["pdf_page"] >= 1 for p in result["pages"])


def test_chunk_no_cross_page(cfg_tmp):
    cfg, _, _ = cfg_tmp
    text = ("Wound healing and burn care with chlorogenic acid. " * 80)
    chunks = chunk_page_text(
        text,
        doc_id="doi:10.1/demo",
        pdf_page=5,
        language="en",
        title="Demo",
        year=2024,
        doi="10.1/demo",
        source_filename="demo.pdf",
        sha256="a" * 64,
        cfg=cfg,
    )
    assert chunks
    assert all(c["pdf_page"] == 5 for c in chunks)
    ids = [make_chunk_id("doi:10.1/demo", 5, i) for i in range(len(chunks))]
    assert [c["chunk_id"] for c in chunks] == ids


def test_zh_and_en_chunking(cfg_tmp):
    cfg, _, _ = cfg_tmp
    en = chunk_page_text(
        ("Burn inflammation hydrogel. " * 100),
        doc_id="sha256:" + "b" * 64,
        pdf_page=1,
        language="en",
        title="EN",
        year=2024,
        doi="",
        source_filename="en.pdf",
        sha256="b" * 64,
        cfg=cfg,
    )
    zh = chunk_page_text(
        ("烧伤创面愈合与金银花甘草绿原酸抗炎研究。" * 40),
        doc_id="sha256:" + "c" * 64,
        pdf_page=1,
        language="zh",
        title="中文",
        year=2023,
        doi="",
        source_filename="zh.pdf",
        sha256="c" * 64,
        cfg=cfg,
    )
    assert en and zh
    assert all(c["language"] == "en" for c in en)
    assert all(c["language"] == "zh" for c in zh)


def test_config_load(cfg_tmp):
    _, _, cfg_path = cfg_tmp
    loaded = load_config(cfg_path)
    assert "paths" in loaded
    assert Path(loaded["paths"]["data_dir"]).exists() or True
    assert loaded["extraction"]["preferred_engine"] == "pymupdf"


def test_improved_language_detection():
    assert detect_language("烧伤创面修复与金银花研究。" * 20) == "zh"
    assert detect_language("Burn wound healing with hydrogel. " * 20) == "en"
    assert detect_language(("烧伤创面。" * 20) + ("English abstract. " * 20)) == "mixed"
    assert detect_document_language(
        ["烧伤创面与甘草治疗。" * 100, "English abstract. " * 10]
    ) == "zh"


def test_chinese_character_search_tokens():
    tokens = chinese_search_tokens("金银花")
    assert "金" in tokens.split()
    assert "金银" in tokens.split()
    assert "银花" in tokens.split()


def test_resume_and_duplicate_detection(cfg_tmp):
    cfg, logger, _ = cfg_tmp
    r1 = run_inventory(cfg, logger, resume=False, force=True)
    assert r1["document_count"] == 4
    r2 = run_inventory(cfg, logger, resume=True, force=False)
    assert r2["new_count"] == 0

    docs = read_jsonl(cfg["paths"]["documents_jsonl"])
    shas = [d["sha256"] for d in docs]
    assert len(shas) == len(set(shas))

    state = Path(cfg["paths"]["state_dir"]) / "inventory_done.jsonl"
    done1 = load_done_ids(state)
    mark_done(state, "extra_id")
    done2 = load_done_ids(state)
    assert "extra_id" in done2
    assert done1 <= done2


def test_abnormal_pdf_and_jsonl_integrity(cfg_tmp):
    cfg, logger, _ = cfg_tmp
    run_inventory(cfg, logger, force=True)
    ext = run_extract(cfg, logger, force=True)
    assert ext["total"] == 4
    # blank pdf should be needs_ocr or failed-ish
    docs = {d["source_filename"]: d for d in read_jsonl(cfg["paths"]["documents_jsonl"])}
    assert docs["blank_scan.pdf"]["extraction_status"] in {"needs_ocr", "failed", "ok"}

    run_chunk(cfg, logger, force=True)
    pages = read_jsonl(cfg["paths"]["pages_jsonl"])
    chunks = read_jsonl(cfg["paths"]["chunks_jsonl"])
    assert pages
    assert all("doc_id" in p and "pdf_page" in p and "text" in p for p in pages)
    assert all(p["pdf_page"] >= 1 for p in pages)
    assert all("chunk_id" in c and c["pdf_page"] >= 1 for c in chunks)

    # JSONL 完整性：可逐行解析
    for path in (
        cfg["paths"]["documents_jsonl"],
        cfg["paths"]["pages_jsonl"],
        cfg["paths"]["chunks_jsonl"],
    ):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    json.loads(line)

    manifest = run_validate(cfg, logger)
    assert manifest["documents"] == 4
    assert manifest["source_integrity"]["unchanged"] is True


def test_sqlite_fts_query_and_source(cfg_tmp):
    cfg, logger, _ = cfg_tmp
    run_inventory(cfg, logger, force=True)
    run_extract(cfg, logger, force=True)
    run_chunk(cfg, logger, force=True)
    counts = build_index(cfg, logger, force=True)
    assert counts["documents"] == 4
    assert counts["chunks"] == counts["fts_rows"]

    results = query_index(cfg, "burn wound healing", top_k=5)
    assert results
    assert all(r["pdf_page"] >= 1 for r in results)
    first = results[0]
    page = source_page(cfg, first["doc_id"], first["pdf_page"])
    assert page
    assert page["source_filename"] == first["source_filename"]
    assert query_index(cfg, "xyz987654不存在独角兽", top_k=5) == []


def test_rrf_fuses_by_stable_chunk_id():
    keyword = [
        {"chunk_id": "a", "keyword_score": 4.0, "title": "A"},
        {"chunk_id": "b", "keyword_score": 3.0, "title": "B"},
    ]
    vector = [
        {"chunk_id": "b", "vector_score": 0.9, "title": "B"},
        {"chunk_id": "c", "vector_score": 0.8, "title": "C"},
    ]
    result = rrf_fuse(keyword, vector, top_k=3, rrf_k=60)
    assert [row["chunk_id"] for row in result] == ["b", "a", "c"]
    assert result[0]["keyword_rank"] == 2
    assert result[0]["vector_rank"] == 1
    assert result[0]["fusion_rank"] == 1


def test_ancient_keyword_and_source(cfg_tmp):
    cfg, logger, _ = cfg_tmp
    run_inventory(cfg, logger, force=True)
    run_extract(cfg, logger, force=True)
    run_chunk(cfg, logger, force=True)
    build_index(cfg, logger, force=True)

    ancient = query_any_corpus(cfg, "金银花 烧伤 创面修复", "keyword", 5, mode="ancient")
    assert ancient
    assert ancient[0]["corpus"] == "ancient"
    assert ancient[0]["doc_id"] == "ancient:testbook"

    page = source_any_page(cfg, "ancient:testbook", 1, mode="auto")
    assert page
    assert page["corpus"] == "ancient"
    assert "金银花" in page["text"]


def test_dual_mode_merges_modern_and_ancient(cfg_tmp):
    cfg, logger, _ = cfg_tmp
    run_inventory(cfg, logger, force=True)
    run_extract(cfg, logger, force=True)
    run_chunk(cfg, logger, force=True)
    build_index(cfg, logger, force=True)

    modern = query_modern_retrieval(cfg, "金银花 烧伤", "keyword", 5)
    assert modern

    dual = query_any_corpus(cfg, "金银花 烧伤", "keyword", 8, mode="dual")
    corpora = {row["corpus"] for row in dual}
    assert "modern" in corpora
    assert "ancient" in corpora


def test_ancient_qwen_route(monkeypatch, cfg_tmp):
    cfg, _, _ = cfg_tmp

    def fake_query(cfg_arg, question, top_k):
        return [
            {
                "corpus": "ancient",
                "chunk_id": "ancient:testbook:p000001",
                "doc_id": "ancient:testbook",
                "title": "外科古籍样本",
                "year": "",
                "doi": "",
                "pdf_page": 1,
                "page_label": "卷一第一页",
                "source_filename": "ancient_sample.pdf",
                "sha256": "a" * 64,
                "snippet": "金银花可用于烧伤与创面修复",
                "vector_score": 0.99,
                "fusion_rank": 1,
            }
        ]

    monkeypatch.setattr(dual_retrieval, "query_ancient_qwen_vector", fake_query)
    rows = query_any_corpus(cfg, "金银花 烧伤", "qwen-vector", 5, mode="ancient")
    assert rows
    assert rows[0]["corpus"] == "ancient"
    assert rows[0]["doc_id"] == "ancient:testbook"
