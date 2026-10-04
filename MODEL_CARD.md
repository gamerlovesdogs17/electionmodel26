# Model card — Senate hierarchical v0.9.23 development boundary

> **Validation status (2026-10-03):** v0.9.23 is an unvalidated current-cycle
> evidence repair. Current candidate-source and matchup coverage fail closed;
> no v0.9.23 release identity, evidence seal, or forecast exists. v0.9.22 remains
> the last completed sealed research rebuild against bundle
> `eb-3e91ca3a90628d69`, with release identity
> `truth_v1_v0.9.22_eb-3e91ca3a90628d69`. Both surfaces remain `research_only`
> with `PUBLIC_LIVE_ENABLED=False`.

## Target
- **Office:** U.S. Senate only (Class II 2026 + OH/FL specials + historical cycles)
- **Estimands:** two-party margins for supported D-v-R contests; joint seats and
  chamber control are withheld when an active contest lacks a supported target
- **Auxiliary:** multiway shares + turnout foils (do not drive seat math)
- **Nonstandard candidates:** candidate, ballot party, modeled side, and caucus
  affiliation are distinct fields. Warehouse rows now carry current ticket
  identities. The four modeled Independents and held Independent seats retain
  `I` ballot/held labels and use a user-declared Democratic-caucus accounting
  assumption. Ballot party never changes because of that caucus assumption.
  Current I-v-R and Alaska RCV/multi-candidate contests are blocked pending
  separately validated targets, and missing identity metadata blocks accounting.

## Architecture (five layers — do not conflate)
1. **Reference / generative spine** — PyMC hierarchical Student-t. Default CLI method `pymc` is the **static** Election-Day latent (`latent_path=static_election_day`). Challenger `pymc_dynamic` is a **weekly random-walk** path with Morris-calibrated future innovations and residual ED terminal only (`latent_path=weekly_random_walk_morris_calibrated`).
2. **Stack candidates** — separately identified frozen OOF predictors: static `pymc`, `pymc_dynamic`, `state_space`, `ridge_fundamentals`, and eligible baselines. Structural challengers are diagnostics and are excluded from the production simplex. The validated structure is the all-off `pymc` reference; sponsor, questionnaire, and study effects remain disabled, with heuristic study downweighting enabled.
3. **Distributional mixture code** — nonnegative weights from empirical predictive-mixture CRPS over frozen draws. Mean-score softmax is diagnostic only. The validated v0.9.22 stack assigns approximately 21.9% to `pymc` and 78.1% to `state_space`; other candidates receive zero production mass.
4. **Overlays** — expert ratings + Kalshi race/control soft pulls are optional. Publication use requires an exact-weight timestamp-pure nested-OOS validation contract; otherwise the publication fit runs core-only and records the layers as compare-only.
5. **Final correlated chamber simulator** — joint margin draws → seats → control. Simulation count is separate from posterior sample count (`n_joint_sims` vs `n_posterior_samples`). Independent Bernoulli foil is diagnostic only.

## Update cadence
`forecast` / `refresh`. Releases: `data/manifests/releases.jsonl` + signed `data/releases/{run_id}/`.
Shadow publications (audit P3): `data/manifests/shadow_publications.jsonl` + write-once `data/shadow/{shadow_id}/`.
Acceptance gates (Milestone-0): `acceptance-gates` → `data/artifacts/acceptance_gates_latest.json` (G1–G11).
Run coherence: `run_coherence_latest.json` ties forecast ↔ eligibility ↔ rebuild fingerprints.
Public live: `publish-live` → stamps `public_release` on forecast + `publication_latest.json` (requires green gates).
Governance: `GOVERNANCE.md`. Validation: `validation-report`, `leave-pollster-out`, `verify-rebuild`, `replay-cycle`, `shadow-verify`.

## v0.9.22 production research freeze

