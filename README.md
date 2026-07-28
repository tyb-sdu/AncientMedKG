# AncientMedKG

AncientMedKG is a provenance-first, local command-line RAG project for burn and wound-healing evidence. It keeps modern biomedical literature and Chinese medical classics in separate corpora, then supports keyword, vector, and hybrid retrieval with page-level citations.

## Included code

- `app/`: modern-literature processing, FTS5, FAISS, Qwen embedding and reranking, dual-corpus retrieval, command-line interface, and tests.
- `ancient_ocr/`: page-level OCR, ancient-book SQLite/FTS preparation, quality checks, and tests.

## Deliberately excluded

This public repository does not include PDFs, scanned ancient books, OCR text, SQLite databases, FAISS indexes, model weights, caches, logs, or Python environments. Those artifacts may be copyrighted, large, or specific to a local research environment.

## Design principles

- Preserve original PDF page provenance.
- Reuse stable `chunk_id` or ancient-book `page_id` in every sidecar index.
- Keep modern literature and ancient books in independent databases.
- Do not call paid APIs, start a web service, or generate medical conclusions.

## Quick start

Install the dependencies in `app/requirements.txt`, prepare your own licensed or openly accessible corpus, update `app/config.yaml` for local paths, then run `python rag_cli.py --help` from `app/`.

See `app/README.md`, `app/HANDOFF.md`, and `app/RUN_REPORT.md` for the implemented pipeline and verification record.
