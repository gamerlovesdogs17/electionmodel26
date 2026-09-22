# Governance — Senate forecasting system

Blueprint §§11–12 roles and change control (Senate research production).

## Public live probabilities
**HOLD (17 Sep 2026 data-drop audit / v0.9.21):** `PUBLIC_LIVE_ENABLED=False`.
Truth_v1 ledger/expectations are unified; Wikipedia vote scrape remains quarantined.
`publish-live` fail-closes until multi-cycle nested validation and fold-pure evidence
clear a fresh review. Thin/single-cycle calibration claims are blocked by G6/G7.
New blueprint extensions separate code capability from empirical proof. A
capability marked implemented in `BLUEPRINT_COMPLIANCE_AUDIT.json` does not
permit publication while its empirical gate is pending, stale, requires a
refresh, or requires a rebuild.

Current surface: `research_only`. Do not label research runs as live.

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
