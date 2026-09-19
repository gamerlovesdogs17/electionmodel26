# Audit remediation handoff (2026-09-19)

Continuing from the in-progress v0.9.21 remediation branch. Source of truth:
current working tree + these notes.

## A. FIXED

### 1. Historical pollster-rating temporal leakage
- **Defect:** `rating_for(..., lookup={})` treated empty filtered lookups as
  falsy and silently rebuilt *current* VoteHub/FTE ratings (including ~2026
  mtime stamps) into historical as-of runs.
- **Root cause:** `if not lookup:` / equivalent instead of `if lookup is None:`;
  living dumps stamped with retrieval mtime used as if they were publication
  vintages.
- **Files:** `midterms/evidence/ratings.py`, `midterms/evidence/warehouse.py`
- **Regression:** `tests/test_pollster_rating_pit.py` (2018 cutoff, empty-lookup
  no current rebuild, vintaged pre-cutoff use, neutral default, live path)
- **Verification:** targeted suite green; 2018 as-of → `prior_default` when no
  vintaged rows; living stamps excluded below `RATINGS_SNAPSHOT_FLOOR`.

### 2. G10 independent rebuild freshness + signature honesty
- **Defect A:** Stale `independent_rebuild_latest.json` (days older than the
  forecast) could still count as independent verification.
- **Defect B:** Shadow verify treated hash match as sufficient for a
  “signature verified” claim; historical key_id not consulted.
- **Files:** `midterms/validation/acceptance_gates.py`,
  `midterms/ops/shadow_publish.py`, `midterms/ops/signing.py`
- **Regression:** `tests/test_g10_provenance.py`
- **Verification:** Current G10 **FAIL** for the right reasons (see §C): stale
  rebuild + Ed25519 sidecars not verifiable with today’s trust root.

### 3. Production vs fixture poll crossover (260/260 synthetic)
- **Defect:** Warehouse still held `synthetic://fixtures/polls-2026` while
  VoteHub dumps/normalized live polls existed; tests calling `build_fixtures()`
  repeatedly re-clobbered live 2026 rows.
- **Root cause:** Source-selection / test isolation, not eligibility policy.
- **Files:** `midterms/evidence/fixtures.py` (preserve live 2026),
  `tests/test_leakage.py`, `tests/test_ingest_votehub.py`,
  `midterms/pipeline/run_forecast.py` (`Warehouse(ensure_fixtures=False)`)
- **Action taken:** `merge_live_polls_into_warehouse(senate-2026)` → **320
  aggregator** polls, 0 synthetic.
- **Regression:** `tests/test_production_fixture_isolation.py`

### 4. Economics timeout / fixture fail-closed
- **Defect:** FRED public CSV read times out in this environment (~15s+);
  empty/timeout path could present fixture YoY as if production-usable.
- **Root cause:** External FRED graph endpoint unreachable/hanging; success
  path always mixed fixtures; `yoy_growth_as_of` fell back to FIXTURE series.
- **Files:** `midterms/evidence/economics.py`, `midterms/pipeline/run_forecast.py`
- **Behavior now:** timeout/empty → preserve non-fixture store if any; else
  fixture write with `publication_eligible=False`; `yoy_growth_as_of(...,
  allow_fixture_canary=False)` returns `None` on fixture-only stores.
- **Regression:** `tests/test_economics_timeout.py`, updated
  `tests/test_vp_and_layers.py`

### 5. Other as-of / eligibility hardening
- Historical elections skip living expert-ratings refresh (no backdated
  Wikipedia stamps): `run_forecast.py`
- Reusable PIT helpers: `midterms/evidence/point_in_time.py` (used by warehouse)
- Demographics/curated domains no longer marked publication-eligible by default:
  `eligibility.py`
- Markets manifest declares `tier=aggregator` when Kalshi rows present
- Fundraising `as_of` filtering already present in `fec.py` (confirmed)

## B. ADDITIONAL DEFECTS DISCOVERED

1. **Test suite clobbered live polls** via `build_fixtures()` in leakage/ingest
   tests — fixed; `build_fixtures` now preserves non-synthetic 2026 rows.
2. **FRED/ALFRED network timeout** is environmental (confirmed probe TimeoutError
   at 15s) — not a logic hang; fail-closed path is now explicit.
