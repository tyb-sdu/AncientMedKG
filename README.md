# AncientMedKG

AncientMedKG is a provenance-first, local command-line RAG project for burn and wound-healing evidence. It keeps modern biomedical literature and Chinese medical classics in separate corpora, then supports keyword, vector, and hybrid retrieval with page-level citations.

## Included code

- `app/`: modern-literature processing, FTS5, FAISS, Qwen embedding and reranking, dual-corpus retrieval, command-line interface, and tests.
- `ancient_ocr/`: page-level OCR, ancient-book SQLite/FTS preparation, quality checks, and tests.
- `knowledge_graph/`: provenance-first five-layer graph schema, immutable builds,
  source verification, release gates, and Neo4j/JSON-LD export.
- `research_pipeline/`: burn ontology, Rendongtang same-name formula
  disambiguation, controlled-vocabulary query planning, and evidence-bundle
  construction.
- `discovery_pipeline/`: PubChem identity resolution, modern-corpus candidate
  scanning, blinded dual review, independent adjudication, reviewed KG handoff,
  C0-C5 compound scoring, and evidence-tiered mechanism analysis.

## Deliberately excluded

This public repository does not include PDFs, scanned ancient books, OCR text, SQLite databases, FAISS indexes, model weights, caches, logs, or Python environments. Those artifacts may be copyrighted, large, or specific to a local research environment.

## Design principles

- Preserve original PDF page provenance.
- Reuse stable `chunk_id` or ancient-book `page_id` in every sidecar index.
- Keep modern literature and ancient books in independent databases.
- Do not call paid APIs, start a web service, or generate medical conclusions.

## Quick start

Install the dependencies in `app/requirements.txt`, prepare your own licensed or openly accessible corpus, update `app/config.yaml` for local paths, then run `python rag_cli.py --help` from `app/`.

See `app/README.md`, `app/HANDOFF.md`, `app/RUN_REPORT.md`,
`knowledge_graph/README.md`, `research_pipeline/README.md`, and
`discovery_pipeline/README.md` for the implemented pipelines and verification
record.

## Research and graph commands

These commands operate locally and do not start a server:

```bash
python -m knowledge_graph doctor --help
python -m research_pipeline.validate_assets
python -m discovery_pipeline --help
python -m discovery_pipeline doctor --help
```

Graph draft export requires an explicit `--allow-unreleased` flag. A research
draft with pending evidence cannot pass the knowledge-graph release gate.
Likewise, a valid discovery intake report proves data integrity but deliberately
keeps `scientific_release_ready=false` until identity, full-text, C0-C5,
mechanism, and experimental reviews are complete.

## Candidate manifest validation

PaddleOCR-VL outputs are not accepted merely because they pass structural validation. Validate a locally generated manifest before review or an explicitly authorized versioned promotion:

```bash
python ancient_ocr/verify_candidate_manifest.py /path/to/candidate_manifest.csv \
  --output /path/to/candidate_manifest_integrity.json
python -m pytest ancient_ocr app/tests -q
```

The verifier checks required fields, SHA-256 values, book and source identity, physical-page keys, expected candidate/image paths, and empty-candidate flags. Candidate JSON, rendered page images, OCR text, and the generated validation report are local artifacts and are not included in the public repository.

## Release preflight

Before publishing from the actual Git repository, verify that Git does not track private or generated artifacts:

```bash
python ancient_ocr/release_preflight.py --repository .
python -m pytest -q
git diff --check
```

See `RELEASE_ACCEPTANCE.md` for the vNext promotion decision, evidence, known limitations, and final release gate.

## Promote a complete VLM batch

Create a versioned database without overwriting the accepted source database:

```bash
python ancient_ocr/promote_vl_candidates.py \
  --manifest /path/to/candidate_manifest.csv \
  --candidate-root /path/to/candidate_output \
  --database ancient_ocr/data/ancient_rag.db \
  --output-database ancient_ocr/data/versions/vl_vnext/ancient_rag.db \
  --output-pages-jsonl ancient_ocr/data/versions/vl_vnext/pages.jsonl
```

Every manifest row receives a promotion audit record. Non-empty candidates become the vNext page text; an empty candidate keeps the original OCR text and is recorded as `original_fallback_empty_candidate`. The new database rebuilds affected FTS rows, keeps `payload_json.text` synchronized, exports a matching `pages.jsonl`, and passes `PRAGMA quick_check` plus the expected 5,624-page count before it replaces its temporary outputs. Point the vNext runtime's `ancient_pages_jsonl` setting at this exported file so the independent vector-index fingerprint belongs to the promoted text.

The 2026-07-31 OCR release promoted 113 audited pages into an independent vNext
database (105 candidate texts and 8 original-text fallbacks). The complete
vNext gate passed; deep doctor verified all database, JSONL, corpus, and layout
fingerprints. Subsequent KG and research milestones are recorded in
`PROJECT_STATUS.md` and `RELEASE_ACCEPTANCE.md`.
