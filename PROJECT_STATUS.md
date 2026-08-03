# Project status

Updated: 2026-08-03

## Release state

The six-deliverable release is complete. The active policy automatically
discards confidence scores below `0.7` and approves the remaining source-
verified records with `human_reviewed=false`.

| Component | Accepted result |
| --- | --- |
| Modern RAG | 584 documents / 9,870 pages / 10,983 chunks |
| Ancient RAG | 22 books / 26,949 pages / 26,949 FTS rows |
| Ancient index | 26,949 Qwen page vectors; zero missing/orphan rows; all fingerprints match |
| Ancient KG | 21 sources / 613 entities / 1,744 evidence / 3,200 assertions |
| Modern KG | 138 sources / 194 entities / 606 evidence / 1,488 assertions |
| Combined KG | 159 sources / 807 entities / 2,350 evidence / 4,688 assertions |
| Mechanism chains | 97 compound-target-pathway-phenotype candidates |
| Automatic treatment claims | 0 `TREATS` assertions |
| Optimized public test suite | 130 passed |

All 2,350 released evidence records resolve to exact read-only SQLite source
rows. Neo4j and JSON-LD exports have independent SHA-256 manifests. The combined
content fingerprint is
`6840888ccf92c761b0392f497ed8eabb54c2c158cee4e853d498c2f665f8771e`.

## Retrieval

| Benchmark | Channel | Recall@5 | Recall@10 | MRR@10 | Page locatable | No-answer |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Independent 52 | keyword | 0.8696 | 0.8913 | 0.6728 | 1.0 | 1.0 |
| Independent 52 | qwen-vector | 0.5652 | 0.5870 | 0.4746 | 1.0 | 1.0 |
| Independent 52 | reranked hybrid | 0.9130 | 0.9565 | 0.8200 | 1.0 | 1.0 |
| Source locator 240 | routed channels | 0.9955 | 0.9955 | 0.9795 | 1.0 | 1.0 |

The 240-question result measures explicit source-title routing and term
normalization and is not presented as raw-vector performance. One known locator
failure remains: the `烫伤` target on physical page 47 of `疡医大全`.

## Formula and mechanism examples

`FormulaConcept` and `FormulaVariant` prevent same-name formulas from collapsing.
The two Rendongtang records in `医学心悟` point to distinct physical pages,
compositions, evidence records, and composition fingerprints.

One modern chlorogenic-acid chain resolves DOI `10.2147/IJN.S594688`, physical
page 20, and its immutable chunk to NFKB1, NFE2L2, VEGFA, and NLRP3 signals,
Nrf2/HO-1 and NLRP3 pathways, and wound-healing/inflammation/oxidative-stress/
angiogenesis/antibacterial outcomes. Its confidence is `0.9325`. The graph marks
this as a source-supported mechanism candidate, not direct binding or clinical
proof.

## Quality boundary

This is a strong research engineering release, not a clinical knowledge base.
Automatic confidence does not replace chemical structure confirmation,
pharmacokinetics, toxicology, wet-lab validation, randomized trials, or
regulatory assessment. No treatment recommendation should be generated from the
graph alone.

## Public repository boundary

Git contains code, tests, schemas, examples, and aggregate reports. Private
PDFs, OCR text, databases, FAISS indexes, model weights, raw snippets, logs,
source paths, and credentials are excluded and enforced by release preflight.
