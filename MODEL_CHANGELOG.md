# Model change log

Immutable research release notes (blueprint §11.3 / §12). Older public artifacts
should be preserved alongside newer ones under `data/artifacts/` and
`data/releases/{run_id}/`. Index: `data/manifests/releases.jsonl`.

## 2026-09-13 — senate-hierarchical-v0.7

- Redistributable results: MEDSL 2016 state aggregates + curated certified margins (2018–2024),
  merged over synthetic fixture results by `race_id`.
- Bitemporal as-of: poll `valid_from` / `valid_to` + latest `release_version` in `build_as_of`.
- Continuous similarity covariance on joint shocks (`midterms.model.similarity`).
- Institutional race fields: `election_phase`, `runoff_of`, `vacancy_reason`, `ballot_status`,
  `effective_election_day` (OH/FL `vacancy_reason=appointment`); withdrawn / `runoff_pending` excluded.
- Ops lite: `monitor-check`, append-only `releases.jsonl`, hashed `data/releases/`, `refresh` CLI +
  GitHub Actions refresh workflow hook.

## 2026-09-13 — senate-hierarchical-v0.6

- Ticket refresh from current nominees (ME Troy Jackson, MI Abdul El-Sayed, NE Dan Osborn Ind,
  FL Angie Nixon, CO Hickenlooper, IL Stratton/Tracy, KY Booker/Barr, etc.).
- Ind-without-Dem races: ID Achilles, MT Bodnar, NE Osborn, SD Bengs (`dem_party=I`).
- UI: histogram ≤50 = GOP control only (no separate 50–50 card); map labels “Democrats/Republicans”;
  tooltip clamped inside map; peer column VoteHub (Economist removed); race class labels removed.
- Nested fundamentals drop-one ablation CLI: `ablate-fundamentals` (+ chamber/overlay scores already in `replay-cycle`).
- ENOP NaN hardening in poll weights (prior v0.5 hotfix retained).

## 2026-09-13 — senate-hierarchical-v0.5

- OH/FL specials; model-derived display ratings; Ind held roster; peer comparison panel;
  cycle-replay chamber CRPS / control Brier / overlay ablation.

## 2026-09-12 — senate-hierarchical-v0.4

- Kalshi + expert rating production overlays with ablation; MODEL_CARD; strict JSON dumps.
