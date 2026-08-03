# Active-compound discovery pipeline

This package implements the automatic, auditable portion of compound discovery.
It resolves chemical identities, scans the frozen modern-literature database,
applies a source-verified confidence threshold, computes C0-C5 and compound
scores, and builds evidence-qualified mechanism candidates.

## Commands

```bash
python -m discovery_pipeline resolve-compounds \
  --output /private/pubchem_resolution.json \
  --cache /private/pubchem_cache

python -m discovery_pipeline scan-corpus \
  --database /private/rag.db \
  --output /private/corpus_scan

python -m discovery_pipeline automatic-loci \
  --loci /private/corpus_scan/compound_loci.jsonl \
  --database /private/rag.db \
  --output /private/automatic_loci \
  --threshold 0.7

python -m discovery_pipeline score \
  --input /private/scoring_input.json \
  --output /private/scoring_report.json

python -m discovery_pipeline mechanism \
  --input /private/mechanism_input.json \
  --output /private/mechanism_report.json

python -m discovery_pipeline doctor \
  --resolution /private/pubchem_resolution.json \
  --cache /private/pubchem_cache \
  --coverage-summary /private/corpus_scan/compound_coverage_summary.json \
  --loci /private/corpus_scan/compound_loci.jsonl \
  --database /private/rag.db \
  --output /private/intake_doctor.json
```

Every accepted locus is reopened from SQLite and checked against its immutable
`doc_id`, `chunk_id`, PDF page, source SHA-256, and chunk-text SHA-256. A score of
at least `0.7` produces machine approval with `human_reviewed=false`; lower
scores are discarded. Outputs are written atomically and existing outputs are
never overwritten silently.

## Evidence semantics

The corpus scanner emits retrieval candidates, not scientific conclusions.
Structured records distinguish study type, compound, target, pathway, outcome,
source locator, and confidence. Compound-target-pathway-phenotype chains are
candidate mechanism paths assembled from source-supported signals. They do not
assert direct binding, clinical efficacy, safety, dosage, or treatment.

The C0-C5 gates cover chemical identity, herb source, formula exposure,
burn/wound relevance, target/pathway support, and safety/verifiability. The
weighted `R_compound` score is accompanied by sensitivity analysis; a numerical
score never overrides failed provenance or release gates. See
[`DATA_DICTIONARY.md`](DATA_DICTIONARY.md).

## Public/private boundary

The public repository contains code, tests, controlled vocabularies, examples,
and aggregate reports only. PubChem raw responses, database content, literature
snippets, PDFs, identities requiring restricted data, and generated graph files
belong in the private runtime.
