# Local RAG application

`app/` provides terminal retrieval over two independent corpora. Modern
literature uses document/page/chunk records; ancient literature uses book/page
records. They share result formatting and source lookup but never share a
database or vector index.

## Accepted datasets

- Modern: 584 PDF documents, 9,870 pages, 10,983 same-page chunks.
- Ancient: 22 books, 26,949 pages.
- Immutable IDs: `doc_id`, `chunk_id`, `book_id`, and `page_id`.
- Source integrity: PDF/database/JSONL/index/layout/model SHA-256 fingerprints.

Private data and model paths belong in an untracked configuration derived from
`config.yaml`. Do not commit databases, PDFs, model weights, indexes, logs, or
generated reports.

## Commands

Run from the repository root with `app/src` on `PYTHONPATH`:

```bash
export PYTHONPATH="$PWD/app/src:$PWD"

python app/rag_cli.py --config app/config.yaml status
python app/rag_cli.py --config app/config.yaml doctor --deep

python app/rag_cli.py --config app/config.yaml query \
  --mode modern --retrieval keyword "绿原酸促进创面修复"
python app/rag_cli.py --config app/config.yaml query \
  --mode modern --retrieval qwen-vector "绿原酸促进创面修复"
python app/rag_cli.py --config app/config.yaml query \
  --mode modern --retrieval qwen-reranked-hybrid "绿原酸促进创面修复"

python app/rag_cli.py --config app/config.yaml query \
  --mode ancient --retrieval qwen-reranked-hybrid "忍冬汤 金银花 甘草"
python app/rag_cli.py --config app/config.yaml query \
  --mode dual --retrieval qwen-reranked-hybrid "金银花 烧伤 创面修复"

python app/rag_cli.py --config app/config.yaml source \
  --mode auto --doc-id DOC_ID --page 20
```

Available modern channels include `keyword`, `vector`, `hybrid`, `bge-vector`,
`reranked-hybrid`, `qwen-vector`, and `qwen-reranked-hybrid`. Ancient production
uses keyword, Qwen vector, or Qwen reranked hybrid. `dual` searches each corpus
independently and merges result ranks while preserving corpus identity.

Every result can include title, year, DOI, physical page, source filename,
snippet, stable IDs, keyword/vector/reranker scores, and fusion rank. `source`
reopens the exact page used by retrieval.

## Indexing

Modern FAISS records reuse existing `chunk_id` values. Ancient FAISS records
reuse `page_id` values. Index builders are resumable and do not rechunk data or
overwrite SQLite.

```bash
python app/rag_cli.py --config app/config.yaml embed --resume
python app/rag_cli.py --config app/config.yaml embed-qwen --resume
python app/rag_cli.py --config app/config.yaml embed-ancient-qwen --resume
```

Qwen production indexes use `Qwen/Qwen3-Embedding-8B`, normalized 4,096-
dimensional vectors, and FAISS inner-product search. Reranking uses
`Qwen/Qwen3-Reranker-8B`. Each index manifest binds model, database, canonical
corpus text, and layout-sidecar fingerprints so stale indexes cannot pass deep
doctor checks.

## Retrieval evaluation

```bash
python app/scripts/retrieval_eval_v2.py
python app/scripts/evaluate_ancient_retrieval.py --config app/config.yaml
```

The independent 52-question ancient set fixes expected book IDs and physical
pages before retrieval and validates expected evidence terms directly on the
label page. Final reranked hybrid performance is Recall@5 `0.9130`, Recall@10
`0.9565`, MRR@10 `0.8200`, page locatability `1.0`, and no-answer accuracy
`1.0`.

The 240-question source-locator set evaluates deterministic title anchoring,
simplified/traditional term normalization, same-name formula handling, and
explicit out-of-era abstention. Its Recall@10 is `0.9955`; this planner-assisted
metric is reported separately from raw vector retrieval.

## Ancient layout and versioning

Ancient OCR segments retain geometry. `reorder_ancient_pages.py` writes a
read-only sidecar that orders horizontal pages left-to-right and vertical pages
right-to-left. Retrieval and index fingerprints use the ordered text without
modifying source PDFs or the base SQLite database.

`promote_vl_candidates.py` creates a separate versioned database with SQLite
backup, verifies source/page/original/candidate hashes, synchronizes
`pages.text`, `payload_json.text`, and FTS, writes same-version `pages.jsonl`,
and proves the source database hash did not change. `kanripo_auto_ingest.py`
applies the current `0.7` automatic text-quality policy to curated transcriptions.

## Verification

```bash
python -m pytest -q --basetemp .pytest_tmp
python ancient_ocr/release_preflight.py --repository .
git diff --check
```

`doctor --deep` must report matching database, JSONL, corpus-text, model, and
layout fingerprints with zero missing or orphaned index entries.
