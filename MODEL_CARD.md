# Model card — Senate hierarchical v0.9

## Target
- **Office:** U.S. Senate only (Class II 2026 + OH/FL specials + historical cycles)
- **Estimands:** two-party margins; joint Dem seats; chamber control (≥51 Dem; ≤50 → R via VP)
- **Auxiliary:** multiway shares + turnout foils (do not drive seat math)
- **Independents:** Ind display (purple) when no Dem nominee; still Dem caucus seats

## Update cadence
`forecast` / `refresh`. Releases: `data/manifests/releases.jsonl` + signed `data/releases/{run_id}/`.
Governance: `GOVERNANCE.md`. Validation: `validation-report`.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls (live 2026) | VoteHub CC BY | No `/polls/archive` — current cycle only |
| Polls (historical) | FiveThirtyEight / ABC News Datasette (CC BY) | Required for complete-cycle replay; synthetic only behind `--allow-synthetic` |
| Pollster quality | VoteHub + FTE fill-in | House ≠ reliability |
| Economics | ALFRED/FRED or fixtures | Vintage YoY RDPI |
| Approval | Curated public-aggregate vintages | As-of store |
| Finance | OpenFEC (receipts/cash/disbursements) | Matched-window share |
| Expert ratings | **Wikipedia multi-rater** (Cook / IE / Sabato core; WH/RCP/DDHQ/Fox/Econ extended) | Ablatable; CC BY-SA page; Solid/Likely/Lean/Tilt/Tossup |
| Licensed ratings | Optional local CSV via `COOK_RATINGS_CSV` | Dormant adapter only — no vendor license required |
| Markets | Kalshi | Ablatable sparse-race / control signal |
| Demography | State research snapshot | Similarity / covariance |
| Results | Certified archive + MEDSL 2016 + fixtures | Prefer certified |

## Core model
- **Production spine:** PyMC hierarchical Student-t (`method=pymc`); discrete CRPS `ensemble_stack` when OOS weights available
- **Non-production:** `fast` hierarchical-t approximation (CI / degraded fallback only)
- Forward state-space challenger: national path + contracting future movement; terminal ED error remains
- Fundamentals prior (approval, income, fundraising, midterm, lean, GB)
- ENOP / caps / study clustering; mode/pop as **offsets only** (not influence weights)
- National / region / local + demographic similarity shocks
- Ratings/Kalshi overlays with ablation
- LA/GA runoff templates; vacancy_reason on specials
- Scenario sensitivity block (not production forecasts)

## Validation / ops
- Complete-cycle replay with challenger pool (state-space, poll-only, ridge, baselines)
- Stack weights from OOS mean CRPS only (no hand-tuned patches)
- Lead-time grid; nested Student-t df / era search; component ablations
- Published `validation_report_latest.{json,md}` with reliability + interval scores
- Monitor alerts; Ed25519 signing; environment lock; correction registry
- **Limits:** VoteHub has no historical archive; no Cook redistribution; House/EC out of scope; peer panel is compare-only (never averaged)

## Out of scope
House, Electoral College.

## Production paths
See `DEPLOY.md`: API bearer auth (`MIDTERMS_API_KEY`), Docker/Fly deploy, Ed25519 signing
(`generate-signing-keys`, `MIDTERMS_REQUIRE_SIGNING=1`), Wikipedia ratings (`fetch-ratings`),
FTE historical ingest (`ingest-fte-polls`), VoteHub dump sealing (`seal-votehub-dumps`).
