# Model card — Senate hierarchical v0.5

## Target
- **Office:** U.S. Senate (Class II 2026 + OH/FL specials + historical cycles for validation)
- **Estimands:** race two-party margins; joint Dem **caucus** seat totals; chamber **control**
- **Control rule:** Dem control requires ≥51 seats. Exactly 50 Dem seats → **Republican control** (VP tiebreak, 2025–2029)
- **Independents:** Displayed as Ind when there is no Dem nominee; wins and held Ind seats still count toward Dem caucus control

## Update cadence
Research / on-demand via `python -m midterms.cli forecast`. Not a live production publishing system.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls | VoteHub API (CC BY 4.0) + synthetic fixtures | As-of via `available_at` |
| Pollster quality | VoteHub scorecards + FTE CSV fill-in | Hierarchical house / extra-SD priors |
| Economics | ALFRED/FRED when `FRED_API_KEY` set; else fixture vintages | YoY real disposable income |
| Finance | OpenFEC candidate totals | Dem receipt share; DEMO_KEY fallback |
| Expert ratings | Curated dated research snapshot / CSV override | Ablatable overlay **input** only — display rating is model-derived |
| Markets | Kalshi public API (`SENATE{ST}-26-*`, `CONTROLS-2026-*`) | Liquidity-scaled overlay |
| Peers | Curated DDHQ + live Kalshi (+ Economist when filled) | Comparison only — **not** averaged into the ensemble |
| Results | Synthetic certified fixtures (+ MEDSL when fetched) | Holdout scoring only |

## Core model
- Hierarchical latent opinion (PyMC NUTS or fast Student-t approximation)
- Fundamentals prior: lean, generic ballot, incumbency, fundraising logit, approval, midterm shift, vintage income growth
- ENOP / pollster caps / study clustering
- Future movement vs terminal industry bias
- National / region / local Student-t shocks → joint chamber simulation
- Predictive stacking of hierarchical + baselines (CRPS weights from cycle replay)
- Display ratings = `rating_from_probability(p_dem)` so map Probability / Ratings / table match

## Optional overlays (ablatable)
- Expert ratings → soft shrink race means (`--no-ratings` to disable)
- Kalshi race markets → liquidity-weighted shrink (`--no-markets` to disable)
- Artifact includes `ablation.unadjusted` vs `ablation.adjusted` and cycle-replay overlay ablation deltas

## Validation
- Leakage canary on `build_as_of`
- Complete-cycle replay: `python -m midterms.cli replay-cycle --all`
- Metrics: race CRPS / log / Brier / MAE; chamber seat CRPS + control Brier; overlay ablation

## Peer comparison (not an ensemble)
Public peers (Kalshi, DDHQ, Economist/FiftyPlusOne when available) appear in `peer_comparison` and the UI panel for context. Named systems are design evidence (blueprint §2), not authorities to average.

## Contested 2026 universe
- 33 Class II seats + **OH and FL specials** (35 contested, 65 held)
- Held Independents (e.g. ME King, VT Sanders) coded `held_by=I` and counted in Dem caucus

## Known limitations
- Small number of modern Senate cycles; synthetic historical polls for offline CI
- Expert ratings are a curated research layer until licensed feeds are wired
- Market overlays inherit Kalshi liquidity / coverage gaps
- Peer snapshots can lag live publisher pages
- House / Electoral College out of scope
