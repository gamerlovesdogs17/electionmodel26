# Blueprint compliance audit

**Audit date:** 2026-10-03

**Scope:** code capability and current empirical status

**Model boundary:** `senate-hierarchical-v0.9.22`

**Current artifacts:** v0.9.22 research rebuild validated against evidence bundle `eb-3e91ca3a90628d69`

**Public live:** disabled

**Publication surface:** `research_only`

The machine-readable source is `BLUEPRINT_COMPLIANCE_AUDIT.json`. “Implemented” means the requirement is exercised in the relevant code path. It does not mean the changed model has passed a new historical evaluation. Capability and empirical proof are recorded separately.

## Summary

| Area | Classification | Publication implication |
|---|---|---|
| Warehouse/as-of polls and truth | Implemented with variation | Canonical content identity includes the conditional candidate-state contract and poll exclusions |
| Raw immutability and hashes | Implemented | New ingests must use the same contracts |
| Poll measurement, pollster, mode/pop, ENOP | Implemented | Optional metadata terms still require OOS testing |
| Sponsor/questionnaire/study terms | Implemented and evaluated | Cross-fitted selection retained the all-off base; optional effects remain disabled |
| Fundamentals and structural priors | Implemented with variation | Challenger features remain off |
| Static/dynamic PyMC and terminal layers | Implemented and evaluated | Canonical OOF is bound to the validated model spec; dynamic PyMC earned zero stack mass |
| Similarity | Implemented and evaluated | Point-in-time source and same-family ablation evidence pass |
| Joint simulation | Implemented and evaluated | Current research forecast retained 50,000 correlated draws |
| Institutional rules | Partial | Transition model remains disabled |
| Turnout | Intentionally deferred | Auxiliary only |
| Predictive-mixture stack | Implemented and evaluated | Validated weights are approximately 21.9% PyMC and 78.1% state-space |
| Expert/market overlays | Intentionally deferred | Disabled/compare-only layers do not enter the production research forecast |
| Market contract semantics | Intentionally deferred | Market layer is disabled in the effective production configuration |
| Whole-cycle/nested validation | Implemented and evaluated | Four-cycle 60/30-day canonical OOF is frozen and lineage-bound |
| Joint proper scores | Implemented and evaluated | Historical joint score artifact passes its current lineage gate |
| Prior predictive/PPC/SBC | Implemented with variation | Prior/PPC gates pass; full-model SBC remains a nonblocking future diagnostic |
| Sampler health | Implemented and evaluated | Current reference fit passes numerical-quality requirements |
| Reproducibility/lineage/MCSE | Implemented with variation | Legacy absolute paths remain historical records |
| Source readiness and sealing | Implemented | Required domains are green and evidence bundle `eb-3e91ca3a90628d69` is sealed and validated |
| Domain freshness | Implemented with variation | Retrieval and observation age are enforced only for enabled layers; source adapters must retain their timestamps |
| Acceptance gates | Implemented and passing | G1–G11: 11 pass / 0 partial / 0 fail; extension gates and run coherence pass |

## Important deviations and decisions

- The current turnout layer remains an auxiliary diagnostic. It is not described as propagated uncertainty.
- Rare common movement is represented with heavy-tailed shared factors rather than a separate disaster-mixture parameter. A new mixture is deferred until it has OOS support.
- Optional sponsor, questionnaire and shared-study effects use shrinkage and are disabled by default. Enabling an explicit study effect disables heuristic study downweighting unless the caller explicitly overrides that choice.
- v0.9.22 evaluates only three positive single-term additions: study, sponsor,
  and questionnaire. Each changes one reference setting, remains excluded from
  stacking, and is selected by an outer-cycle cross-fitted majority-plus-CRPS
  parsimony rule. Multi-term interactions are intentionally deferred.
- Unvalidated expert and market overlays are compare-only for publication-quality execution. The reference fit runs core-only for those layers until an exact-weight nested-OOS contract exists.
- A 2018 demographic table is labeled as a reuse approximation. No earlier historical snapshot is fabricated.
- Ordinary FRED latest/revised data and World Bank annual data are degraded substitutes for historical replay; only traceable ALFRED real-time vintages qualify.
- Single-holdout calibration in the legacy validation report is exploratory and cannot satisfy formal reliability acceptance.
- Historical v0.9.21 OOF, stack, forecast, and diagnostic artifacts remain
  historical records and cannot substitute for the lineage-bound v0.9.22 set.
- Same-family ablations freeze reference and challenger configurations and must
  show exactly one changed feature. No-op ablations are ineligible.
- `source-readiness` is the pre-fit authority. `prepare-evidence --mode seal`
  writes a content-addressed bundle only after every hard current and historical
  domain is ready. OOF, stack and forecast stages require that exact bundle ID.
- Poll-structure validation is deliberately two-stage. The selection pass may
  compare the all-off reference with one-term structural challengers, then
  freezes `validated_model_spec_candidate.json`. A separately keyed canonical
  pass refits the static and dynamic candidates with that selected structure.
  Only the canonical OOF may feed the stack, and the finalized validated model
  spec must bind the bundle, source-readiness report, selected structure,
  canonical OOF, stack and calibration artifact before publication fitting.
- Candidate identity is conditionally required. Ordinary binary races may use
  a content-addressed side-only state, while replacements, withdrawals,
  top-two pairings, and incompatible matchup evidence require point-in-time
  resolution. Pre-primary candidate polls are excluded without using eventual
  nominees. Structurally nonbinary race/cutoffs remain visible but are excluded
  from binary scoring with a recorded reason.

## Current source and validation status

The 2026-10-03 strict source audit is green for every required current and
historical domain. Candidate state is complete under the conditional contract:
there are no unresolved identity-sensitive race/cutoffs, official California
top-two identities override side-only mode, pre-primary candidate polls are
excluded, and nonbinary race/cutoffs carry explicit score exclusions. Markets
and expert ratings remain disabled optional domains.

Evidence bundle `eb-3e91ca3a90628d69` binds the ready source report and the
current/historical snapshot identities. The completed rebuild selected the
all-off PyMC poll structure, regenerated canonical OOF, stack, reliability,
joint scores and Bayesian diagnostics, then passed the independent rebuild,
run coherence, G1–G11, and required extension gates. Release identity
`truth_v1_v0.9.22_eb-3e91ca3a90628d69` binds the finalized model spec. Public
live remains disabled and the surface remains `research_only`.
