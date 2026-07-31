# Discovery data dictionary

## C0-C5 hard gates

| Gate | Pass criterion | Failure handling |
| --- | --- | --- |
| C0 identity | Clear structure or verifiable analytical peak; unresolved mixtures are classified separately | Eliminate or move to the unresolved-compound list |
| C1 herb source | At least two independent sources, or one high-quality measured herb study | Downgrade single database predictions |
| C2 formula exposure | Extractable in decoction or detected in Rendongtang/a comparable decoction; administration route is explicit | Downgrade compounds present only in raw herb but unavailable in the preparation |
| C3 burn/wound relevance | At least one direct burn/wound study, or at least two complementary mechanism evidence items | Generic anti-inflammatory labels cannot enter the top tier |
| C4 target/pathway | At least two experimental targets, or significant disease-network enrichment with FDR below 0.05 | Docking-only targets remain prediction edges |
| C5 safety/verification | Purchasable standard, quantification method, and an acceptable experimental window | Retain high-risk items only at lower priority |

Gate statuses are `pass`, `pending`, `fail`, and `not_assessed`. A `pass` or
`fail` record must cite `evidence_ids`. Any unresolved gate prevents a final
tier; any failed gate eliminates the candidate.

## R_compound

| Dimension | Weight |
| --- | ---: |
| source_content | 0.20 |
| formula_exposure | 0.15 |
| burn_wound_evidence | 0.25 |
| target_pathway_support | 0.20 |
| synergy_complementarity | 0.10 |
| safety_verifiability | 0.10 |

Each component is normalized to `[0, 1]`. Tier 1 requires `R_compound >= 0.75`;
Tier 2 requires `0.60 <= R_compound < 0.75`. A numerical threshold never
overrides gate or review status. The sensitivity report varies each weight by
plus/minus 20 percent and the burn/wound component by plus/minus 0.10.

## Chemical identity

`candidate_id` is the stable project identifier. `InChIKey` is the chemical
identity key after curator review. Parent compounds, salts, glycosides,
aglycones, stereoisomers, and metabolites are distinct records connected by
explicit relations such as `metabolite_of`.

PubChem output fields include `cid`, `title`, `molecular_formula`,
`molecular_weight`, `inchikey`, canonical/isomeric SMILES, `query_url`, and
`response_sha256`. The status `resolved_requires_curator_review` is not a C0
pass decision.

The resolution artifact also stores `catalog_sha256`, `resolved_count`, and an
`identity_fingerprint` over candidate ID, CID, InChIKey, and raw-response hash.
The optional cache contains exact `.response.json` bytes so the response hash
can be independently recomputed.

## Literature scan

Each `compound_loci.jsonl` record points to one existing RAG chunk. Important
fields are:

| Field | Meaning |
| --- | --- |
| locus_id | Deterministic candidate/chunk pair |
| context_class | `burn_context`, `wound_context`, or `compound_only` |
| review_status | Always starts as `pending_full_text_review` |
| evidence_status | Always starts as `retrieval_candidate_not_scientific_evidence` |
| doc_id / chunk_id | Existing immutable RAG identifiers |
| pdf_page | PDF page used by `rag_cli.py source` |
| source_sha256 | SHA-256 of the source PDF |
| chunk_text_sha256 | SHA-256 of the unmodified chunk text |
| matched_terms / context_terms | Terms that triggered retrieval |
| snippet | Normalized local context for triage only |

ASCII chemical names use lexical boundaries, so `rutin` does not match
`routine`. Malformed document topic tags do not discard a true hit; they are
emptied in the record and reported under `data_quality`.

The coverage summary stores the canonical catalog hash, source database hash,
loci-file hash, candidate count, and proof that the database hash was unchanged
before and after the scan. The `doctor` recomputes all aggregates from JSONL;
summary counts alone are not trusted.

## Blinded review and adjudication

`prepare-review` balances the requested batch across all represented compounds,
targets burn/wound/compound-only contexts, and prefers distinct documents before
repeating a document. Reviewer sheets contain the same immutable source fields
in independently shuffled orders. Reviewers must not edit those fields.

Required labels are defined in `ANNOTATION_CODEBOOK.md`. `merge-reviews`
validates both sheets, calculates per-field Cohen's kappa, and creates a third-
reviewer queue for every item. Exact dual agreement still requires confirmation.
`finalize-review` accepts only a distinct adjudicator and refuses approval unless
full text and PDF page are verified, relevance is evidentiary, and confidence is
at least 3/5. Reviewer and adjudicator dates must use ISO `YYYY-MM-DD`, and an
approved item cannot retain an `uncertain` study type. The three decisions are `approve`, `reject`, and
`needs_more_information`.

Approved records preserve the two reviewer IDs, adjudicator ID, source hashes,
final labels, and immutable RAG locators. Approval applies to the reviewed
evidence record only; it does not by itself pass compound identity C0 or imply a
target, pathway, efficacy, safety, or treatment claim.

## Reviewed KG handoff

`build-reviewed-kg` reopens the modern SQLite database read-only, verifies every
approved `doc_id`, `chunk_id`, PDF page, document SHA-256, and chunk-text SHA-256,
and uses exact database text as the evidence quote. The generated overlay is
limited to `Compound -[STUDIED_IN]-> Study`. Evidence records are approved, but
the corresponding graph edges remain pending until C0 compound identity is
curator-approved.

## Mechanism evidence

Compound-target evidence tiers are: direct binding/functional experiment,
high-throughput experiment, curated database, similarity or machine-learning
prediction, and molecular docking. The first three experimental categories may
enter the high-confidence target set only after approval; database and
prediction categories remain in the extended set.

Disease evidence channels are `database_association`, `transcriptome`, and
`literature_experiment`. A disease gene is high confidence after approval when
it has a literature experiment or at least two independent channels.

PPI and pathway records require `review_status`. Approved records additionally
require source/database provenance and evidence IDs. Every included or excluded
target, PPI edge, disease gene, and pathway is retained in an audit section of
the mechanism report.
