# Discovery data dictionary

## C0-C5 hard gates

| Gate | Pass criterion | Failure handling |
| --- | --- | --- |
| C0 identity | Clear structure or verifiable analytical peak | Eliminate unresolved mixtures or retain them outside ranked compounds |
| C1 herb source | Two independent sources, or one high-quality measured-herb study | Downgrade database-only predictions |
| C2 formula exposure | Extractable in decoction or detected in the formula/comparable decoction | Downgrade raw-herb-only compounds |
| C3 burn/wound relevance | Direct burn/wound evidence or complementary mechanism evidence | Generic activity labels cannot enter the top tier |
| C4 target/pathway | Experimental targets or significant disease-network enrichment | Docking-only targets remain predictions |
| C5 safety/verification | Standard availability, quantification method, and acceptable window | Retain unresolved risk only at lower priority |

Gate states are `pass`, `pending`, `fail`, and `not_assessed`. A `pass` or
`fail` record cites evidence IDs. Failed gates eliminate a candidate; unresolved
gates prevent a final tier.

## Compound score

| Dimension | Weight |
| --- | ---: |
| source_content | 0.20 |
| formula_exposure | 0.15 |
| burn_wound_evidence | 0.25 |
| target_pathway_support | 0.20 |
| synergy_complementarity | 0.10 |
| safety_verifiability | 0.10 |

Each component is normalized to `[0, 1]`. Tier 1 requires
`R_compound >= 0.75`; Tier 2 requires `0.60 <= R_compound < 0.75`. Sensitivity
analysis varies every weight by plus/minus 20 percent and varies burn/wound
weight by plus/minus 0.10. Scores never override evidence or release gates.

## Chemical identity

`candidate_id` is the stable project identifier. Parent compounds, salts,
glycosides, aglycones, stereoisomers, and metabolites remain distinct records
and use explicit relations such as `metabolite_of`.

PubChem resolution stores `cid`, title, molecular formula, molecular weight,
InChIKey, canonical/isomeric SMILES, query URL, and raw-response SHA-256. The
artifact also stores catalog SHA-256, resolved count, and an identity fingerprint
over candidate ID, CID, InChIKey, and response hash. This verifies which
identity record was used; it is not wet-lab structure confirmation.

## Literature locus

Each `compound_loci.jsonl` row points to an immutable RAG chunk:

| Field | Meaning |
| --- | --- |
| `locus_id` | Deterministic candidate/chunk pair |
| `context_class` | `burn_context`, `wound_context`, or `compound_only` |
| `doc_id` / `chunk_id` | Existing immutable RAG identifiers |
| `pdf_page` | Physical PDF page accepted by `rag_cli.py source` |
| `source_sha256` | SHA-256 of the source PDF |
| `chunk_text_sha256` | SHA-256 of the unmodified chunk text |
| `matched_terms` / `context_terms` | Terms that triggered the candidate |
| `snippet` | Normalized local context for evidence extraction |

ASCII chemical names use lexical boundaries, so `rutin` does not match
`routine`. Malformed topic tags are reported but do not suppress a true text
match. The coverage report records the database hash before and after scanning,
and the doctor recomputes all JSONL aggregates instead of trusting summaries.

## Automatic acceptance

`automatic-loci` reopens every candidate from SQLite and rejects identity,
page, source-hash, or text-hash mismatches. The versioned policy accepts scores
`>= 0.7` and discards lower scores. Accepted records contain:

- `review_status=approved`
- `human_reviewed=false`
- policy ID and threshold
- approval timestamp
- exact source locator and evidence hash

This status means the machine policy and engineering gates passed. It does not
mean an expert reviewed the record and does not establish efficacy, safety,
direct binding, exposure, dosage, or clinical treatment.

## Structured evidence

Structured modern records distinguish compounds, genes/proteins, pathways,
phenotypes/outcomes, study type, evidence quote, and source locator. Supported
study types include randomized and controlled clinical studies, animal studies,
in-vitro experiments, analytical chemistry, computational work, and reviews.

Mechanism chains require source-supported nodes and edges and preserve their
evidence IDs. Co-mention and pathway signals are represented as candidate
mechanism relations. The automatic graph does not generate `TREATS` edges.