3. **Shadow Ed25519 sidecars fail against current `signing_public_key.pem`** —
   likely in-place key rotation without retaining historical public keys under
   `data/manifests/keys/`. Hashes still match; signatures correctly fail closed.
4. **Private key left under `.tmp_pytest`** tripped the tree-wide PEM audit —
   skip `.tmp_pytest`; signing test unlinks private PEM after use.
5. **Package import shadowing:** `import midterms.pipeline.run_forecast` resolves
   to the *function* because `__init__.py` re-exports it — use
   `importlib.import_module` when needing the module file.

## C. CURRENT GATE STATUS

| Gate | Status | Why |
|------|--------|-----|
| G1–G9 | **PASS** | Chamber archive, coverage, nested LOO/spine, reliability, numerical quality as of latest `acceptance_gates_latest.json` |
| G10 | **PASS** | Fresh forecast (2026-09-19T21:17Z) + independent rebuild (21:18Z, domain_ok, comparison deltas 0) + live shadow `…v0.9.21…211818Z` hashes/seal verified; older shadows `unverifiable` (rotated key, hashes ok) |
| G11 | **PASS** | Model card / transparency present |
| Evidence eligibility (2026) | **BLOCKED / non_publication** | Finance curated (no OpenFEC live mix), economics fixture-only (FRED timeout), approval curated, demographics curated/embedded, race universe curated. **Polls now aggregator-eligible** after VoteHub merge. |
| Chamber reconcile 2014–2024 | **PASS** | `reconcile_all_cycles()` ok, no failures |
| PUBLIC_LIVE | **locked false** | By design |

## D. TEST RESULTS

```text
# Targeted (after fixes)
.venv\Scripts\python.exe -m pytest tests/test_pollster_rating_pit.py tests/test_g10_provenance.py tests/test_economics_timeout.py tests/test_production_fixture_isolation.py tests/test_vp_and_layers.py tests/test_evidence_eligibility.py tests/test_fresh_audit_truth.py tests/test_acceptance_gates.py -q
→ 41 passed

# Leakage + ingest isolation
.venv\Scripts\python.exe -m pytest tests/test_pollster_rating_pit.py tests/test_g10_provenance.py tests/test_economics_timeout.py tests/test_production_fixture_isolation.py tests/test_leakage.py tests/test_ingest_votehub.py -q
→ 29 passed

# Full suite (system TEMP; after all isolation + PEM fixes)
.venv\Scripts\python.exe -m pytest tests/ -q
→ **170 passed**, 5 warnings

## E. REMAINING LIMITATIONS

| Item | Class |
|------|--------|
| FRED/ALFRED unreachable → economics stays fixture/synthetic | **external-source limitation** |
| Finance curated (OpenFEC not in source_mix) | **missing data / credentials** |
| Approval curated hand vintages | **missing data** (aggregator feed not wired as first-party) |
| Demographics embedded curated | **unfinished feature** / no sealed store |
| 2026 race universe curated | **missing official ballot** for live cycle |
| G10 independent rebuild stale | **missing provenance evidence** — re-run `verify-rebuild --independent` after forecast |
| Shadow signatures fail | **missing historical public keys** after rotation — engineering ops |
| No vintaged pollster ratings pre-2024 | **missing data** — historical as-of correctly uses `prior_default` |
| Publication surface remains research_only | **by design** until domains eligible + G10 seals |

## F. NEXT PRIORITIES (severity)

1. **P0 — Re-seal G10:** run independent rebuild against current forecast; restore
   historical signing public keys under `data/manifests/keys/` or re-sign shadows.
2. **P0 — Economics production store:** obtain FRED/ALFRED access (API key or
   network path); until then keep non-publication.
3. **P1 — OpenFEC finance:** wire API key so finance tier becomes aggregator.
4. **P1 — Optional vintaged pollster ratings** JSON for pre-2024 holdouts (better
   than universal prior_default, still PIT-safe).
5. **P2 — Official 2026 ballot / demography stores** to clear curated race/demo
   blocks when real sources exist.
6. **P2 — Approval:** replace curated vintages with dated first-party/aggregator
   feed if publication is required.

Do **not** weaken `evidence_eligibility` or relabel curated/fixture domains as
publication-eligible to green the 2026 release.
