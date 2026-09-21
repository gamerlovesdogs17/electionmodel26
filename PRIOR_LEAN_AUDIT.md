# `prior_lean` construction audit

Status: official vote-count sources and the derived point-in-time prior are
connected to warehouse snapshots. Full-model validation remains pending.

## Verified source layer added 2026-09-20

`midterms/evidence/presidential_results.py` verifies immutable raw Federal
Election Commission files for 2012, 2016, 2020, and 2024 against pinned
SHA-256 digests. The parser stores statewide major-party vote counts for 50
states plus DC, with source URL, election date, conservative FEC
publication-based `available_at`, retrieval time, and parser version. DC is
flagged ineligible for a Senate-state prior. Maine and Nebraska contribute one
statewide row each. The normalized store and source manifest have separate
integrity hashes. Source years are selected from `available_at`.
The `build-presidential-vote-store` CLI command re-verifies and rebuilds this
count-only store without running a forecast.

The files are listed by the [FEC election-results index](https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/).
Availability bounds follow the [2012 notice](https://www.fec.gov/updates/federal-elections-2012-compiled-election-results-now-available/),
[2016 notice](https://www.fec.gov/updates/election-results-publication-now-available-2018/),
[2020 digest](https://www.fec.gov/updates/week-february-1-5-2021/), and
[2024 digest](https://www.fec.gov/updates/week-of-january-20-24-2025/).

The fixed two-source weighting rule is declared as newest 2/3, previous 1/3.
Publication eligibility requires the verified derived-prior source and per-race
provenance. A source manifest alone cannot satisfy that gate.

## Derived prior and model-input integration

`midterms/evidence/prior_sources.py` defines a versioned source record with
observation date, `available_at`, attribution URL, source SHA-256, local value,
national reference, and source kind. A pure function computes predeclared
weighted local-minus-national components, rejects observations unavailable at
the requested snapshot, and records each component and a snapshot hash. Fixture
fallback is explicit and marked ineligible for production.

`midterms/evidence/presidential_prior.py` computes the statewide Democratic
minus Republican two-party margin, subtracts the corresponding national
two-party margin (including DC in the national count), and combines the two
latest published elections at 2/3 and 1/3. It validates all 51 statewide
vote-count rows per year, source hashes, and `available_at`. One available
source gets weight 1.0 and is marked non-production. The derived 50-state side
table records each component and is sealed by a snapshot SHA-256. It never
reads Senate outcomes or adds random noise.

`Warehouse.build_as_of` overlays that side table onto both historical and
current race rows at the requested snapshot date. The resulting model input
contains the derived mean and per-race provenance hash; the evidence snapshot
records the sealed side-table path and source fingerprint. The underlying
`races_official.parquet` retains its fixture field for historical traceability;
it is not the production structural prior.

## Retired fixture construction

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
2026 rows to the historical ledger rows. Repository-backed warehouse snapshots
now replace the old `prior_lean` field before model fitting. Publication
eligibility rejects fixture or unprovenanced structural priors.

## Historical cycles

`midterms/evidence/official_ledger.py` may still store a contest's supplied
`prior_lean` or a `BASE_LEANS` fallback. Every repository-backed historical
warehouse snapshot replaces that field using the same statewide presidential-
relative method at its own `as_of`. By `available_at`, 2018 and pre-election
2020 use 2016/2012; 2022 and pre-election 2024 use 2020/2016; 2026 uses
2024/2020. Historical truth fields are unchanged.

## Consequence

No state value was hand-edited. The derived store is deterministic and
connected to race snapshots. Old forecasts and stack/validation artifacts
predate this meaning of `prior_lean`; their reported performance is stale
until the complete new validation chain runs.
