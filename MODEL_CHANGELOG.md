# Model change log

Immutable research release notes (blueprint §11.3 / §12). Older public artifacts
should be preserved alongside newer ones under `data/artifacts/`.

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
