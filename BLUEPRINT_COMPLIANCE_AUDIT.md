# Blueprint compliance audit

**Audit date:** 2026-10-06

**Scope:** code capability and current empirical status

**Model boundary:** `senate-hierarchical-v0.9.24` research-only development;
immutable completed rebuild remains `senate-hierarchical-v0.9.23`
(as-of `2026-10-05`, evidence bundle `eb-19f9ccea0cc3812a`)

**Current artifacts:** v0.9.23 sealed forecast/release/shadow identities are
historical records. v0.9.24 adds contest presentation, MT/ID/NE audits,
multiway fail-closed, and poll-mechanics repairs without a fresh nested OOF /
publication rebuild.

**Public live:** disabled

**Publication surface:** `research_only`

The machine-readable source is `BLUEPRINT_COMPLIANCE_AUDIT.json`. “Implemented” means the requirement is exercised in the relevant code path. It does not mean the changed model has passed a new historical evaluation. Capability and empirical proof are recorded separately.

## Summary

| Area | Classification | Publication implication |
|---|---|---|
| Warehouse/as-of polls and truth | Implemented with variation | Canonical content identity includes the conditional candidate-state contract and poll exclusions |
| Raw immutability and hashes | Implemented | New ingests must use the same contracts |
| Poll measurement, pollster, mode/pop, ENOP | Implemented | v0.9.24 removes n/quality double-count; absolute recency and calendar Δt repaired; await fresh OOF |
| Sponsor/questionnaire/study terms | Implemented and evaluated | Cross-fitted selection retained the all-off base; optional effects remain disabled |
| Fundamentals and structural priors | Implemented with variation | Experience/special/turnout challengers remain off |
| Static/dynamic PyMC and terminal layers | Implemented and evaluated | Canonical OOF is bound to the prior validated model spec; dynamic PyMC earned zero stack mass |
| Similarity | Implemented and evaluated | Point-in-time source and same-family ablation evidence pass |
| Joint simulation | Implemented | v0.9.23 rebuild completed; v0.9.24 has no new 50k publication run |
| Institutional rules / multiway | Partial + plurality path | Plurality fail-closed implemented; runoff transition still disabled |
| Shared race presentation | Implemented | Alaska UI parity without mutating RCV draws |
| Turnout | Intentionally deferred | Auxiliary only |
| Predictive-mixture stack | Implemented and evaluated | Validated weights remain historical until a v0.9.24 rebuild |
| Expert/market overlays | Intentionally deferred | Disabled/compare-only layers do not enter the production research forecast |
| Whole-cycle/nested validation | Implemented historically | Formal OOF remains 60/30; broader lead grid wired but not re-run |
| Acceptance gates | Passing for sealed v0.9.23 rebuild | v0.9.24 cannot promote using v0.9.23 validated-model-spec identity |

## Important v0.9.24 notes

- Outside-model MT/ID/NE/IA/KS map oddities are audit triggers only, never tuning targets.
- Corrective pass: MT/ID/NE official general ballots are multiway plurality; binary
  non-major probabilities for those races are superseded and withheld. SD remains
  binary I-v-R after the Democratic nominee withdrew.
- Ballot authority is independent official SOS / election-admin evidence
  (`data/current/official_ballot_fields_2026.json`); the current registry is not
  its own ballot authority.
- Candidate-experience / special-election challengers are **not yet implemented**
  (config stubs only), not merely disabled.
- Nebraska in-architecture poll coverage remains a source-coverage limitation
  (1 exact Osborn–Ricketts row); structure is now multiway regardless.
- `PUBLIC_LIVE_ENABLED=False`; fresh OOF/rebuild required before any promotion claim.
