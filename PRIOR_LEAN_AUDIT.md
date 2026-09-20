# `prior_lean` construction audit

Status: provenance identified; source methodology has not been replaced.

## Current 2026 construction

`generate_2026_races` in `midterms/evidence/fixtures.py` supplies the current
race rows. It starts from the hand-authored `BASE_LEANS` table, applies a fixed
scale and clipping rule, adds seeded random noise, then rounds the result.
The same 50-state base table is duplicated in `midterms/evidence/official_ballot.py`.
The tables currently agree for all 50 states.

The 2026 construction reads no presidential or Senate election result. It has
no national normalization, historical averaging or regression, or recency
weighting. Candidate identity and incumbency are separate inputs and do not
enter the lean expression directly. The random stream is shared with other
fixture generation, so unrelated changes to generation order could change
the per-state perturbation. This is an approximate research fixture prior,
not a measured election-derived partisan lean.

`races_official.parquet` is assembled by `write_official_ballot_store` in
`midterms/evidence/official_ballot.py`; that builder appends the generated
2026 rows to the historical ledger rows. `Warehouse.build_as_of` reads these
race rows but applies `available_at` filtering to polls and results, not to
`prior_lean`. `RACE_COLUMNS` has no prior-lean source or vintage field.

## Historical cycles

`midterms/evidence/official_ledger.py` uses a contest's supplied `prior_lean`
when present and falls back to `BASE_LEANS` otherwise. This differs from the
2026 scaled-and-noisy fixture construction. Historical ledger values therefore
must be audited per contest before describing the cross-cycle field as one
consistent measured state baseline.

## Consequence

No state value was changed in this pass. A replacement requires a dated,
licensed source series, a predeclared national normalization and weighting
rule, and a point-in-time vintage for every cycle. Until then, diagnostics
should label the field `approximate_fixture_prior` and publication review
should not describe it as an election-derived generic partisan lean.
