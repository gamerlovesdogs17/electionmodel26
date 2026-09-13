# Model card — Senate hierarchical v0.8

## Target
- **Office:** U.S. Senate only (Class II 2026 + OH/FL specials + historical cycles)
- **Estimands:** two-party margins; joint Dem seats; chamber control (≥51 Dem; ≤50 → R via VP)
- **Auxiliary:** multiway shares + turnout foils (do not drive seat math)
- **Independents:** Ind display when no Dem nominee; still Dem caucus seats

## Update cadence
`forecast` / `refresh`. Releases: `data/manifests/releases.jsonl` + signed `data/releases/{run_id}/`.
Governance: `GOVERNANCE.md`. Validation: `validation-report`.

## Sources
| Domain | Source | Notes |
| --- | --- | --- |
| Polls | VoteHub (live 2026, CC BY) + FiveThirtyEight Senate poll mirror (historical, CC BY) + fixtures | VoteHub has no archive endpoint |

| Pollster quality | VoteHub + FTE fill-in | House ≠ reliability |
| Economics | ALFRED/FRED or fixtures | Vintage YoY RDPI |
| Approval | Curated public-aggregate vintages | As-of store |
| Finance | OpenFEC (receipts/cash/disbursements) | Matched-window share |
| Expert ratings | **Wikipedia Predictions table** (Cook / IE / Sabato consensus) | Ablatable overlay; Solid/Likely/Lean/**Tilt**/Tossup ladder; CC BY-SA page with attributed handicappers |
| Licensed ratings | Optional local CSV via `COOK_RATINGS_CSV` | Only if *you* hold a vendor license; never committed; overrides Wikipedia |
| Markets | Kalshi | Ablatable sparse-race / control signal (Cook substitute for many uses) |
| Markets | Kalshi | Ablatable |
| Demography | State research snapshot | Similarity / covariance |
| Results | Certified archive + MEDSL 2016 + fixtures | Prefer certified |

## Core model
- Hierarchical Student-t spine (+ optional PyMC / state-space)
- Forward state-space challenger: future movement shrinks; terminal ED error remains
- Fundamentals prior (approval, income, fundraising, midterm, lean, GB)
- ENOP / caps / study clustering; mode/pop offsets
- National / region / local + demographic similarity shocks
- CRPS stacking of hierarchical + state-space + baselines
- Ratings/Kalshi overlays with ablation
- LA/GA runoff templates; vacancy_reason on specials
- Scenario sensitivity block (not production forecasts)

## Validation / ops
- Complete-cycle replay; lead-time grid; nested Student-t df / era search
- Component ablations; extended metrics (interval score, reliability, energy)
- Monitor alerts; HMAC signatures; environment lock; correction registry
- **Limit:** FTE historical mirror can lag; licensed Cook/IE feeds are optional and local-only

## Out of scope
House, Electoral College.

## Production paths
See `DEPLOY.md`: API bearer auth (`MIDTERMS_API_KEY`), Docker/Fly deploy, Ed25519 signing
(`generate-signing-keys`, `MIDTERMS_REQUIRE_SIGNING=1`), licensed ratings CSV adapter
(`COOK_RATINGS_CSV` — vendor files never committed), VoteHub CC BY dump sealing
(`seal-votehub-dumps`).
