# Governance — Senate forecasting system

Blueprint §§11–12 roles and change control (Senate research production).

## Public live probabilities
**HOLD (27 Sep 2026 pre-rebuild boundary / v0.9.22):** `PUBLIC_LIVE_ENABLED=False`.
Truth_v1 ledger/expectations are unified; Wikipedia vote scrape remains quarantined.
`publish-live` fail-closes until multi-cycle nested validation and fold-pure evidence
clear a fresh review. Thin/single-cycle calibration claims are blocked by G6/G7.
New blueprint extensions separate code capability from empirical proof. A
capability marked implemented in `BLUEPRINT_COMPLIANCE_AUDIT.json` does not
permit publication while its empirical gate is pending, stale, requires a
refresh, or requires a rebuild.
Strict research acceptance requires both the unchanged G1-G11 report and the
separate version-bound blueprint extension evaluation. Old-version empirical
artifacts remain historical records and cannot be relabeled as current.

Current surface: `research_only`. Do not label research runs as live.

## Evidence preparation boundary

Expensive validation must consume a sealed, content-addressed evidence bundle.
The preparation stage may audit and refresh only registered safe adapters, then
must pass strict source readiness before writing or committing the bundle. The
model stage checks out the exact evidence commit, verifies the bundle ID, and
must not refresh evidence while fitting. Cache hits are performance aids only;
restored artifacts still have to pass their own semantic lineage checks.

The current source audit is allowed to remain red. Missing real evidence must
be ingested or refreshed; fixtures, current registries, and degraded substitutes
cannot be relabeled to clear a historical source gate.

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
