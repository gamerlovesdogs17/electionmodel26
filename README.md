# Midterms Senate Probability Model

Internal research system for **U.S. Senate** seat-by-seat predictive probabilities and **chamber-wide** seat totals / majority from **joint** correlated draws (never independent Bernoulli from marginals alone).

Design authority: Project blueprint (`docs/us-election-forecasting-model-blueprint.pdf` in the Project Context store). Locked decisions live in `project-context.md`.

## What this repo contains

| Layer | Location |
| --- | --- |
| Evidence warehouse (bitemporal polls/results, `build_as_of`, manifests) | `midterms/evidence/` |
| Synthetic offline fixtures + provenance | `data/` (generated) |
| Three baselines + holdout replay | `midterms/baselines/`, `midterms replay-baselines` |
| Hierarchical latent-opinion model (PyMC + fast hierarchical-t path) | `midterms/model/` |
| Joint chamber simulator | `midterms/simulate/` |
| Forecast artifacts | `data/artifacts/forecast_latest.json` |
| Internal research UI | `web/` |
| Forecast JSON API | `midterms/api/` (`midterms serve-api`) |

## Quick start

```bash
# Python 3.11+ (PyMC NUTS needs python3-dev / a C++ compiler on Linux)
# On Windows: if App Control blocks NumPy DLLs under OneDrive, create the venv
# outside OneDrive, e.g. %LOCALAPPDATA%\electionmodel-venv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Build fixtures, pull VoteHub polls + ratings, generate forecast
python -m midterms.cli build-fixtures
python -m midterms.cli fetch-external
python -m midterms.cli ingest-polls
python -m midterms.cli replay-baselines --year 2022
python -m midterms.cli replay-cycle --all
python -m midterms.cli forecast --method fast --as-of 2026-09-01

# Optional: full PyMC NUTS fit (slower)
python -m midterms.cli forecast --method pymc --draws 400 --tune 400 --chains 2

# API for the UI (port 8787)
python -m midterms.cli serve-api --port 8787
```

```bash
# Research UI (uncommon port)
cd web
npm install
npm run dev -- --port 4317
```

Open [http://127.0.0.1:4317](http://127.0.0.1:4317).

## Public site (GitHub Pages)

The research UI is published at [https://gamerlovesdogs17.github.io/electionmodel26/](https://gamerlovesdogs17.github.io/electionmodel26/) via `.github/workflows/deploy-pages.yml` (static Next.js export of `web/`).

In the repo **Settings → Pages**, set **Source** to **GitHub Actions** (not “Deploy from a branch”). Branch-root deploys only render this README.

## Regenerate forecasts

```bash
python -m midterms.cli forecast \
  --election-id senate-2026 \
  --as-of 2026-09-01 \
  --method fast \
  --seed 20260901 \
  --generic-ballot -1.0
```

Artifacts land in `data/artifacts/` with a run manifest under `data/manifests/`. The UI reads `forecast_latest.json` via the API (or a vendored copy under `web/public/`).

## Data / licensing

- Default path uses **synthetic research fixtures** shaped like historical Senate cycles (2014–2024 + 2026 demo) so CI and offline dev never depend on restricted poll redistribution.
- **Live polls (preferred):** VoteHub Polling API (`us-senator`, `generic-ballot`) — [CC BY 4.0](https://votehub.com/polls/api/). Attribution: Polling data from [VoteHub](https://votehub.com).
- **Pollster ratings:** VoteHub [Pollster Scorecards](https://votehub.com/polls/pollster-scorecards/) (grades, house effect, error metrics) plus FiveThirtyEight pollster-ratings CSV as fill-in for unrated firms.
- Ingest: `python -m midterms.cli fetch-external` then `python -m midterms.cli ingest-polls`.
- Provenance + sha256 hashes: `data/manifests/`.
- Best-effort public fetch also tries MEDSL context CSV when reachable.

## Tests

```bash
pytest -q
```

Includes a **leakage canary**: future-dated polls must not survive `build_as_of`.
VoteHub normalize/merge tests require `data/raw/external/votehub_*.json` (created by `fetch-external`).

## Out of scope

House, Electoral College, public auth, production cloud deploy.

## Model v0.7 upgrades

- Certified results archive (MEDSL 2016 + curated 2018–2024) preferred over synthetic results.
- Bitemporal poll as-of (`valid_from`/`valid_to`); continuous similarity joint shocks.
- Institutional race fields (runoffs / vacancies / ballot status); OH/FL marked `appointment`.
- Ops: `monitor-check`, `refresh`, `releases.jsonl` + hashed `data/releases/`.

## Model v0.6 upgrades

- Nominee ticket refresh (ME Jackson, MI El-Sayed, NE Osborn Ind, FL Nixon, CO Hickenlooper, …).
- UI: ≤50 seats = GOP in histogram; Democrats/Republicans labels; clamped map tooltips; VoteHub peer column.
- Nested fundamentals ablation CLI (`ablate-fundamentals`) + `MODEL_CHANGELOG.md`.

## Model v0.5 upgrades

- **OH + FL specials** in the contested universe (35 races / 65 held).
- **Model-derived display ratings** aligned with Probability map bands; expert ratings are overlay inputs only.
- **Independent caucus display** (`dem_party=I` / held `I`) with Dem seat math unchanged.
- **Peer comparison** panel (Kalshi / DDHQ / Economist snapshots) — not averaged into the ensemble.
- Cycle replay adds **chamber seat CRPS / control Brier** and overlay ablation deltas.

## Model v0.4 upgrades

- **Kalshi markets** as a real overlay layer (`fetch-markets`): race `SENATE{ST}-26-*` + `CONTROLS-2026-*`, liquidity-scaled.
- **Expert ratings warehouse** (`fetch-ratings`): timestamped curated snapshot / CSV override; map Ratings view uses these.
- Overlays **on by default** when data exists; disable with `--no-ratings` / `--no-markets`.
- Forecast artifact includes **`ablation`** (unadjusted vs adjusted chamber).
- **`MODEL_CARD.md`** documents estimands, sources, limitations.

## Model v0.3 upgrades

- **VP tiebreak:** 50–50 Senate → Republican control for the chamber-control estimand.
- **ALFRED/fixture economic vintages** (`fetch-economics`) → real-income YoY in fundamentals.
- **OpenFEC / fixture fundraising shares** (`fetch-finance`) → Dem receipt share prior.
- **Optional ratings/markets overlays** (now production layers in v0.4; flags were `--with-ratings` / `--with-markets`).
- **Senate map** at the top of the research UI: Probability / Ratings / Margin views, flip hatching, hover tooltips.

## Model v0.2 upgrades

- **ENOP / pollster caps / study clustering** — influence weights so prolific firms and repeated releases add sublinear information (`midterms/model/poll_weights.py`).
- **Richer fundamentals** — fundraising share (logit), presidential approval, midterm out-party shift (`midterms/model/fundamentals.py`).
- **Mode / population offsets** in the measurement path.
- **Complete-cycle replay** of baselines + hierarchical model with CRPS/Brier and learned stack weights:
  `python -m midterms.cli replay-cycle --all`
- **Predictive stacking** — mixture of hierarchical core + baselines using holdout CRPS weights (`--no-ensemble` to disable).
