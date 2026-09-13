# Model card — Senate hierarchical v0.6

## Target
- **Office:** U.S. Senate (Class II 2026 + OH/FL specials + historical cycles for validation)
- **Estimands:** race two-party margins; joint Democratic seat totals; chamber **control**
- **Control rule:** Dem control requires ≥51 seats (≤50 → Republican control, including 50–50)
- **Independents:** Shown as Ind when there is no Dem nominee; still count toward Democratic seats

## Update cadence
Research / on-demand via `python -m midterms.cli forecast`. See `MODEL_CHANGELOG.md`.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls | VoteHub API (CC BY 4.0) + synthetic fixtures | As-of via `available_at` |
| Pollster quality | VoteHub scorecards + FTE CSV fill-in | Hierarchical house / extra-SD priors |
| Economics | ALFRED/FRED when keyed; else fixtures | YoY real disposable income |
| Finance | OpenFEC candidate totals | Dem receipt share |
| Expert ratings | Curated dated snapshot / CSV | Ablatable overlay **input** only |
| Markets | Kalshi | Liquidity-scaled overlay |
| Peers | Kalshi / DDHQ / VoteHub (comparison only) | **Not** averaged into the ensemble |
| Results | Synthetic fixtures (+ MEDSL when fetched) | Holdout scoring |

## Core model
- Hierarchical latent opinion (PyMC NUTS or fast Student-t)
- Fundamentals prior with nested drop-one ablation (`ablate-fundamentals`)
- ENOP / pollster caps / study clustering
- Future movement vs terminal industry bias
- National / region / local Student-t shocks → joint chamber simulation
- CRPS stacking of hierarchical + baselines
- Display ratings = `rating_from_probability(p_dem)`

## Validation
- Leakage canary on `build_as_of`
- Complete-cycle replay: race CRPS/Brier + chamber seat CRPS / control Brier + overlay ablation
- Nested fundamentals ablation across held-out cycles
- **Limit:** historical CI still relies heavily on synthetic polls — real redistributable archives remain the integrity bottleneck

## Still deferred (next iter)
- Licensed expert feeds; continuous similarity covariance; runoffs/vacancies automation
- Full production ops (scheduled ingest, monitoring alerts, signed release archive)
- House / Electoral College

## Contested 2026 universe
35 contested (33 Class II + OH/FL specials) + 65 held; held Ind (ME/VT) in Dem seat math.
