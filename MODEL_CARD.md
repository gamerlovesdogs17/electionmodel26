# Model card — Senate hierarchical v0.9.21

## Target
- **Office:** U.S. Senate only (Class II 2026 + OH/FL specials + historical cycles)
- **Estimands:** two-party margins; joint Dem seats; chamber control (≥51 Dem; ≤50 → R via VP)
- **Auxiliary:** multiway shares + turnout foils (do not drive seat math)
- **Nonstandard candidates:** candidate, ballot party, modeled side, and caucus
  affiliation are distinct fields in the new generic identity schema. Existing
  real-data consumers still require an explicit integration audit.

## Architecture (five layers — do not conflate)
1. **Reference / generative spine** — PyMC hierarchical Student-t. Default CLI method `pymc` is the **static** Election-Day latent (`latent_path=static_election_day`). Challenger `pymc_dynamic` is a **weekly random-walk** path with Morris-calibrated future innovations and residual ED terminal only (`latent_path=weekly_random_walk_morris_calibrated`).
2. **Stack candidates** — separately identified frozen OOF predictors: static `pymc`, `pymc_dynamic`, `state_space`, `ridge_fundamentals`, and eligible baselines. Structural ablations are diagnostics and are excluded from the production simplex.
3. **Distributional mixture code** — nonnegative weights from empirical predictive-mixture CRPS over frozen draws. Mean-score softmax is diagnostic only. Current `stack_weights_oof.json` predates this code and is stale; a real-data refit is pending.
4. **Overlays** — expert ratings + Kalshi race/control soft pulls (ablatable; never forced to market).
5. **Final correlated chamber simulator** — joint margin draws → seats → control. Simulation count is separate from posterior sample count (`n_joint_sims` vs `n_posterior_samples`). Independent Bernoulli foil is diagnostic only.

## Update cadence
`forecast` / `refresh`. Releases: `data/manifests/releases.jsonl` + signed `data/releases/{run_id}/`.
Shadow publications (audit P3): `data/manifests/shadow_publications.jsonl` + write-once `data/shadow/{shadow_id}/`.
Acceptance gates (Milestone-0): `acceptance-gates` → `data/artifacts/acceptance_gates_latest.json` (G1–G11).
Run coherence: `run_coherence_latest.json` ties forecast ↔ eligibility ↔ rebuild fingerprints.
Public live: `publish-live` → stamps `public_release` on forecast + `publication_latest.json` (requires green gates).
Governance: `GOVERNANCE.md`. Validation: `validation-report`, `leave-pollster-out`, `verify-rebuild`, `replay-cycle`, `shadow-verify`.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls (live 2026) | VoteHub CC BY | Required for `run_class=publication`; fixtures are non-publication |
| Polls (historical) | FiveThirtyEight / ABC News (CC BY; Wayback/sealed) | Candidate identity + official race_id map; `--allow-synthetic` CI only |
| Pollster quality | VoteHub + FTE fill-in | House ≠ reliability |
| Economics | FRED public CSV / World Bank GDPPC YoY fallback | Observation vs available_at; fixtures are leakage canaries |
| Approval | VoteHub Trump approval aggregates | As-of store; aggregator tier |
| Finance | OpenFEC API or FEC `weball` bulk | Matched-window share; bulk is first_party when API 429s |
| Expert ratings | **Wikipedia multi-rater** (Cook / IE / Sabato core; WH/RCP/DDHQ/Fox/Econ extended) | Ablatable; CC BY-SA page; Solid/Likely/Lean/Tilt/Tossup |
| Licensed ratings | Optional local CSV via `COOK_RATINGS_CSV` | Dormant adapter only — no vendor license required |
| Markets | Kalshi | Soft overlays; `SENATE{ST}S` for FL/OH specials; chamber calibration **off** by default |
| Demography | MEDSL ACS county means (state aggregate) | Similarity / covariance; aggregator tier |
| Results | Certified archive + MEDSL 2016 + fixtures | Prefer certified |
| Race universe | Wikipedia Class II / 2026 schedule + constitutional roster | Chamber reconcile gate **2014–2024** |

## Evidence eligibility (P0.4)
- Tiers: `official` › `first_party` › `aggregator` › `curated` › `imputed` › `synthetic` › `untraceable`
- Publishable runs reject synthetic/imputed/untraceable; `evidence-eligibility` / `--require-publishable`
- Forecast writes `evidence_eligibility_latest.json` with matching `forecast_run_id` + `evidence_fingerprint`
- Development may continue with `run_class=non_publication` (UI banner mandatory)

## Core model
- **Reference spine (default):** static PyMC (`method=pymc`)
- **Dynamic challenger:** `method=pymc_dynamic` — weekly national+race RW; future process noise calibrated to Morris `4.5√(days/120)` budget; terminal = residual ED error only (avoids double-counting path + full static terminal)
- **OOS replay code:** nested LOO can freeze static and dynamic implementations
  under separate identifiers and retain draws for each declared lead. No new
  real-data validation was run for this code change.
- **Non-production:** `fast` hierarchical-t approximation (CI / `--allow-fast-fallback` only)
- Generic ballot: VoteHub **21-day trailing weighted average** (Winsorized headline D−R)
- **Morris §7.2:** current opinion ≠ future movement ≠ Election-Day terminal polling error
- Previously generated OOF artifacts do not validate the current code changes.
- Fundamentals prior; ENOP / caps / study clustering; hierarchical mode/pop; named **error_budget**
- Ratings / markets overlays with ablation
- Fold-pure stack weights (`stack_provenance`); never manually assign positive weight
- Joint sims: CI ≥2.5k; routine ≥10k; production target **50k** correlated draws (`--n-joint-sims`)

## Validation / ops
- Complete-cycle replay; nested LOO; lead-time grid; component ablations
- Generic grouped model registry, point-in-time prior provenance, decomposition
  schema, and artifact lineage checks are implemented but not yet fully wired
  into a regenerated real-data chain. Current artifacts are stale.
- Dynamic OOS grid artifact: `dynamic_core_oos_grid.json` (freeze-before-truth)
- Monitor alerts; Ed25519 signing; environment lock; `verify-rebuild`; correction registry
- **Pre-P0 cycle_replay artifacts are non-comparable** — not validated backtests (`VALIDATION_ARCHIVE_NOTICE.md`)

## Limitations
- **Public live probabilities are locked** (`PUBLIC_LIVE_ENABLED=False`). Surface is `research_only` until a fresh independent clearance.
- Do **not** call PyMC the effective production predictor when its learned stack weight is ~0; the mixture (often `state_space` + `ridge_fundamentals`) is the production predictor, with PyMC as reference spine / candidate.
- Wikipedia `certified_vote_counts.json` is **quarantined** (parser-development only).
- Canonical outcomes use **truth_v1** (`official_senate_ledger.json` + independent expectations).
- Margin scoring uses `score_eligible` / `margin_definition`.
- Runoff contests use decisive-stage `election_day` with `available_at` day-after bound.
- G6/G7 require multi-cycle nested evidence before “calibrated” language.
- Private signing keys must never appear in handoff ZIPs.
- Production economics use FRED / WB; fixture series are canaries only.
- Finance prefers OpenFEC; FEC weball bulk is the rate-limit alternative (same filings).
- `pymc_dynamic` must earn OOF mass before displacing static pymc as reference or mixture member.
- Poll coverage for 2014/2016 is not production-gated.
- Peer snapshots are compare-only and never averaged into the ensemble.
- House, governors, and Electoral College are out of scope.

## Known limitations
See **Limitations** above (retained heading for acceptance G11).
