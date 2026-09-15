# Model card — Senate hierarchical v0.9.19

## Target
- **Office:** U.S. Senate only (Class II 2026 + OH/FL specials + historical cycles)
- **Estimands:** two-party margins; joint Dem seats; chamber control (≥51 Dem; ≤50 → R via VP)
- **Auxiliary:** multiway shares + turnout foils (do not drive seat math)
- **Independents:** Ind display (purple) when no Dem nominee; still Dem caucus seats

## Update cadence
`forecast` / `refresh`. Releases: `data/manifests/releases.jsonl` + signed `data/releases/{run_id}/`.
Shadow publications (audit P3): `data/manifests/shadow_publications.jsonl` + write-once `data/shadow/{shadow_id}/`.
Acceptance gates (Milestone-0): `acceptance-gates` → `data/artifacts/acceptance_gates_latest.json` (G1–G11).
Public live: `publish-live` → stamps `public_release` on forecast + `publication_latest.json` (requires green gates).
Governance: `GOVERNANCE.md`. Validation: `validation-report`, `leave-pollster-out`, `verify-rebuild`, `replay-cycle`, `shadow-verify`.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls (live 2026) | VoteHub CC BY | Required for `run_class=publication`; fixtures are non-publication |
| Polls (historical) | FiveThirtyEight / ABC News (CC BY; Wayback/sealed) | Candidate identity + official race_id map; `--allow-synthetic` CI only |
| Pollster quality | VoteHub + FTE fill-in | House ≠ reliability |
| Economics | ALFRED/FRED or multi-vintage fixtures | Observation vs available_at; revisions do not leak |
| Approval | Curated public-aggregate vintages | As-of store |
| Finance | OpenFEC (receipts/cash/disbursements) | Matched-window share; amendment/coverage chain |
| Expert ratings | **Wikipedia multi-rater** (Cook / IE / Sabato core; WH/RCP/DDHQ/Fox/Econ extended) | Ablatable; CC BY-SA page; Solid/Likely/Lean/Tilt/Tossup |
| Licensed ratings | Optional local CSV via `COOK_RATINGS_CSV` | Dormant adapter only — no vendor license required |
| Markets | Kalshi | Soft overlays; `SENATE{ST}S` for FL/OH specials; chamber calibration **off** by default |
| Demography | State research snapshot | Similarity / covariance |
| Results | Certified archive + MEDSL 2016 + fixtures | Prefer certified |
| Race universe | Official class/special ballots | Chamber reconcile gate **2014–2024** (poll coverage production 2018–2024) |

## Evidence eligibility (P0.4)
- Tiers: `official` › `first_party` › `aggregator` › `curated` › `imputed` › `synthetic` › `untraceable`
- Publishable runs reject synthetic/imputed/untraceable; `evidence-eligibility` / `--require-publishable`
- Development may continue with `run_class=non_publication` (UI banner mandatory)

## Core model
- **Production spine (default):** PyMC hierarchical Student-t (`method=pymc`) — static Election-Day latent (`latent_path=static_election_day`)
- **Dynamic spine (P1.1):** weekly national+race RW (`method=pymc_dynamic`); compare with `compare-static-dynamic`
- **OOS replay:** `replay-cycle` defaults to scoring **pymc** folds (`--hierarchical-method pymc_dynamic` available)
- **Non-production:** `fast` hierarchical-t approximation (CI / `--allow-fast-fallback` only)
- Generic ballot: VoteHub **21-day trailing weighted average** (Winsorized headline D−R), not a single poll
- **Morris §7.2 split in core PyMC:** contracting future movement (national+race) + fixed terminal ED error
- Forward state-space challenger: national path + calibrated future/terminal scales; mode/pop/house offsets aligned
- Fundamentals prior (approval, income, fundraising, midterm, lean, GB)
- ENOP / caps / study clustering / max-weight-ratio; mode/pop as **offsets only**
- National / region / local + demographic similarity shocks; named **error_budget** in diagnostics
- Ratings / race markets / soft control pull overlays with ablation (no forced market matching)
- Fold-pure stack weights (`stack_provenance`); hierarchical mass labeled `pymc`
- Leave-pollster-out diagnostics; peer integrity gate (control gap soft)
- LA/GA runoff templates; vacancy_reason on specials; scenario sensitivity block

## Validation / ops
- Complete-cycle replay with challenger pool; LOO stack weights; production PyMC OOS
- Lead-time grid; nested Student-t df / era search; component ablations
- Published `validation_report_latest.{json,md}` + `leave_pollster_out_latest.json`
- Monitor alerts; Ed25519 signing; environment lock; `verify-rebuild`; correction registry
- **Pre-P0 cycle_replay artifacts are non-comparable** — not validated backtests (`VALIDATION_ARCHIVE_NOTICE.md`)
- **Limits:** VoteHub has no historical archive; no Cook redistribution; House/EC out of scope; peer panel is compare-only (never averaged)

## Limitations
- **Public live probabilities are enabled** (`PUBLIC_LIVE_ENABLED=True`) after the 14 Sep 2026 independent re-audit checklist. Live publish still fail-closes on red acceptance gates, ineligible evidence, or failing numerical quality.
- Historical margins for non-digitized FEC races use two-party counts scaled to certified margins pending full FEC PDF digitization; OH 2018 and AZ 2024 use exact canvass totals.
- Production economics use FRED public CSV (`A229RX0` + YoY); fixture RDPI series remain as leakage canaries only.
- Live 2026 finance uses curated FEC-browse estimates when OpenFEC rate-limits (eligible curated tier, not `fixture_hash`); full candidate-level digitization is incomplete.
- Nested LOO / stack OOF now use a **pymc** spine (aligned with production); predictive mixture currently puts mass on last_election_swing / ridge / state_space (pymc OOF CRPS did not earn mixture weight).
- Production hierarchical fit remains a static Election-Day latent; weekly dynamic is a challenger (2022 compare: static better CRPS).
- Poll coverage for 2014/2016 is not production-gated (no FTE identity archive yet).
- Peer snapshots are compare-only and never averaged into the ensemble.
- House, governors, and Electoral College are out of scope.

## Known limitations
See **Limitations** above (retained heading for acceptance G11).


## Production paths
See `DEPLOY.md`: API bearer auth (`MIDTERMS_API_KEY`), Docker/Fly deploy, Ed25519 signing
(`generate-signing-keys`, `MIDTERMS_REQUIRE_SIGNING=1`), Wikipedia ratings (`fetch-ratings`),
FTE historical ingest (`ingest-fte-polls`), VoteHub dump sealing (`seal-votehub-dumps`).
