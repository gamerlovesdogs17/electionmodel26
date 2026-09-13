# Model change log

Immutable research release notes (blueprint §11.3 / §12). Older public artifacts
should be preserved alongside newer ones under `data/artifacts/` and
`data/releases/{run_id}/`. Index: `data/manifests/releases.jsonl`.

## 2026-09-13 — senate-hierarchical-v0.8.1

Production-path follow-through on remaining exclusions:

- Licensed ratings **adapter** (`ingest-licensed-ratings` / `COOK_RATINGS_CSV`) — no vendor data in repo.
- VoteHub CC BY dump sealing + warehouse import (`seal-votehub-dumps`); archive endpoint probed.
- Forecast API bearer auth + CORS env; Docker / Compose / Fly deploy sketches (`DEPLOY.md`).
- Ed25519 signing with committed public key root; `MIDTERMS_REQUIRE_SIGNING=1` fail-closed.

## 2026-09-13 — senate-hierarchical-v0.8

Senate-only blueprint completion pass (House/EC still excluded):

- Sealed historical poll archive + bitemporal correction versions; richer poll schema fields.
- Presidential approval vintages; FEC cash/disbursement/matched-window fields.
- Forward state-space challenger (future movement vs terminal ED error); stack includes state-space.
- Demographic features in similarity covariance; turnout/multiway auxiliary layers.
- LA/GA runoff templates + vacancy defaults; scenario sensitivity in artifact + UI.
- Lead-time grid, component ablations, nested df/era search, validation report CLI.
- HMAC signing, environment lock, domain hashes, correction registry, richer monitor alerts.
- `GOVERNANCE.md` roles / change-control.

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
