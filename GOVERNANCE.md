# Governance — Senate forecasting system

Blueprint §§11–12 roles and change control (Senate research production).

## Public live probabilities
**HOLD (4 Oct 2026 v0.9.23 development boundary):** `PUBLIC_LIVE_ENABLED=False`.
Truth_v1 ledger/expectations are unified; Wikipedia vote scrape remains quarantined.
The sealed multi-cycle research rebuild, independent rebuild, G1–G11, extension
gates, and run coherence pass. This establishes a validated production research
specification; it does not authorize live publication. `publish-live` remains
separately locked pending an explicit future review.
New blueprint extensions separate code capability from empirical proof. A
capability marked implemented in `BLUEPRINT_COMPLIANCE_AUDIT.json` does not
permit publication while its empirical gate is pending, stale, requires a
refresh, or requires a rebuild.
Strict research acceptance requires both the unchanged G1-G11 report and the
separate version-bound blueprint extension evaluation. Old-version empirical
artifacts remain historical records and cannot be relabeled as current.

Current surface: `research_only`. Do not label research runs as live.

v0.9.23 repairs current candidate/matchup evidence and is not empirically
validated. A separate limited-validation adapter supports reviewed binary
Independent-versus-Republican targets with compatible polls while preserving
Independent ballot identity and explicit caucus accounting. South Dakota is
limited-supported prior-only with zero polls and wide uncertainty; Alaska RCV/multiway remains
unsupported. No current forecast or chamber probability may be promoted under this boundary. The
v0.9.22 research freeze below remains immutable and is the last completed
empirical reference.

## Frozen v0.9.22 specification

The frozen statistical specification is bound by
`validated_model_spec_latest.json`, evidence bundle `eb-3e91ca3a90628d69`, and
release identity `truth_v1_v0.9.22_eb-3e91ca3a90628d69`. Substantive changes to
poll structure, formulas, stack candidates or weights, calibration, uncertainty,
turnout propagation, institutional transitions, fundamentals, historical
training scope, source semantics, or probability generation require a new
research version and appropriate OOS validation. Compatible operational,
reporting, documentation, and ingestion repairs may retain v0.9.22 only when
they do not redefine the validated specification or forecast probabilities.

## Evidence preparation boundary

Expensive validation must consume a sealed, content-addressed evidence bundle.
The preparation stage may audit and refresh only registered safe adapters, then
must pass strict source readiness before writing or committing the bundle. The
model stage checks out the exact evidence commit, verifies the bundle ID, and
must not refresh evidence while fitting. Cache hits are performance aids only;
restored artifacts still have to pass their own semantic lineage checks.

The frozen v0.9.22 source audit is green and evidence bundle
`eb-3e91ca3a90628d69` is sealed. It is the evidence boundary used by the
completed v0.9.22 research validation; it does not permit live publication.
The v0.9.23 source audit is green and is reported separately from forecast-model
coverage. Forecast coverage remains incomplete for Alaska RCV. Its historical equivalence report
shows no formal 2018/2020/2022/2024 60/30 input change, but that finding does not
relabel old validation artifacts as v0.9.23 artifacts.

The exceptional adapter is not an ordinary stack candidate. Its predeclared
historical analog exercise contains four scorable lead-time cases across two
elections, is classified `limited_validation_exception_model`, and cannot
support a calibration claim. A zero-poll binary I-v-R contest may be prior-only
only with a verified point-in-time state anchor and explicitly wide uncertainty.
A nonbinary or otherwise unsupported contest remains withheld. Chamber output
fails closed instead of substituting the incumbent holder as certainty.
Missing real evidence in a future bundle must be ingested or refreshed;
fixtures, current registries, and degraded substitutes cannot be relabeled to
clear a historical source gate.

Candidate identity is conditionally required as a leakage-control domain.
Ordinary races may use a fingerprinted D-vs-R side-only state when exact
identity does not select any input. Candidate transitions, top-two pairings,
and ambiguous matchups remain fail-closed. Before a primary, future nominee
identity may not be backfilled and candidate-named polls are excluded.
Structurally nonbinary race/cutoffs stay in evidence diagnostics but are
excluded from binary proper scoring with a machine-readable reason.

Poll-structure selection and production validation use separate expensive
passes. The first pass evaluates only same-family one-term structural changes
and freezes a candidate spec. The second pass regenerates canonical static and
dynamic OOF predictions under the selected configuration. Structural challenger
labels never enter the production stack. A final validated model spec must bind
the evidence bundle, source-readiness hash, selection, canonical OOF, stack and
calibration artifacts before any publishable fit. A cache hit does not relax
any of these semantic checks.

## Roles
| Role | Responsibility |
| --- | --- |
| Research | Spec changes, challengers, nested validation, ablations |
| Production | Scheduled ingest/forecast, monitoring, release archives, `publish-live` |
| Editorial / presentation | UI copy, rounding, scenario labels (not new forecasts) |
| Approval | Review current-cycle overlays, new sources, recalibrations |

## Change log
Public notes: `MODEL_CHANGELOG.md`. Immutable index: `data/manifests/releases.jsonl`.

## Manual adjustments
Manual race adjustments are exceptional. If used they must be:
1. Timestamped and documented in the run manifest
2. Accompanied by an **unadjusted** artifact (`ablation` block)
3. Reviewed against holdout evidence when possible

## Corrections
Use `midterms.ops.corrections.register_correction` to pair original + corrected
artifacts with reason, first affected as-of, statistical impact, and prevention test.

## Signing
Release manifests and forecast JSON carry **Ed25519** signature sidecars
(`generate-signing-keys`, `MIDTERMS_SIGNING_KEY` / keypair under `data/keys/`).
Set `MIDTERMS_REQUIRE_SIGNING=1` to fail closed when signatures are missing.
(Legacy HMAC notes are obsolete.)

## Red-team checklist
- Pollster flood / ENOP sublinearity
- Leave-pollster-out CRPS stability (`leave-pollster-out` CLI)
- Common national polling miss scenarios
- Heavy-tail joint shocks
- Candidate withdrawal / runoff_pending exclusion
- Leakage canary on `build_as_of`
- Source outage → last sealed artifact + stale alerts
- Fold-pure stack weights (no holdout peeking)
- Same-family structural ablations (no PyMC-to-fast substitution)
- Prior predictive, grouped PPC, and synthetic SBC diagnostics
- Explicit zero-divergence publication rule and sampler-health availability
- Candidate/race bitemporal snapshots and demographic/economic vintage checks
- Verified market contract-family semantics and overlay validation contracts
- Joint energy/variogram/seat-count scoring with sealed race ordering
- Rebuild hash check (`verify-rebuild`)

House / Electoral College remain out of scope.
