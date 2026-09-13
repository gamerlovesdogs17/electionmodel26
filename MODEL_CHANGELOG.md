# Model change log

Immutable research release notes (blueprint §11.3 / §12). Older public artifacts
should be preserved alongside newer ones under `data/artifacts/` and
`data/releases/{run_id}/`. Index: `data/manifests/releases.jsonl`.

## 2026-09-13 — senate-hierarchical-v0.9.16

2014/2016 certified archive + G10 independent rebuild:

- Official ballots include 2014 HI/OK/SC specials; curated `CERTIFIED_MARGINS` for 2014–2016; chamber `GATE_YEARS` = 2014–2024.
- `verify-rebuild --independent` re-executes `run_forecast` (rebuild_mode) and compares chamber/race probs within MCSE tolerances.
- Manifests seal a full `configuration` block; G10 prefers lite hash seal + independent report + shadow seals.

## 2026-09-13 — senate-hierarchical-v0.9.15

Public-facing live probabilities (post Milestone-0):

- CLI `publish-live` fail-closes unless G1–G11 green, evidence publication-eligible, and numerical quality ok.
- Stamps `public_release` / `publication_surface=live` on `forecast_latest.json`, syncs `web/public`, seals live shadow.
- UI shows live banner when stamped; research-only copy otherwise.
- Index: `data/manifests/public_publications.jsonl` + `publication_latest.json`.

## 2026-09-13 — senate-hierarchical-v0.9.14

Close Milestone-0 acceptance partials (pre–public live probabilities):

- Nested component LOO + OOF stack weights re-fit with **pymc** spine (G5 identity aligned).
- Forecast regenerated with stamped `numerical_quality` (G9) and sealed release hashes (G10 verify-rebuild).
- Acceptance G9 missing-block is fail (not soft partial); G10 pass requires rebuild seal + shadow seals.

## 2026-09-13 — senate-hierarchical-v0.9.13

First publishable milestone (post-P3 / Milestone-0):

- `acceptance-gates` aggregates G1–G11 into `acceptance_gates_latest.json` (+ markdown).
- G6/G7: log score, calibration slope/intercept, PIT, overconfidence stop in `metrics.py`.
- `shadow-publish --mode milestone` seals a **pymc** spine 2022 D−60 evaluation with acceptance gates copied in.
- Archive scope documented as **2018–2024 official** (2014/2016 provisional). Public live probabilities still wait.
- Monitor hard-fails publication claims when acceptance gates are red.

## 2026-09-13 — senate-hierarchical-v0.9.12

Audit P3 (prospective shadow publication):

- Write-once sealed bundles under `data/shadow/{shadow_id}/` with content hashes + optional Ed25519.
- Historical mode freezes predictions before reading certified results, then scores; live mode seals `forecast_latest`.
- CLI `shadow-publish` / `shadow-verify`; index `data/manifests/shadow_publications.jsonl`.
- Exit: at least one retained frozen cycle evaluation (`shadow-publish --mode ensure`).

## 2026-09-13 — senate-hierarchical-v0.9.11

Audit P2.3 (MCSE / convergence / simulation floor):

- `numerical_quality`: chamber/race MCSE, ArviZ R-hat/ESS on `mu_final`, deterministic replay probe.
- Demo defaults raised (800 draws × 2 chains); sim draws padded to ≥2500; publishable gate enforces thresholds.
- Forecast stamps `numerical_quality`; monitor G9 check; CLI `numerical-check`.

## 2026-09-13 — senate-hierarchical-v0.9.10

Audit P2.2 (genuine predictive ensemble weights):

- `fit-stack-weights` builds nonnegative simplex weights from the P2.1 OOF CRPS matrix.
- G8-disabled + structural ablation variants excluded; fingerprint + reproduction check.
- Production forecast loads `stack_weights_oof.json` with **no** fast→pymc remapping; unscored spines earn no inherited mass.

## 2026-09-13 — senate-hierarchical-v0.9.9

Audit P2.1 (nested leave-one-cycle-out for every component):

- `nested-component-loo` freezes predictions for hierarchical + challengers + baselines **before** reading certified results.
- Honest component identity (no fast→pymc remapping); failures recorded explicitly.
- G8 keep/disable recommendations + OOF CRPS matrix → `nested_component_loo.json` (feeds P2.2).

## 2026-09-13 — senate-hierarchical-v0.9.8

Audit P1.3 (terminal + similarity covariance):

- Production terminal is national + race (in-model) + demographic-similarity (post-draw).
- Shared scales in `midterms.model.terminal`; wired into `fit_pymc`, `fit_pymc_dynamic`, fast, state-space.
- Nested grid CLI `calibrate-covariance` → `covariance_calibration.json`; gate passed (race sd 1.4→1.8; chamber objective ↓, coverage flat).

## 2026-09-13 — senate-hierarchical-v0.9.7

Audit P1.2 (hierarchical poll + fundamentals effects):

- Mode/population: hierarchical `Normal(prior, σ)` in `fit_pymc` / `fit_pymc_dynamic` (no pre-subtracted fixed offsets). Priors in `midterms.model.effects`.
- Fundamentals: nested leave-one-cycle ridge toward `PRIOR_COEF`; `prior_lean` stays fixed at 1.0 with documented rationale.
- CLI `estimate-fundamentals` → `coefficient_stability.json`; remaining fixed effects registry.
- Fast/state-space still use prior-mean mode/pop (labeled); production spine estimates.