`senate-hierarchical-v0.9.22` is frozen as the completed validated production
research specification identified by `validated_model_spec_latest.json` and
release identity `truth_v1_v0.9.22_eb-3e91ca3a90628d69`. Changes to model
formulas, poll structure, stack membership or weights, calibration, uncertainty,
turnout propagation, institutional transitions, fundamentals, historical
training scope, source semantics, or probability generation require a new
research version and complete-cycle OOS validation. Compatible ingestion,
operations, reporting, and documentation repairs may retain v0.9.22 only when
they leave the validated statistical specification and forecast probabilities
unchanged. Turnout propagation and fuller runoff/transition modeling remain
future-version research candidates.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls (live 2026) | VoteHub CC BY | Exact 3 Oct raw JSON receipt, parser and Parquet lineage are bound; candidate IDs/names and matchup IDs survive canonical ingest; fixtures are non-publication |
| Polls (historical) | FiveThirtyEight / ABC News (CC BY; Wayback/sealed) | Candidate identity + official race_id map; `--allow-synthetic` CI only |
| Pollster quality | VoteHub plus sealed FiveThirtyEight snapshots | Exact source-commit dates are sealed for the 2018/2020/2023 content vintages; none was public by the formal 2018 cutoffs, and the mismatched 2021 file is excluded |
| Economics | Sealed ALFRED observations-by-vintage archive | All formal 60/30 cutoffs plus the current cutoff are source-ready; FRED latest, World Bank and fixtures cannot clear the gate |
| Approval | Vendored compiled individual-poll archive plus VoteHub current polls | Formal historical cutoffs use the sealed point-in-time 30-day aggregate and pass source readiness |
| Finance | Official FEC candidate/committee links plus Form 3 report summaries | Formal cutoffs use receipt-date availability and as-of amendment resolution; source readiness is complete |
| Candidate state | Official race universe, bitemporal candidate events, and archived official pages | Historical replay retains the conditional identity contract. Current 2026 labels require traceable identity for matchup selection; the curated ticket registry is diagnostic-only and cannot clear publication eligibility. Current source completion remains pending |
| Expert ratings | **Wikipedia multi-rater** (Cook / IE / Sabato core; WH/RCP/DDHQ/Fox/Econ extended) | Ablatable; CC BY-SA page; Solid/Likely/Lean/Tilt/Tossup |
| Licensed ratings | Optional local CSV via `COOK_RATINGS_CSV` | Dormant adapter only — no vendor license required |
| Markets | Kalshi | Candidate mapping plus verified candidate-win/exclusive/exhaustive contract-family semantics required before normalization |
| Demography | Official ACS 5-year and Census urban/rural files | Release-dated 2016–2024 vintages and the 2010/2020 urban availability policy pass formal-cutoff source readiness |

## Bayesian and sampler diagnostics

- Prior-predictive summaries report margin extremes and ranges for national,
  house, measurement, movement and terminal terms.
- Posterior-predictive utilities report standardized residuals, grouped
  residuals and interval coverage by pollster/mode/population/study.
- A deterministic synthetic SBC harness is available; its small conjugate
  example tests plumbing and does not establish real-model adequacy.
- Publication numerical quality now requires explicit divergence diagnostics
  with zero divergent transitions. Tree-depth hits and BFMI are checked when
  available; missing divergence status cannot pass publication.

## Optional poll structures

Sponsor, questionnaire-family and shared-study effects use hierarchical
shrinkage and stable identifiers in both static and dynamic PyMC. Missing
metadata receives a unique reserved row identity and cannot create a shared
latent group. They are off
by default. When an explicit study effect is enabled, heuristic study
downweighting defaults off to avoid counting the same dependence twice.
The predeclared selection set contains the all-off base plus one addition at a
time. For each held-out cycle, the other cycles alone select a structure. A
challenger must beat the base in a strict majority and have lower pooled
empirical CRPS; ties and incomplete evidence select the base. Multi-term
interaction structures are deferred for v0.9.22.

Selection and production fitting use two distinct expensive passes. The first
pass evaluates the all-off reference and one-term challengers and writes a
candidate model spec. The second pass refits canonical static and dynamic PyMC
with the selected configuration, excludes structural labels from stacking, and
is the only OOF artifact permitted to train the production stack. A final
`validated_model_spec_latest.json` binds the evidence bundle, source-readiness
hash, selected structure, canonical OOF, stack, calibration artifact, cycles,
lead cutoffs, and code commit. Publication fitting loads that spec and passes
the selected structure to every static/dynamic fit; defaulting silently to the
all-off configuration is forbidden.

## Temporal and institutional limits

Candidate/race timeline schemas distinguish effective, available and retrieved
times. Candidate identity is a conditional leakage-control domain: exact future
nominees are never required before nomination, identity-sensitive transitions
remain fail-closed, and structurally nonbinary race/cutoffs carry explicit score
exclusions. The draw-level institutional interface supports thresholds,
advancement and linked runoff draws, but no empirically validated runoff
transition is enabled. Turnout remains auxiliary and does not drive seat math.
| Results | Certified archive + MEDSL 2016 + fixtures | Prefer certified |
| Structural state prior | Federal Election Commission official presidential files, 2012–2024 | Statewide two-party margin minus national two-party margin; latest 2/3 + previous 1/3 by source availability; 50-state sealed side table |
| Race universe | Wikipedia Class II / 2026 schedule + constitutional roster | Chamber reconcile gate **2014–2024** |

## Evidence eligibility (P0.4)
- Tiers: `official` › `first_party` › `aggregator` › `curated` › `imputed` › `synthetic` › `untraceable`
- Publishable runs reject synthetic/imputed/untraceable; `evidence-eligibility` / `--require-publishable`
- Repository-backed race snapshots now attach a verified derived-prior source,
  row provenance hash, and prior snapshot fingerprint. Fixture priors fail
  publication eligibility.
- Market eligibility requires the current candidate-aware parser, complete
  fetch/audit coverage, unchanged store bytes, and a market vintage no later
  than the forecast `as_of`.
- Forecast writes `evidence_eligibility_latest.json` with matching `forecast_run_id` + `evidence_fingerprint`
- `evidence-snapshot-fingerprint-v2` hashes selected forecasting rows and
  component source identities using canonical serialization.
