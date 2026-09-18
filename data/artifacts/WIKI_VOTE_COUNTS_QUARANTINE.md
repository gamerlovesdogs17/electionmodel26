# Quarantine: certified_vote_counts.json (v0.9.21)

**Status:** parser-development only — NOT canonical truth.

The 17 Sep 2026 data-drop audit found decisive-stage, row-role, party, and
duplicate-contest defects in `data/raw/external/certified_vote_counts.json`
(Wikipedia scrape). It must not be merged into `official_senate_ledger.json`
or consumed by warehouse / chamber / gates.

Canonical historical results: FTE hashed CSV + FEC/state canvass overrides via
`midterms.evidence.build_certified_ledger_v3` → `official_senate_ledger.json`
(`truth_v1` contract in `midterms.evidence.truth_contract`).