## 2026-09-13 — senate-hierarchical-v0.9.6

Audit P1.1 (dynamic latent path):

- `fit_pymc_dynamic`: weekly national + race random walks; innovation ∝ √days; polls observe θ[r,t]; distinct terminal ED layer.
- Static `fit_pymc` labeled `latent_path=static_election_day` (honest Finding-3 description).
- CLI `compare-static-dynamic` + `--method pymc_dynamic` / replay `--hierarchical-method pymc_dynamic`.

## 2026-09-13 — senate-hierarchical-v0.9.5

Audit P0.4–P0.5 (evidence eligibility + archive labeling):

- Explicit evidence tiers (`official`…`synthetic`/`untraceable`); publishable runs reject blocked tiers.
- Forecast stamps `run_class` / `publishable` / `evidence_eligibility`; `--require-publishable` hard-fails.
- Monitor checks claim consistency; UI shows unavoidable non-publication banner.
- Docs/UI: pre-P0 cycle_replay is not a validated backtest.

## 2026-09-13 — senate-hierarchical-v0.9.4

Audit P0.3 (historical polls):

- Multi-source FTE fetch (sealed local → Wayback polls-page → GitHub → Datasette).
- Normalize keeps dem/rep candidate identity + matchup_id; drops hypotheticals by default.
- Maps polls onto official race_ids (Class III regulars + OK special); wrong-class dropped.
- `poll-coverage` gate: contest + nominee coverage at 90/60/30/7-day leads; wired into monitor/report/replay.

## 2026-09-13 — senate-hierarchical-v0.9.3

Audit P0.1–P0.2 (historical truth):

- Official Senate class lists + per-cycle contested contests (incl. 2022 OK special).
- Certified margins expanded to full contested ballots; race_id for specials.
- Held caucus counts derived so certified winners reproduce post-election seats/control.
- `reconcile-chamber` CLI + hard gate artifact (default 2018–2024); warehouse prefers `races_official`.
- 2020 control scored at cycle completion (post–GA runoffs + Dem VP), not election-night Pence VP.
- Monitor / validation-report include chamber reconcile; pre-P0 cycle_replay marked non-comparable.

## 2026-09-13 — senate-hierarchical-v0.9.2

Closed remaining blueprint Partial gaps (Senate-only):

- **OOS PyMC:** `replay-cycle` defaults to hierarchical_method=`pymc` (fast remains CI/challenger).
- **Morris §7.2 in core:** PyMC + fast use contracting `future_movement_sd` + fixed `terminal_error_sd` (national+race future path).
- **ALFRED multi-vintage:** fixture store keeps preliminary + post-election revisions; as-of never leaks revisions.
- **FEC amendment chain:** latest coverage_end by as-of; `amendment_chain` lineage on shares.

## 2026-09-13 — senate-hierarchical-v0.9.1

Blueprint alignment pass (Senate-only):

- **Fold-pure stacking:** production weights from OOS cycle CRPS; LOO weights per holdout; hierarchical mass labeled `pymc` (`stack_provenance`).
- **Markets:** soft CONTROLS pull only by default; hard chamber calibration opt-in (`--control-calibrate`) — blueprint §9.4.
- **Peer gate:** integrity/race MAE hard; control gap vs markets soft (§2 compare-only); pass if any peer panel OK when panels disagree.
- **Leave-pollster-out** CLI + validation report block; named `error_budget` in fit diagnostics.
- **Kalshi specials:** `SENATEFLS-26` / `SENATEOHS-26` event tickers for FL/OH specials.
- **Ops:** Ed25519 in GOVERNANCE; `verify-rebuild` hash gate; OneDrive-safe parquet writes; UI null-safe chips/bars.

## 2026-09-13 — senate-hierarchical-v0.9

Blueprint gap-close (Senate-complete; no Cook license; House/EC still out):

- **Evidence:** FTE historical poll seal + per-cycle manifests; replay fails closed on synthetic unless `--allow-synthetic`.
- **Spine:** PyMC hierarchical-t is production default; `fast` is CI/degraded-only with explicit labeling.
- **State-space:** National path + calibrated future/terminal scales; mode/pop/house offsets aligned with hierarchical measurement.
- **Stacking:** OOS challenger pool (state-space, poll-only, ridge, baselines); removed hand-tuned stack weight patches.
- **Ratings:** Wikipedia multi-rater (`wikipedia:multi-rater`) — Cook/IE/Sabato core + WH/RCP/DDHQ/Fox/Econ extended; Solid…Tilt ladder.
- **Validation:** Richer `validation-report` (reliability, interval score, stack provenance); monitor alerts for method degradation / stale weights / non-FTE history.
- Layer failures surface in `artifact.warnings`.

## 2026-09-13 — senate-hierarchical-v0.8.1

Production-path follow-through on remaining exclusions:

- Licensed ratings **adapter** (`ingest-licensed-ratings` / `COOK_RATINGS_CSV`) — no vendor data in repo.
- **Wikipedia Predictions table** expert ratings (`fetch-ratings` default): Cook / Inside Elections / Sabato consensus with provenance.
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
