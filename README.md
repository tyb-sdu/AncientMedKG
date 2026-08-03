# AncientMedKG

Evidence-first local RAG and knowledge-graph tooling for burn, wound healing,
Rendongtang, honeysuckle, licorice, and their modern biomedical evidence.
The project runs from the terminal, does not start a web server, and does not
require a paid API.

## Current release

The 2026-08-03 release combines two independently versioned corpora:

| Layer | Accepted scale |
| --- | ---: |
| Modern literature | 584 documents, 9,870 pages, 10,983 chunks |
| Ancient literature | 22 books, 26,949 pages |
| Ancient KG | 613 entities, 1,744 evidence records, 3,200 assertions |
| Modern KG | 194 entities, 606 evidence records, 1,488 assertions |
| Combined KG | 807 entities, 2,350 evidence records, 4,688 assertions |
| Mechanism candidates | 97 compound-target-pathway-phenotype chains |

All accepted automatic records satisfy the configured confidence threshold of
`0.7`. Records below the threshold are discarded. Machine approval is recorded
with `human_reviewed=false`; it proves that the engineering and provenance gates
passed, not that efficacy, direct binding, safety, or clinical benefit has been
experimentally established. The automatic graph emits no `TREATS` assertions.

The final acceptance summary is in
[`FINAL_SIX_DELIVERABLES.md`](FINAL_SIX_DELIVERABLES.md) and the machine-readable
public report is
[`research_pipeline/reports/final_six_acceptance_v1.json`](research_pipeline/reports/final_six_acceptance_v1.json).

## Architecture

```text
PDF / curated ancient text
        |
        v
immutable document, page, and chunk IDs + SHA-256
        |
        +--> SQLite FTS5 --------------------+
        |                                    |
        +--> Qwen3-Embedding-8B / FAISS -----+--> RRF --> Qwen3-Reranker-8B
        |                                    |              |
        +--> source/page verification -------+              v
        |                                              traceable results
        v
candidate evidence --> confidence >= 0.7 --> source verification
        |                                      |
        v                                      v
ancient + modern evidence bundles --> immutable KG JSONL
        |
        +--> schema/release doctor
        +--> Neo4j CSV + constraints
        +--> JSON-LD + SHA-256 manifest
```

Stable identifiers are assigned before retrieval or graph construction. Modern
evidence retains `doc_id`, `chunk_id`, physical PDF page, DOI, source-file hash,
and exact database text. Ancient evidence retains `book_id`, `page_id`, physical
page, source hash, text hash, and exact quote. Graph validation reopens the
SQLite databases read-only and resolves every released evidence record back to
its source row.

## Repository layout

| Directory | Responsibility |
| --- | --- |
| `app/` | Modern and ancient terminal RAG, FTS5, FAISS, RRF, reranking, source lookup, deep doctor |
| `ancient_ocr/` | OCR ingestion, layout ordering, immutable promotion, corpus integrity, release preflight |
| `knowledge_graph/` | Typed graph model, stable IDs, validation, source verification, Neo4j and JSON-LD export |
| `research_pipeline/` | Ancient evidence extraction, formula disambiguation, retrieval evaluation, combined release |
| `discovery_pipeline/` | Compound identity, corpus scanning, automatic evidence thresholding, scoring and mechanisms |

PDFs, OCR text, databases, model weights, FAISS indexes, raw snippets, logs, and
credentials are deliberately excluded from Git. They remain in the private
runtime and are checked by SHA-256 manifests.

## Local setup

Python 3.10 or newer is recommended. Install the RAG dependencies from the
repository root:

```bash
python -m venv .venv
.venv/bin/pip install -r app/requirements.txt
export PYTHONPATH="$PWD/app/src:$PWD"
```

On Windows PowerShell, use `.venv\Scripts\python.exe` and set
`$env:PYTHONPATH="$PWD\app\src;$PWD"`.

Real runs require an untracked configuration whose paths point to the private
SQLite databases, JSONL files, model directories, and indexes. The public
[`app/config.yaml`](app/config.yaml) documents the complete schema.

## Retrieval

```bash
python app/rag_cli.py --config app/config.yaml doctor --deep
python app/rag_cli.py --config app/config.yaml query \
  --mode ancient --retrieval qwen-reranked-hybrid "忍冬汤 金银花 甘草"
python app/rag_cli.py --config app/config.yaml query \
  --mode modern --retrieval qwen-reranked-hybrid "绿原酸促进创面修复"
python app/rag_cli.py --config app/config.yaml source \
  --mode auto --doc-id DOC_ID --page 20
```

Keyword retrieval uses SQLite FTS5. Vector retrieval uses normalized
Qwen3-Embedding-8B vectors in FAISS `IndexFlatIP`. Hybrid retrieval combines
lexical and vector ranks with reciprocal-rank fusion and applies
Qwen3-Reranker-8B to the candidate pool. Returned rows include title, year, DOI,
physical page, snippet, filename, stable IDs, component scores, and final rank.

On the fixed 52-question ancient benchmark, final reranked hybrid retrieval
reaches Recall@10 `0.9565`, MRR@10 `0.8200`, page locatability `1.0`, and
no-answer accuracy `1.0`. The separate 240-question source-locator benchmark
reaches Recall@10 `0.9955`; it evaluates explicit title/term routing and is not
reported as raw-vector performance.

## Automatic evidence and graph release

```bash
python -m discovery_pipeline scan-corpus \
  --database /private/rag.db --output /private/scan
python -m discovery_pipeline automatic-loci \
  --loci /private/scan/compound_loci.jsonl \
  --database /private/rag.db --output /private/approved --threshold 0.7

python -m research_pipeline.run_automatic_ancient_kg \
  --database /private/ancient_rag.db \
  --output-root /private/ancient-kg \
  --candidate-graph-version ancient-candidate-v1 \
  --approved-graph-version ancient-approved-v1 \
  --threshold 0.7

python -m research_pipeline.finalize_combined_release \
  --ancient-graph /private/ancient-kg/approved_graph \
  --modern-graph /private/modern-kg/graph \
  --ancient-database /private/ancient_rag.db \
  --modern-database /private/rag.db \
  --output-root /private/combined-kg \
  --graph-version combined-v1
```

`FormulaConcept` represents a formula name and `FormulaVariant` represents one
source-specific composition. This prevents two different Rendongtang recipes
from being merged merely because they share a name. Mechanism paths are exported
as evidence-qualified candidates; co-mention does not become a direct-target or
clinical-treatment claim.

## Release checks

```bash
python -m pytest -q --basetemp .pytest_tmp
python ancient_ocr/release_preflight.py --repository .
git diff --check
```

The release preflight rejects tracked corpora, PDFs, databases, indexes, model
weights, logs, secrets, caches, and generated runtime directories. The final
2026-08-03 release passed 148 tests before repository cleanup; the optimized
release records its fresh count in `PROJECT_STATUS.md`.

## Scientific boundary

This repository is a traceable research infrastructure project. It does not
provide clinical advice. Automatic confidence is a reproducible policy score,
not a substitute for chemical identity confirmation, wet-lab experiments,
toxicology, pharmacokinetics, randomized trials, or regulatory review.
