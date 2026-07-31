# Candidate catalog

`compound_candidates_v1.json` is a small, proposal-aligned identity-resolution
queue for honeysuckle and licorice. It is not a complete phytochemical census
and it is not a ranked result.

Additions require a stable `candidate_id`, canonical name, herb source, role,
and aliases needed for corpus retrieval. Parent compounds and metabolites must
remain separate. An expected PubChem CID should be set only when an authoritative
project source fixes that identity; a mismatch then fails resolution rather
than being silently accepted.
