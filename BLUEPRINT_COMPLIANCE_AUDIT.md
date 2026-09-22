# Blueprint compliance audit

**Audit date:** 2026-09-22

**Scope:** code and statistical infrastructure only

**Current artifacts:** stale relative to this code

**Public live:** disabled

**Publication surface:** `research_only`

The machine-readable source is `BLUEPRINT_COMPLIANCE_AUDIT.json`. “Implemented” means the requirement is exercised in the relevant code path. It does not mean the changed model has passed a new historical evaluation. Capability and empirical proof are recorded separately.

## Summary

| Area | Classification | Publication implication |
|---|---|---|
| Warehouse/as-of polls and truth | Implemented with variation | Candidate timeline remains a blocker |
| Raw immutability and hashes | Implemented | New ingests must use the same contracts |
| Poll measurement, pollster, mode/pop, ENOP | Implemented | Optional metadata terms still require OOS testing |
| Sponsor/questionnaire/study terms | Blocked by real-data validation | Off by default |
| Fundamentals and structural priors | Implemented with variation | Challenger features remain off |
| Static/dynamic PyMC and terminal layers | Implemented | Same-family OOF must be regenerated |
| Similarity | Partial | Point-in-time demographic provenance is incomplete |
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
| Domain freshness | Implemented with variation | Next source refresh must populate statuses |
| Acceptance gates | Implemented with variation | New empirical gates remain pending/requires rebuild |

## Important deviations and decisions

- The current turnout layer remains an auxiliary diagnostic. It is not described as propagated uncertainty.
- Rare common movement is represented with heavy-tailed shared factors rather than a separate disaster-mixture parameter. A new mixture is deferred until it has OOS support.
- Optional sponsor, questionnaire and shared-study effects use shrinkage and are disabled by default. Enabling an explicit study effect disables heuristic study downweighting unless the caller explicitly overrides that choice.
- Unvalidated expert and market overlays are compare-only for publication-quality execution. The reference fit runs core-only for those layers until an exact-weight nested-OOS contract exists.
- A 2018 demographic table is labeled as a reuse approximation. No earlier historical snapshot is fabricated.
- Ordinary FRED latest/revised data and World Bank annual data are degraded substitutes for historical replay; only traceable ALFRED real-time vintages qualify.
- Single-holdout calibration in the legacy validation report is exploratory and cannot satisfy formal reliability acceptance.

## Required real-data work

The next expensive run must first ingest candidate timelines, point-in-time demographic snapshots where available, complete ALFRED history, and refreshed market contract semantics. It must then regenerate same-family nested OOF predictions, joint scores, stack evidence, Bayesian diagnostics, and sampler health before strict acceptance can be reconsidered.
