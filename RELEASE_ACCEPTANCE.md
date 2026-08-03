# Release acceptance

Acceptance date: 2026-08-03

## Decision

The terminal-only RAG and evidence-first knowledge graph are accepted for code
release under the automatic threshold policy. Records below confidence `0.7`
are discarded; accepted records are marked `human_reviewed=false` and retain the
policy ID, threshold, timestamp, immutable locator, and source hashes.

## Hard gates

- Ancient database, FTS, and pages JSONL each contain 26,949 pages.
- Modern database contains 584 documents, 9,870 pages, and 10,983 chunks.
- Ancient and modern FAISS manifests match database, canonical text, model, and
  layout fingerprints with zero missing or orphaned IDs.
- All 2,350 combined evidence records resolve to exact SQLite source rows.
- Combined graph validation, source doctor, Neo4j export, JSON-LD export, and
  aggregate acceptance return `valid=true` with no issues.
- The graph contains no automatically generated `TREATS` assertion.
- Release preflight finds no tracked private or generated artifact.

## Accepted graph

| Layer | Sources | Entities | Evidence | Assertions |
| --- | ---: | ---: | ---: | ---: |
| Ancient | 21 | 613 | 1,744 | 3,200 |
| Modern | 138 | 194 | 606 | 1,488 |
| Combined | 159 | 807 | 2,350 | 4,688 |

The modern layer contains 97 source-qualified compound-target-pathway-phenotype
candidate chains. Same-name Rendongtang formulas are released as two distinct
`FormulaVariant` nodes under one `FormulaConcept`.

## Retrieval gates

The independent 52-question benchmark reports final hybrid Recall@10 `0.9565`,
MRR@10 `0.8200`, page locatability `1.0`, and no-answer accuracy `1.0`. The
separate 240-question source-locator benchmark reports Recall@10 `0.9955`; its
planner-assisted result is kept distinct from raw vector metrics.

## Reproducibility

Run from the real repository with private paths configured:

```bash
python -m pytest -q --basetemp .pytest_tmp
python app/rag_cli.py --config /private/config.yaml doctor --deep
python -m knowledge_graph doctor --help
python ancient_ocr/release_preflight.py --repository .
git diff --check
git status --short
```

The private runtime retains the databases, JSONL corpora, graph JSONL, Neo4j
CSV, JSON-LD, model indexes, and ten-report aggregate acceptance evidence. The
public summary is `research_pipeline/reports/final_six_acceptance_v1.json`.

## Scientific boundary

Acceptance proves software behavior, source traceability, deterministic policy
application, and artifact integrity. It does not prove direct molecular binding,
clinical efficacy, dosage, safety, or a final active-compound ranking. Those
claims require appropriate chemical, experimental, clinical, and regulatory
evidence outside this automatic release.
