# Blueprint compliance audit

**Audit date:** 2026-09-29

**Scope:** code and statistical infrastructure only

**Model boundary:** `senate-hierarchical-v0.9.22`

**Current artifacts:** v0.9.21 empirical artifacts are stale for this code

**Public live:** disabled

**Publication surface:** `research_only`

The machine-readable source is `BLUEPRINT_COMPLIANCE_AUDIT.json`. “Implemented” means the requirement is exercised in the relevant code path. It does not mean the changed model has passed a new historical evaluation. Capability and empirical proof are recorded separately.

## Summary

| Area | Classification | Publication implication |
|---|---|---|
| Warehouse/as-of polls and truth | Implemented with variation | Canonical content identity includes the conditional candidate-state contract and poll exclusions |
| Raw immutability and hashes | Implemented | New ingests must use the same contracts |
| Poll measurement, pollster, mode/pop, ENOP | Implemented | Optional metadata terms still require OOS testing |
| Sponsor/questionnaire/study terms | Capability implemented; empirical rebuild required | Base and three single-addition challengers are off by default |
| Fundamentals and structural priors | Implemented with variation | Challenger features remain off |
| Static/dynamic PyMC and terminal layers | Implemented | Same-family OOF must be regenerated |
| Similarity | Adapter and point-in-time source implemented | Demographic source readiness is complete; OOS ablation remains pending |
| Joint simulation | Implemented | No new run was executed |
| Institutional rules | Partial | Transition model remains disabled |
| Turnout | Intentionally deferred | Auxiliary only |
| Predictive-mixture stack | Implemented | Stored weights are stale after code changes |
| Expert/market overlays | Blocked by real-data validation | Publication policy runs unvalidated layers as compare-only/core-only |
| Market contract semantics | Blocked by refresh | Parser v4 requires verified contract-family semantics |
| Whole-cycle/nested validation | Blocked by real-data validation | Full outer-cycle regeneration required |
| Joint proper scores | Capability implemented | Historical joint scoring requires rebuild |
| Prior predictive/PPC/SBC | Capability implemented | Model-specific artifacts still pending |
| Sampler health | Capability implemented | Current reference fit must be rerun |
| Reproducibility/lineage/MCSE | Implemented with variation | Legacy absolute paths remain historical records |
| Source readiness and sealing | Implemented | Required domains are green and evidence bundle `eb-7d4691b12b12db1e` is sealed; empirical rebuild remains pending |
| Domain freshness | Implemented with variation | Retrieval and observation age are enforced only for enabled layers; source adapters must retain their timestamps |
| Acceptance gates | Implemented with variation | Machine-readable v0.9.22 empirical gates remain pending/requires rebuild |

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
- Stored v0.9.21 OOF, stack, forecast, and diagnostic artifacts cannot satisfy a
  v0.9.22 extension gate. Metadata is not rewritten across the version boundary.
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

## Current source status

The 2026-09-27 strict source audit is green for every required current and
historical domain. Candidate state is complete under the conditional contract:
there are no unresolved identity-sensitive race/cutoffs, official California
top-two identities override side-only mode, pre-primary candidate polls are
excluded, and nonbinary race/cutoffs carry explicit score exclusions. Markets
and expert ratings remain disabled optional domains.

Evidence bundle `eb-7d4691b12b12db1e` binds the ready source report and the
current/historical snapshot identities. The expensive action must still run the
selection OOF, freeze the selected poll structure, regenerate canonical OOF,
then regenerate joint scores, stack evidence, reliability calibration,
Bayesian diagnostics and sampler health before strict acceptance can be
reconsidered. The finalized `validated_model_spec_latest.json` remains a
required empirical gate.
