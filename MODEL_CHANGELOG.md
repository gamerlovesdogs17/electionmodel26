# Model change log

Immutable research release notes (blueprint §11.3 / §12). Older public artifacts
should be preserved alongside newer ones under `data/artifacts/` and
`data/releases/{run_id}/`. Index: `data/manifests/releases.jsonl`.

## 2026-09-22 — blueprint capability remediation (validation pending)

- Added a machine-readable and readable blueprint compliance audit. Capability
  status is separate from empirical validation; existing model artifacts are
  stale relative to this code.
- Added reusable prior-predictive, grouped posterior-predictive and synthetic
  SBC diagnostics. No current model was fit and no real-data performance claim
  was made.
- Added NUTS divergence, tree-depth, BFMI and acceptance diagnostics. A
  publication run must report zero divergences; missing divergence information
  fails closed.
- Added optional shrinkage-safe sponsor, questionnaire and study effects shared
  by static/dynamic PyMC. They remain off pending same-family nested OOS tests.
- PyMC no-similarity and no-terminal-race ablations now retain the reference
  family and seed policy. Optional poll-term ablations are registered.
- Added bitemporal candidate timeline, demographic-vintage, economic evidence
  class, overlay-validation, market-semantic, draw-level institutional,
  turnout-interface, domain-freshness, and optional fundamentals challenger
  contracts. Missing real source history remains explicit and fail closed.
- Added deterministic joint energy/variogram and seat-count CRPS utilities with
  sealed race ordering. Historical joint scores require regeneration.
- Environment locks now include PyMC/ArviZ/PyTensor/Matplotlib, numerical backend
  text and dependency hashes. New run/shadow references are repository relative
  or content addressed where possible.
- No forecast, network refresh, historical PyMC OOF, stack refit, joint election
  simulation, publication, or GitHub Action was run.

## 2026-09-20 — declared Independent caucus accounting assumption (no model rerun)

- At the user's direction, all modeled Independent challengers in the current
  ticket registry and held Independent seats now count toward the Democratic
  caucus in chamber math. Their ballot/held party remains `I`; the accounting
  basis is explicitly marked as a user-declared model assumption, not as an
  individual candidate's verified caucus pledge.
- Independent contests remain excluded from D–R margin scoring. A future
  forecast will write the policy into its chamber artifact and run
  configuration; run coherence rejects an older current forecast without it.
  Missing or conflicting caucus metadata still fails closed.
- Only focused synthetic/accounting tests were run. No current forecast,
  market refresh, stack fit, simulation, or acceptance chain was regenerated.

## 2026-09-20 — sourced prior and validation integration (release pending)

- The verified FEC statewide vote-count store now produces sealed 50-state
  presidential-relative prior snapshots. Warehouse races use the point-in-time
  derived prior; fixture `BASE_LEANS` remains only in source/fixture tables.
  The same method applies to historical snapshots without changing truth.
- The candidate-aware Kalshi store was refreshed with an event-level mapping
  audit. Named Independent contracts take precedence over party suffixes;
  ambiguous events stay disabled. Market ingestion rejects backdated live
  timestamps and loader/eligibility checks bind parser, audit, raw and normalized
  hashes, full fetch status, and snapshot date.
- OOF freezes static and dynamic PyMC as distinct candidates, records source
  and prior lineage, and requires validation R-hat/ESS diagnostics. The formal
  stack-training protocol remains 60/30 days. A custom OOF output path keeps
  its freeze index beside the requested artifact, avoiding changes to `latest`
  artifacts during tests. Reliability is reported unadjusted; uncertainty is
  no longer widened using the same held-out outcomes being scored.
- The four-cycle formal OOF was run with 800 tune and retained draws over two
  chains. Two dynamic fits missed the ESS threshold and were refitted at
  2000/2000/4 using diagnostics alone; the final archive has no failed fits.
  Candidate screening scores now use exact empirical CRPS from frozen draws,
  with Gaussian moment scores labeled diagnostics. The true predictive-mixture
  stack was fitted and independently reproduced. Dynamic PyMC earned zero
  production mass under the predeclared screen.
- Historical ridge training excludes held-out, later, and not-yet-available
  results. A strict validation fit cannot silently revert to prior coefficients.
  The forecast code has separate static/dynamic component fits and fails when
  a positive production stack weight lacks its named predictive component.
- Candidate identity and caucus metadata are separate throughout the warehouse
  and chamber preflight. Unknown Independent caucus treatment blocks chamber
  accounting. The per-race decomposition hook is connected but has not generated
  a new current forecast artifact.
- The sourced prior, market store, formal OOF, and stack have been rebuilt.
  The current forecast, independent rebuild, coherence, and G1–G11 remain
  blocked by unknown Independent caucus metadata. Public live stays disabled.

