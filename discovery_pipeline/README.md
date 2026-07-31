# Active-compound discovery pipeline

This package implements the auditable computational part of the Rendongtang
active-compound work package. It is deliberately conservative: retrieval hits,
database associations, predictions, and docking results are never promoted to
experimental evidence automatically.

## Scope

The pipeline provides four analysis stages and one integrity gate:

1. Resolve the chemical identity of the initial honeysuckle/licorice candidate
   pool with PubChem PUG REST and retain query provenance.
2. Scan the existing modern-literature `rag.db` once and emit traceable
   page/chunk review candidates.
3. Apply the proposal's C0-C5 gates, weighted `R_compound` score, final-tier
   release rules, and 15 sensitivity scenarios.
4. Build an evidence-tiered mechanism report with disease-gene integration,
   experimental target filtering, PPI modules, pathway enrichment, and
   compound complementarity.
5. Run an end-to-end doctor that rehashes the catalog, raw PubChem responses,
   modern database, coverage summary, and every literature locus.

It does not perform chemical-content annotation, expert review, wet-lab
experiments, or clinical efficacy inference. Those inputs must be supplied as
reviewed evidence records before a final candidate tier can be released.

## Commands

Run commands from the repository root. Store all real outputs outside the Git
working tree; they can contain copyrighted snippets and local source paths.

```bash
python -m discovery_pipeline resolve-compounds \
  --output /private/rendongtang/pubchem_resolution.json \
  --cache /private/rendongtang/pubchem_cache

python -m discovery_pipeline scan-corpus \
  --database app/data/rag.db \
  --output /private/rendongtang/corpus_scan

python -m discovery_pipeline score \
  --input /private/rendongtang/reviewed_scoring.json \
  --output /private/rendongtang/scoring_report.json

python -m discovery_pipeline mechanism \
  --input /private/rendongtang/reviewed_mechanism.json \
  --output /private/rendongtang/mechanism_report.json

python -m discovery_pipeline doctor \
  --resolution /private/rendongtang/pubchem_resolution.json \
  --cache /private/rendongtang/pubchem_cache \
  --coverage-summary /private/rendongtang/corpus_scan/compound_coverage_summary.json \
  --loci /private/rendongtang/corpus_scan/compound_loci.jsonl \
  --database app/data/rag.db \
  --output /private/rendongtang/intake_doctor.json
```

Every command refuses to overwrite an existing output. This makes interrupted
runs visible and prevents a later run from silently replacing an accepted
artifact. Use a new version directory for each release.

## Release rules

- A corpus hit is a review candidate, not scientific evidence.
- C0-C5 `pass`/`fail` decisions and approved component scores require one or
  more evidence IDs.
- Any failed gate or rejected score eliminates a candidate.
- Any pending gate or score keeps the numerical result provisional; it cannot
  receive Tier 1 or Tier 2 status.
- Only approved experimental compound targets enter the primary mechanism.
  Curated, predicted, and docking targets remain in the extended set.
- Disease genes enter the high-confidence set only with experimental evidence
  or at least two independent evidence channels.
- Only approved, sourced PPI edges with score at least 0.7 are used. PPI
  modules contain at least five nodes.
- Pathway significance uses a hypergeometric test and Benjamini-Hochberg FDR;
  all approved pathways in the tested collection, including zero-overlap sets,
  count toward multiple testing.

See [DATA_DICTIONARY.md](DATA_DICTIONARY.md) for the exact gates and fields.

## Reproducibility and provenance

The compound catalog separates parent compounds from metabolites and includes
the proposal-specified identifiers for chlorogenic acid (PubChem CID 1794427)
and glycyrrhizic acid (PubChem CID 14982). Machine-resolved identities remain
`requires_curator_review` until a researcher checks name, structure,
stereochemistry, salt form, and source consistency.

PubChem requests use the public [PUG REST service](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest).
Each normalized record contains the request URL and SHA-256 of the raw response;
when `--cache` is supplied, the exact response bytes are retained for hash
verification. The corpus scan records `doc_id`, `chunk_id`, PDF page, DOI,
source-file SHA-256, chunk-text SHA-256, matching terms, and a short review
snippet. It hashes the database before and after scanning and refuses the run if
the source changes.

A valid doctor report means that computational intake is internally consistent.
It intentionally reports `scientific_release_ready=false` until chemical
identity, full text, C0-C5, mechanism, and experimental reviews are complete.

## Verification

```bash
python -m unittest discover -s discovery_pipeline/tests -v
python -m compileall -q discovery_pipeline
python -m discovery_pipeline --help
```

The files under `examples/` are synthetic interface fixtures. They are not
Rendongtang results and must never be cited as biological evidence.

The public, snippet-free result of the first real intake run is stored at
`reports/intake_baseline_v1.json`. Raw PubChem responses, page/chunk loci, local
database paths, and copyrighted snippets remain outside the Git repository.
