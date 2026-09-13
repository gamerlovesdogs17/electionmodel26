# Model card — Senate hierarchical v0.7

## Target
- **Office:** U.S. Senate (Class II 2026 + OH/FL specials + historical cycles for validation)
- **Estimands:** race two-party margins; joint Democratic seat totals; chamber **control**
- **Control rule:** Dem control requires ≥51 seats (≤50 → Republican control, including 50–50)
- **Independents:** Shown as Ind when there is no Dem nominee; still count toward Democratic seats

## Update cadence
Research / on-demand via `python -m midterms.cli forecast` or `refresh`. See `MODEL_CHANGELOG.md`.
Release index: `data/manifests/releases.jsonl` (+ per-run `data/releases/{run_id}/`).

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls | VoteHub API (CC BY 4.0) + synthetic fixtures | As-of via `available_at` + bitemporal `valid_from`/`valid_to` |
| Pollster quality | VoteHub scorecards + FTE CSV fill-in | Hierarchical house / extra-SD priors |
| Economics | ALFRED/FRED when keyed; else fixtures | YoY real disposable income |
| Finance | OpenFEC candidate totals | Dem receipt share |
| Expert ratings | Curated dated snapshot / CSV | Ablatable overlay **input** only |
| Markets | Kalshi | Liquidity-scaled overlay |
| Peers | Kalshi / DDHQ / VoteHub (comparison only) | **Not** averaged into the ensemble |
| Results | Certified archive + MEDSL 2016 aggregates + fixtures | Prefer certified by `race_id` |

## Core model
- Hierarchical latent opinion (PyMC NUTS or fast Student-t)
- Fundamentals prior with nested drop-one ablation (`ablate-fundamentals`)
- ENOP / pollster caps / study clustering
- Future movement vs terminal industry bias
- National / region / local + **continuous similarity** Student-t shocks → joint chamber simulation
- CRPS stacking of hierarchical + baselines
- Display ratings = `rating_from_probability(p_dem)`
- Race schema: `election_phase`, `runoff_of`, `vacancy_reason`, `ballot_status`, `effective_election_day`

## Validation / ops
- Leakage canary + bitemporal poll window on `build_as_of`
- Complete-cycle replay + nested fundamentals ablation
- `monitor-check` health gates; `refresh` fetch→ingest→forecast→monitor chain
- **Limit:** historical poll archives still partly synthetic; certified margins cover major contested seats

## Still deferred
- Licensed expert feeds; GA runoff automation once triggered; House / Electoral College
- Cryptographic signing of release archives (hash sidecars are present)

## Contested 2026 universe
35 contested (33 Class II + OH/FL specials) + 65 held; held Ind (ME/VT) in Dem seat math.
