# Governance — Senate forecasting system

Blueprint §§11–12 roles and change control (Senate research production).

## Roles
| Role | Responsibility |
| --- | --- |
| Research | Spec changes, challengers, nested validation, ablations |
| Production | Scheduled ingest/forecast, monitoring, release archives |
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
Release manifests and forecast JSON carry HMAC-SHA256 sidecars
(`MIDTERMS_SIGNING_KEY` env; dev default is not for public trust).

## Red-team checklist
- Pollster flood / ENOP sublinearity
- Common national polling miss scenarios
- Heavy-tail joint shocks
- Candidate withdrawal / runoff_pending exclusion
- Leakage canary on `build_as_of`
- Source outage → last sealed artifact + stale alerts

House / Electoral College remain out of scope.
