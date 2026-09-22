# Incomplete validation status — 2026-09-20

## Superseding code status — 2026-09-22

The September 20 research artifacts were produced before the blueprint
capability changes now in the repository. They remain historical evidence for
their exact code/data lineage and must not be treated as validation of the new
sampler gates, optional poll structures, same-family ablations, candidate and
demographic vintage contracts, economic evidence classes, overlay policy,
market semantics, institutional interface or joint-score protocol.

### Code complete in this pass

- Prior-predictive summaries, grouped PPC utilities and deterministic synthetic SBC.
- Divergence/tree-depth/BFMI/acceptance extraction and publication fail-closed logic.
- Optional hierarchical sponsor/questionnaire/study structures shared by static and dynamic PyMC.
- Same-family PyMC structural ablation registry and fitting path.
- Candidate timeline schema/loader with effective and available time filters.
- Demographic snapshot metadata interface and explicit reuse approximation.
- Economic vintage classification: ALFRED production-capable; FRED latest,
  World Bank annual and fixtures ineligible for historical replay.
- Overlay validation contract and core-only publication fallback for unvalidated layers.
- Candidate-market contract-family semantic validation.
- Draw-level threshold/runoff architecture with unvalidated transitions disabled.
- Explicit auxiliary turnout interface.
- Deterministic energy, variogram and seat-count CRPS utilities with race-order seals.
- Domain freshness policy and expanded portable environment lock.
- Formal `BLUEPRINT_COMPLIANCE_AUDIT.json` / `.md`.

### Still pending source ingest or user-initiated expensive validation

- Sourced bitemporal candidate/race timelines for all replay cutoffs.
- Point-in-time demographic source snapshots or an approved documented approximation.
- Complete ALFRED real-time history needed by historical folds.
- Market refresh under `kalshi-v4-contract-semantics`.
- Model-specific prior predictive and posterior predictive artifacts.
- Larger PyMC SBC study.
- Formal same-family nested OOF and optional poll-term ablations.
- Overlay incremental-value validation and populated contracts.
- Historical joint-score evaluation, stack refit/reproduction and calibration checks.
- Production reference fit with complete sampler health, 50,000 joint simulations,
  independent rebuild, run coherence and strict G1-G11.

No current forecast or expensive validation chain was run on 2026-09-22.

The code and historical stack validation have advanced, but the **current
research forecast has not been rerun**. On 2026-09-20 the user explicitly
declared a modeling assumption that Independent candidates and held Independent
seats count toward the Democratic caucus. Their ballot/held party remains `I`.
The assumption is recorded in race metadata and future run artifacts; it is
not a claim about any candidate's real-world caucus declaration. Do not use older `latest`
forecast, numerical, validation, rebuild, coherence, or acceptance artifacts
as evidence for this code.
The read-only run-coherence check is currently red: it detects missing new
prior/source/stack/decomposition lineage and a market store newer than the old
forecast snapshot. The changed caucus policy also makes the old current
forecast stale. This is expected until a new same-run forecast exists.

## Completed code and evidence

- Publication sampling defaults to 2000 retained draws and 2000 tune per chain
  over four chains, rejects explicit underpowered publication requests, and
  checks the 8000-sample posterior floor. The 50,000 joint-simulation target
  is configured but has **not** been executed with this code.
- The Federal Election Commission 2012/2016/2020/2024 presidential files are
  pinned by SHA-256, parsed into statewide vote counts, and used to derive a
  point-in-time 50-state structural prior. The latest available election gets
  weight 2/3 and the previous one 1/3. Each warehouse race row carries a
  derived-prior and source fingerprint. Fixture/randomized prior values are
  rejected for publication-quality research runs.
- Candidate, ballot party, modeled side, and caucus are distinct. The four
  current Independent challengers and held Independent seats have an explicit
  Democratic-caucus **model assumption** with a versioned basis. Their `I`
  ballot/held labels remain unchanged. Independent contests remain excluded
  from Democratic-minus-Republican margin scoring. Missing or conflicting
  caucus metadata still fails closed.
- The current Kalshi store was refreshed with the candidate-aware parser on
  2026-09-20. Its raw, normalized, and event-audit hashes are verified; 33
  race overlays are enabled and two are disabled. Ambiguous or unpriced events
  retain explicit disable reasons. It cannot be backdated to 2026-09-13.
- The internal decomposition hook and run lineage checks include prior, market,
  stack, evidence, and run identities. No current race decomposition was
  generated because the forecast was not rerun.
- Formal nested OOF was run on the predeclared 2018/2020/2022/2024 × 60/30-day
  protocol. Static `pymc`, `pymc_dynamic`, `state_space`, and
  `ridge_fundamentals` remain separate candidates. All 264 eligible frozen
  cases have predictive draws for these four models. The validation default
  used 800 tune + 800 retained draws per chain over two chains. Two dynamic
  folds failed the predeclared ESS threshold and were refitted using 2000 tune
  + 2000 retained draws over four chains, selected from convergence diagnostics
  alone. Their replacement predictions were frozen before truth was read.
  The final archive has zero fit failures.
- OOF component CRPS was recomputed from the frozen empirical distributions;
  the earlier Gaussian moment scores remain labeled diagnostics. Production
  stack weights were fitted by true predictive-mixture CRPS and independently
  reproduced from the frozen archive. Dynamic PyMC earned no production mass
  under the predeclared component screen.

## Test status

Focused synthetic/data tests pass, including distributional stack
reproduction, point-in-time prior provenance, market mapping integrity,
candidate/caucus separation, decomposition hooks, and freeze-before-truth
repair. The full suite produced 230 passes, one skip, and 12 failures before
an outdated dynamic-path assertion was corrected and its focused smoke test
passed. The other 11 failures at that time required chamber accounting on
rows with unknown Independent caucus metadata. A later focused synthetic
policy/accounting suite passed after the explicit assumption was added; the
full suite and current forecast have **not** been rerun after that change.

## Required before any current-run completion claim

1. Review the declared Independent Democratic-caucus assumption as a model
   choice. Do not treat it as external candidate evidence or change ballot
   party labels to Democratic.
2. When separately requested, run the publication-quality current research
   forecast with 2000/2000/4 PyMC inference and 50,000 correlated joint
   simulations, then write the
   per-race decomposition artifact.
3. Run numerical quality, validation report, independent rebuild, run
   coherence, and G1–G11 on the resulting **same-run** artifacts. These are
   not green for the new code merely because older artifacts exist.
4. Review the broader 90/60/30/14/7-day grid separately if desired; its old
   120-draw screening evidence is not the production stack dataset.

`PUBLIC_LIVE_ENABLED` remains false and `publication_surface` remains
`research_only`. No public live publication was performed.