- The effective production-domain contract marks sources as required core,
  conditionally required, compare-only, disabled, or quarantine-only.
- `source-readiness` audits current evidence plus 2018/2020/2022/2024 at formal
  60/30-day cutoffs without fitting. `prepare-evidence --mode seal` writes a
  canonical bundle binding every required source and snapshot. Publishable OOF,
  stack and forecast stages must consume the exact bundle ID.
- Candidate-state classifications, excluded matchup polls, exact timeline
  events, and binary-score exclusions are part of the content-addressed
  evidence snapshot. A classification change changes snapshot lineage.
- Development may continue with `run_class=non_publication` (UI banner mandatory)

## Core model
- **Reference spine (default):** static PyMC (`method=pymc`)
- **Dynamic challenger:** `method=pymc_dynamic` — weekly national+race RW; future process noise calibrated to Morris `4.5√(days/120)` budget; terminal = residual ED error only (avoids double-counting path + full static terminal)
- **OOS replay code:** formal 60/30-day nested LOO freezes static and dynamic
  implementations under separate identifiers and retains predictive draws for
  each lead. A broader 90/60/30/14/7-day grid remains diagnostic only.
- **OOF inference floor:** 800 tune + 800 retained draws per chain, two chains,
  with R-hat/ESS checks per PyMC candidate. Publication inference remains
  4000 tune + 2000 retained draws per chain, four chains, with a NUTS target
  acceptance of 0.99. This longer adaptation policy was adopted after the
  2026-09-27 production attempt reported 14 divergent transitions at the prior
  2000-tune / 0.90 target; the zero-divergence publication gate remains intact.
- **Non-production:** `fast` hierarchical-t approximation (CI / `--allow-fast-fallback` only)
- Generic ballot: VoteHub **21-day trailing weighted average** (Winsorized headline D−R)
- **Morris §7.2:** current opinion ≠ future movement ≠ Election-Day terminal polling error
- The validated v0.9.22 formal OOF uses four historical cycles at 60/30-day
  leads. Cycle-cross-fitted production-stack reliability contains 260 cases;
  the selected stack and calibration artifacts are bound by the validated spec.
- Fundamentals prior; ENOP / caps / study clustering; hierarchical mode/pop; named **error_budget**
- Ratings / markets overlays with ablation
- Fold-pure stack weights (`stack_provenance`); never manually assign positive weight
- Joint sims: CI ≥2.5k; routine ≥10k; production target **50k** correlated draws (`--n-joint-sims`)

## Validation / ops
- Complete-cycle replay; nested LOO; lead-time grid; component ablations
- Generic grouped model registry, point-in-time prior provenance, decomposition
  hook, and artifact lineage checks are connected. The prior source, current
  snapshots, formal OOF, stack, forecast, independent rebuild, and acceptance
  artifacts are mutually bound for the v0.9.22 release identity.
- Dynamic OOS grid artifact: `dynamic_core_oos_grid.json` (freeze-before-truth)
- Monitor alerts; Ed25519 signing; environment lock; `verify-rebuild`; correction registry
- **Pre-P0 cycle_replay artifacts are non-comparable** — not validated backtests (`VALIDATION_ARCHIVE_NOTICE.md`)

## Limitations
- **Public live probabilities are locked** (`PUBLIC_LIVE_ENABLED=False`). Surface is `research_only` until a fresh independent clearance.
- Independent modeled candidates count toward the Democratic caucus **by a
  declared modeling assumption**, not by ballot party or a verified individual
  caucus pledge. The ballot party and display identity remain Independent.
- Static PyMC is the reference spine. Current historical OOF stack weights
  give zero production mass to `pymc_dynamic`; this is an OOF selection result,
  not a manual adjustment.
- Wikipedia `certified_vote_counts.json` is **quarantined** (parser-development only).
- Canonical outcomes use **truth_v1** (`official_senate_ledger.json` + independent expectations).
- Margin scoring uses `score_eligible` / `margin_definition`.
- Runoff contests use decisive-stage `election_day` with `available_at` day-after bound.
- G6/G7 require multi-cycle nested evidence before “calibrated” language.
- Private signing keys must never appear in handoff ZIPs.
- The sealed v0.9.22 evidence bundle clears the source gate. This establishes
  source availability and lineage only; it does not establish model performance.
- Candidate declarations still do not establish nomination or ballot
  qualification. They are not used to satisfy identity-sensitive cutoffs.
- Structurally unresolved pre-primary races use side-only inputs and exclude
  candidate-named polls. California top-two pairings use the sealed official
  timeline. Nonbinary/ranked-choice or pre-runoff pairings are retained in
  evidence but excluded from binary proper scoring with machine-readable reasons.
- `pymc_dynamic` must earn OOF mass before displacing static pymc as reference or mixture member.
- Poll coverage for 2014/2016 is not production-gated.
- Peer snapshots are compare-only and never averaged into the ensemble.
- House, governors, and Electoral College are out of scope.

## Known limitations
See **Limitations** above (retained heading for acceptance G11).
