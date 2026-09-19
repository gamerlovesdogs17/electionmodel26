# Acceptance gates — senate-hierarchical-v0.9.21

Generated: 2026-09-19T19:31:18.586642+00:00
Overall: **PASS** (8 pass / 2 partial / 1 fail)

Milestone archive scope is 2014–2024 official ballots + certified margins (chamber G1/G2). Poll-coverage production gate remains 2018–2024 until FTE identity polls are sealed for 2014/2016.

| Gate | Status | Name |
| --- | --- | --- |
| G1 | pass | Race universe |
| G2 | pass | Chamber truth |
| G3 | pass | Poll coverage |
| G4 | pass | Vintage integrity |
| G5 | partial | Model identity |
| G6 | pass | Proper scores |
| G7 | partial | Reliability |
| G8 | pass | Ablation |
| G9 | pass | Numerical quality |
| G10 | fail | Reproducibility |
| G11 | pass | Transparency |

## Failures

G10

## Partials

G5, G7

## Notes

- Public-facing live probabilities remain out of scope until `publish-live`.