## 2026-09-20 — official source and pipeline provenance wiring (validation pending)

- Added hash-pinned raw FEC presidential result files for four historical
  elections and a normalized statewide vote-count store. Source selection uses
  dated FEC availability metadata. At this stage it did not yet derive or
  populate state partisan priors; the integration entry above supersedes that
  status.
- Warehouse snapshots carry the verified source-set fingerprint and selected
  source years. Current ticket identities carry separate ballot-party and
  caucus fields; missing caucus metadata blocks seat accounting before fitting.
- Candidate-market audit records are persisted for enabled and disabled events;
  stale parser versions or tampered normalized/audit files cannot be silently
  reused. The chamber simulator rejects unsupported nonstandard caucus
  mappings before seat accounting.
  No market refresh was run at this earlier stage.
- Publication eligibility fails closed on fixture or unprovenanced structural
  priors. Generic artifact lineage includes optional prior and market hashes.
  Existing forecasts and validation artifacts are unchanged and stale; no
  forecast, replay, stack fit, or simulation was run.

## 2026-09-20 — generic statistical and provenance infrastructure (validation pending)

- Added empirical predictive-mixture CRPS fitting from frozen draws with
  deterministic subsampling, fingerprints, and reproduction checks. Mean-score
  softmax outputs are labeled diagnostics; stale stack artifacts fail closed.
- Added a generic model registry for distinct identifiers, per-model fit
  settings and failures, frozen draws, and grouped freeze-before-truth hooks.
- Added a versioned point-in-time relative-prior source schema and weighted
  calculation. At this stage it was not populated with a real source series or
  connected to production race inputs; the later source-ingest entry above
  records the subsequent count-only work.
- Added explicit candidate/party/modeled-side/caucus identity and a generic
  decomposition schema. Candidate-aware contract mapping now emits an audit
  record for successful and disabled events.
- Added artifact-lineage checks and synthetic tests. Current real-data artifacts
  predate these changes; no forecast, market refresh, or historical replay was
  executed for this entry. Public live remains disabled and the surface remains
  `research_only`.

## 2026-09-20 — senate-hierarchical-v0.9.21 (coherence + dynamic core + joint sims)

Phase 1 — run/artifact coherence (live stays off):

- `midterms/ops/run_coherence.py` fingerprints evidence manifests and fails closed when
  `forecast_latest` / `evidence_eligibility_latest` / rebuild disagree on run_id,
  publishable flag, or fingerprint.
- Forecast writes identity-tied eligibility (`forecast_run_id`, `snapshot_id`,
  `evidence_fingerprint`) and embeds the same on the artifact.
- Acceptance G4 requires eligibility↔forecast identity match; gates write
  `run_coherence_latest.json`.
- `PUBLIC_LIVE_ENABLED` remains `False`; surface stays `research_only`.

Phase 2 — unified dynamic hierarchical PyMC challenger:

- `fit_pymc_dynamic` recalibrated: weekly RW future innovations match Morris
  future-movement budget; ED terminal reduced to residual polling error + light
  similarity (no double-count of path + full static terminal).
- Nested LOO freezes `pymc_dynamic` as its own stack candidate.
- OOS grid script: `scripts/run_dynamic_oos_grid.py` → `dynamic_core_oos_grid.json`.
- Static `pymc` retained as reference; production mixture still OOF-learned only.

Joint simulation precision:

- Separate `n_posterior_samples` from `n_joint_sims` (correlated resample expansion).
- Defaults: demo/routine 10k; `--require-publishable` / production target 50k.
- MCSE reports control, expected seats, and key seat-count probabilities.

## 2026-09-19 — senate-hierarchical-v0.9.21 (P1 margins / provenance / validation honesty)

Follow-on to the same-day P0 stop-line:

- Separate `margin_definition` / `margin_value` / `score_eligible`; Independent winners
  (King/Sanders), same-party finals (AK 2022), and petition Independents (Osborn) are
  excluded from D−R margin fitting/scoring while remaining in seat simulation via caucus.
- Canvass overrides use distinct `source_object_hash` + `discovery_source_hash` and
  `*_transcribed` tiers; bare `fec_canvass` claims fail validation.
- Independent seat roster is fail-closed if absent or missing held/expected IDs.
- G6/G7 fail closed on null CRPS / thin single-cycle reliability; calibration claims
  require multi-cycle nested evidence and adequate bin samples.
- Consumers (`score_forecasts`, nested LOO, cycle replay chamber, fundamentals ridge)
  honor `score_eligible`.

## 2026-09-19 — senate-hierarchical-v0.9.21 (P0 stop-line / bitemporal truth)

Independent Cursor audit remediations (live stays off):

