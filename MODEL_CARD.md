# Model card — Senate hierarchical v0.4

## Target
- **Office:** U.S. Senate (Class II 2026 + historical cycles for validation)
- **Estimands:** race two-party margins; joint Dem seat totals; chamber **control**
- **Control rule:** Dem control requires ≥51 seats. Exactly 50 Dem seats → **Republican control** (VP tiebreak, 2025–2029)

## Update cadence
Research / on-demand via `python -m midterms.cli forecast`. Not a live production publishing system.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls | VoteHub API (CC BY 4.0) + synthetic fixtures | As-of via `available_at` |
| Pollster quality | VoteHub scorecards + FTE CSV fill-in | Hierarchical house / extra-SD priors |
| Economics | ALFRED/FRED when `FRED_API_KEY` set; else fixture vintages | YoY real disposable income |
| Finance | OpenFEC candidate totals | Dem receipt share; DEMO_KEY fallback |
| Expert ratings | Curated dated research snapshot / CSV override | **Not** Cook/IE/Sabato redistribution |
| Markets | Kalshi public API (`SENATE{ST}-26-*`, `CONTROLS-2026-*`) | Liquidity-scaled overlay |
| Results | Synthetic certified fixtures (+ MEDSL when fetched) | Holdout scoring only |

## Core model
- Hierarchical latent opinion (PyMC NUTS or fast Student-t approximation)
- Fundamentals prior: lean, generic ballot, incumbency, fundraising logit, approval, midterm shift, vintage income growth
- ENOP / pollster caps / study clustering
- Future movement vs terminal industry bias
- National / region / local Student-t shocks → joint chamber simulation
- Predictive stacking of hierarchical + baselines (CRPS weights from cycle replay)

## Optional overlays (ablatable)
- Expert ratings → soft shrink race means toward rating anchors (`--no-ratings` to disable)
- Kalshi race markets → liquidity-weighted shrink (`--no-markets` to disable)
- Artifact includes `ablation.unadjusted` vs `ablation.adjusted`

## Validation
- Leakage canary on `build_as_of`
- Complete-cycle replay: `python -m midterms.cli replay-cycle --all`
- Metrics: CRPS, log score, Brier, MAE

## Known limitations
- Small number of modern Senate cycles; synthetic historical polls for offline CI
- Expert ratings are a curated research layer until licensed feeds are wired
- Market overlays inherit Kalshi liquidity / coverage gaps
- House / Electoral College out of scope
