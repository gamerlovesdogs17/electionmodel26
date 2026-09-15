# Governance — Senate forecasting system

Blueprint §§11–12 roles and change control (Senate research production).

## Public live probabilities
**HOLD (fresh audit 14 Sep 2026 / v0.9.18):** `PUBLIC_LIVE_ENABLED=False`.
`publish-live` fail-closes until an independent reviewer clears the vote-count
ledger and all-domain eligibility on a fresh archive. Current surface is
`research_only` even when evidence is publication-eligible.

When re-enabled after clearance: green G1–G11 + publication-eligible evidence +
`python -m midterms.cli publish-live` stamps `public_release`, syncs UI JSON, and
seals a prospective live shadow. Do not label research-only runs as live.

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
- Rebuild hash check (`verify-rebuild`)

House / Electoral College remain out of scope.