- Correct runoff `election_day` / `available_at` for LA 2014/2016, MS 2018 special,
  GA 2020 regular+special, GA 2022; `certified_at` null without archived certification.
- Official results path fail-closed (no silent synthetic “certified” fallback).
- Release-identity reseal after rebuild; G10 fails on hash mismatch; milestone live
  flag ignores superseded `public_release` blocks.
- Archive scanner test rejects bundled private signing keys.

## 2026-09-17 — senate-hierarchical-v0.9.21

Truth integration (17 Sep data-drop audit §9), live stays off:

- Quarantine Wikipedia `certified_vote_counts.json` as parser-development only.
- Single canonical **truth_v1** schema/builder (`rebuild_canonical_truth`); legacy
  `build_official_ledger.build_and_write` redirects; expectations keys unified
  (`expected_race_ids` / `post_dem_seats`).
- Decisive-stage selection, meta row-role classifier, Independent caucus mapping
  (King/Sanders); FEC canaries LA 2014 / GA 2020-special / OK+CA 2022.
- Warehouse / chamber / gates consume winner_caucus; 211 contests reconcile 2014–2024.
- Deferred: forecast retune, stack/covariance, finance/econ/approval vintage replacement.

## 2026-09-15 — senate-hierarchical-v0.9.20

Blueprint-first audit (v0.9.19 review) all-in remediation:

- **A-01:** `PUBLIC_LIVE_ENABLED=False`; supersede v0.9.19 live with research_only.
- **A-02…A-08:** Exact vote-count truth path, independent seat rosters, curated≠publication,
  OpenFEC fail → sealed-or-disable (no BASE_LEANS finance), ALFRED/lagged economics,
  G3 fail-closed, version-coherent clearance.
- **A-09…A-13:** Rolling-origin ridge, hybrid fundamentals labeling, distribution-aware
  stacking, nested covariance outer holdout, multiway contest flags.
- Live publish remains locked pending a fresh independent review of the repaired evidence chain.

## 2026-09-14 — senate-hierarchical-v0.9.19

Independent re-audit clearance for public live:

- Re-audit checklist green (`independent_reaudit_latest.json`): R-01…R-10 + G1–G11.
- Primary-source canaries confirmed (OH 2018 Brown 2,358,508 / Renacci 2,057,559; AZ 2024 Gallego 1,676,335).
- Covariance artifact restored after smoke-test overwrite; winning scales `sim_scale=1.4`, `length_scale=1.25`.
- `PUBLIC_LIVE_ENABLED=True`; publish-live still fail-closed on red gates / ineligible evidence / NQ.

## 2026-09-14 — senate-hierarchical-v0.9.18

Post-audit unblockers on the repaired truth ledger:

- FRED public CSV economics (`A229RX0` + YoY) as production series; fixtures retained only as leakage canaries.
- Forecast no longer clobbers economics with bare `write_economic_store()`; refresh prefers FRED and preserves production rows.
- Curated FEC-browse finance shares when OpenFEC 429s (no `fixture_hash`); approval manifest gains `source_url`/`tier`.
- Nested LOO emits race-level `oof_means`/`oof_truths`; stack weights use predictive mixture CRPS (R-08).
- Nested LOO + stack re-fit with **pymc** spine (G5 identity aligned); pymc OOF CRPS did not earn mixture mass vs challengers.
- Multi-cycle covariance calibration persists winning terminal scales to `terminal_defaults.json`; static/dynamic comparison re-run on the new ledger (static preferred on 2022).
- Production floors raised to 2000 draws × 4 chains for G9 ESS/R-hat.
- Live publish remains locked (`PUBLIC_LIVE_ENABLED=False`) pending independent re-audit.

## 2026-09-14 — senate-hierarchical-v0.9.17

Fresh audit (14 Sep 2026) all-in remediation:

- **R-01/R-03:** External `official_senate_ledger.json` with vote-count margins; 2018 MN/MS specials, 2020 AZ/GA specials, 2024 CA/NE unexpired terms; OH 2018 / AZ 2024 canaries.
- **R-02:** Deleted winner-solved `sync_held_counts`; held seats from `independent_chamber_expectations.json`.
- **R-04:** Eligibility enumerates finance/economics/approval/ratings/markets; fixture_hash / `*_FIXTURE` blocks publication.
- **R-05 / Stage-0:** `PUBLIC_LIVE_ENABLED=False`; publish-live fail-closed; research_only surface.
- **R-06:** Poll coverage denominator = full official ballot; nominees from ledger.
- **R-07/R-08:** Ridge fundamentals actually fits fold coefficients; predictive mixture objective added.
- **R-12:** G11 requires model-card + forecast limitations; G8 enforces disable∉stack.

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
