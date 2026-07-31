# Dual-review annotation codebook

The reviewer must open the original PDF at `pdf_page` before completing a row.
The snippet is for triage only and cannot establish evidence by itself. Reviewers
A and B work independently and must not see each other's labels.

## Required fields

| Field | Allowed values |
| --- | --- |
| `full_text_checked` | `yes`, `no` |
| `source_page_verified` | `yes`, `no` |
| `relevance_label` | `direct_burn`, `direct_wound`, `mechanistic_support`, `formula_exposure`, `safety`, `background_only`, `irrelevant`, `uncertain` |
| `study_type` | `randomized_trial`, `controlled_clinical`, `observational_clinical`, `animal`, `in_vitro`, `analytical_chemistry`, `systematic_review`, `narrative_review`, `computational`, `other`, `uncertain` |
| `evidence_direction` | `supportive`, `null`, `adverse`, `mixed`, `not_applicable`, `uncertain` |
| `supports_c1_source` | `yes`, `no`, `uncertain` |
| `supports_c2_exposure` | `yes`, `no`, `uncertain` |
| `supports_c3_burn_wound` | `yes`, `no`, `uncertain` |
| `supports_c4_target_pathway` | `yes`, `no`, `uncertain` |
| `supports_c5_safety` | `yes`, `no`, `uncertain` |
| `confidence_1_to_5` | Integer 1 through 5 |

`reviewer_id` must be one stable non-empty identifier throughout a reviewer
file. `reviewed_at` must be the row's review date in `YYYY-MM-DD` format. Do not
modify IDs, titles, hashes, page numbers, snippets, or any other source field.
Put free-text qualifications in `notes`.

## Boundary

Agreement between two reviewers is measured but does not automatically approve
evidence. Any disagreement enters adjudication. Even exact agreement remains
`dual_agreement_unadjudicated` until an identified adjudicator verifies the
source and makes an explicit final decision.

## Adjudication

The adjudicator must be different from both primary reviewers. Complete every
`final_*` field, provide `adjudicated_at` in `YYYY-MM-DD` format, and choose
exactly one `adjudication_decision`:

- `approve`: full text and source page are both verified, relevance is not
  background/irrelevant/uncertain, study type is known, and confidence is at
  least 3.
- `reject`: the candidate is not usable scientific evidence.
- `needs_more_information`: a final decision requires another source, clearer
  full text, or specialist review.

An approved annotation is still not a compound C0 identity decision and does
not authorize an inferred target, pathway, safety, efficacy, or treatment edge.
