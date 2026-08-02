# AncientMedKG release acceptance

Acceptance date: 2026-08-02

## Decision

The local, terminal-only engineering platform is accepted for code release.
Scientific claims remain unreleased. The repository enforces that distinction:
pending KG evidence fails release validation, and discovery intake reports
`scientific_release_ready=false` even when every integrity check passes.

## Accepted evidence

| Area | Result |
| --- | --- |
| Modern corpus | 584 documents, 9,870 pages, 10,983 chunks |
| Ancient corpus | 12 books, 5,624 pages |
| OCR vNext | 113 rows: 105 candidate-adopted, 8 original-text fallbacks |
| Ancient keyword retrieval | Recall@10 0.8913, page locating 1.0 |
| Ancient Qwen vector | Recall@10 0.6739, page locating 1.0 |
| Ancient Qwen reranked hybrid | Recall@10 0.7826, page locating 1.0 |
| KG toolkit | Immutable graph builds, source verification, Neo4j/JSON-LD export, release gate |
| Rendongtang assets | 39 ontology entries, 2 same-name variants, 15 evaluation questions |
| Rendongtang KG draft | 2 sources, 17 entities, 4 evidence records, 32 assertions |
| Ancient source verification | 3 exact page/quote matches, 1 curated rule not applicable to SQLite |
| Twelve-book candidate KG | 314 pages, 369 entities, 1,316 evidence records, 2,234 assertions |
| Candidate source verification | 1,316/1,316 exact SQLite page/quote checks; draft structural errors 0 |
| Candidate Neo4j export | 369 entity, 1,316 evidence, 2,234 assertion, 12,732 provenance relationships |
| Discovery intake | 13/13 PubChem identities, 2,238 review loci, 0 integrity issues |
| Review batch | 500 blank dual-review assignments; 109 burn, 223 wound, 168 compound-only; 151 documents; independent validation passed |
| Calibration pilot | 50 parent-verified blank assignments; 25 burn, 15 wound, 10 compound-only; 47 documents |
| Complete server test suite | 123 passed for this milestone |

The controlled-vocabulary Rendongtang planner reaches 1.0 on all reported
metrics for its 15 specialized questions, including three explicit abstention
cases. This is a rule-level evaluation with fixed page labels; the raw retriever
baseline is retained separately and the planner result is not presented as a
general retrieval benchmark.

## Mandatory scientific blocks

- Four KG evidence records and all 32 assertions remain `pending`.
- The draft release gate fails only with `evidence_not_approved` and
  `edge_not_approved`, as intended.
- The two burn links are E5 `MECHANISM_TRANSFER` hypotheses, not direct ancient
  burn-treatment claims.
- PubChem identity resolution still requires curator review.
- The 2,238 literature loci require full-text and study-grade review.
- The first 500-record review batch is prepared but remains blank; no evidence
  is approved until two independent reviews and third-party adjudication finish.
- All 1,316 candidate-layer evidence records and 2,234 assertions remain
  `pending`; this broad layer does not replace the accepted Rendongtang sample.
- The 50-item calibration pilot is blank and approves no evidence. It must be
  reviewed before the remaining 450 formal-batch records.
- C0-C5 gates, compound scores, targets, pathways, safety, exposure, and
  experimental validation are not approved.

## Reproducibility

The private vNext database, indexes, raw PubChem responses, and literature loci
stay outside Git. Public reports contain fingerprints and aggregate counts only.
The canonical commands are documented in each module README.

Before every GitHub milestone, run from the actual server repository:

```bash
.conda/bin/python -m pytest -q
.conda/bin/python -m knowledge_graph validate \
  --graph research_pipeline/output/acceptance_20260731/kg_draft
.conda/bin/python -m knowledge_graph verify-sources \
  --graph research_pipeline/output/acceptance_20260731/kg_draft \
  --ancient-database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db
.conda/bin/python ancient_ocr/release_preflight.py --repository .
git diff --check
git status --short
```

The separate private candidate layer is regenerated and checked with:

```bash
.conda/bin/python -m research_pipeline.build_ancient_candidate_kg \
  --database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db \
  --output-bundle /private/kg/ancient-candidate-v1/kg_evidence_bundle.json \
  --output-manifest /private/kg/ancient-candidate-v1/candidate_pages.jsonl \
  --graph-version ancient-candidate-2026-07-31-v1

.conda/bin/python -m knowledge_graph verify-sources \
  --graph /private/kg/ancient-candidate-v1/graph \
  --ancient-database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db
```

The aggregate KG doctor includes the scientific release gate. For this pending
draft it must exit non-zero, while source and Neo4j verification remain valid,
and its issue-code set must be exactly `evidence_not_approved` and
`edge_not_approved`. A zero exit before documented approvals would be a release
integrity failure.

For a private discovery intake, additionally run:

```bash
.conda/bin/python -m discovery_pipeline doctor \
  --resolution /private/run/pubchem_resolution.json \
  --cache /private/run/pubchem_cache \
  --coverage-summary /private/run/corpus_scan/compound_coverage_summary.json \
  --loci /private/run/corpus_scan/compound_loci.jsonl \
  --database app/data/rag.db \
  --output /private/run/intake_doctor.json
```

Public release is allowed only when tests pass, preflight reports no violations,
`git diff --check` is clean, and the staged file list contains no private data.
