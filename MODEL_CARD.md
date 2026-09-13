# Model card — Senate hierarchical v0.9.2

## Target
- **Office:** U.S. Senate only (Class II 2026 + OH/FL specials + historical cycles)
- **Estimands:** two-party margins; joint Dem seats; chamber control (≥51 Dem; ≤50 → R via VP)
- **Auxiliary:** multiway shares + turnout foils (do not drive seat math)
- **Independents:** Ind display (purple) when no Dem nominee; still Dem caucus seats

## Update cadence
`forecast` / `refresh`. Releases: `data/manifests/releases.jsonl` + signed `data/releases/{run_id}/`.
Governance: `GOVERNANCE.md`. Validation: `validation-report`, `leave-pollster-out`, `verify-rebuild`, `replay-cycle`.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls (live 2026) | VoteHub CC BY | No `/polls/archive` — current cycle only |
| Polls (historical) | FiveThirtyEight / ABC News Datasette (CC BY) | Required for complete-cycle replay; synthetic only behind `--allow-synthetic` |
| Pollster quality | VoteHub + FTE fill-in | House ≠ reliability |
| Economics | ALFRED/FRED or multi-vintage fixtures | Observation vs available_at; revisions do not leak |
| Approval | Curated public-aggregate vintages | As-of store |
| Finance | OpenFEC (receipts/cash/disbursements) | Matched-window share; amendment/coverage chain |
| Expert ratings | **Wikipedia multi-rater** (Cook / IE / Sabato core; WH/RCP/DDHQ/Fox/Econ extended) | Ablatable; CC BY-SA page; Solid/Likely/Lean/Tilt/Tossup |
| Licensed ratings | Optional local CSV via `COOK_RATINGS_CSV` | Dormant adapter only — no vendor license required |
| Markets | Kalshi | Soft overlays; `SENATE{ST}S` for FL/OH specials; chamber calibration **off** by default |
| Demography | State research snapshot | Similarity / covariance |
| Results | Certified archive + MEDSL 2016 + fixtures | Prefer certified |

## Core model
- **Production spine:** PyMC hierarchical Student-t (`method=pymc`); discrete CRPS `ensemble_stack` when OOS weights available
- **OOS replay:** `replay-cycle` defaults to scoring **pymc** folds (use `--hierarchical-method fast` for CI)
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
- **Limits:** VoteHub has no historical archive; no Cook redistribution; House/EC out of scope; peer panel is compare-only (never averaged)

## Out of scope
House, Electoral College, governors.

## Production paths
See `DEPLOY.md`: API bearer auth (`MIDTERMS_API_KEY`), Docker/Fly deploy, Ed25519 signing
(`generate-signing-keys`, `MIDTERMS_REQUIRE_SIGNING=1`), Wikipedia ratings (`fetch-ratings`),
FTE historical ingest (`ingest-fte-polls`), VoteHub dump sealing (`seal-votehub-dumps`).
