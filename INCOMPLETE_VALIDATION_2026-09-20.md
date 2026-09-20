# Incomplete validation status — 2026-09-20

This completion pass remains incomplete as a real-data validation effort. The current `forecast_latest.json`,
`nested_component_loo.json`, `stack_weights_oof.json`, validation reports,
independent rebuild, and acceptance artifacts predate the code changes in
this pass. Do not treat them as evidence for the modified pipeline.

Completed code and focused verification:

- Publication sampling now resolves to the production floor by default and
  rejects explicit underpowered publication requests before fitting.
- The numerical gate checks per-chain draws, tuning, chain count, and the
  retained posterior total for publication runs.
- Candidate-aware market mapping uses the modeled candidate identity and
  disables ambiguous events. Multi-contract events normalize across all
  priced contracts.
- A development run cannot inherit a publication label solely from evidence
  eligibility.
- Focused synthetic regression tests passed.
- Generic empirical predictive-mixture CRPS now optimizes frozen draw distributions
  on the simplex. Prediction and matrix fingerprints support deterministic refitting;
  old mean-only stack artifacts fail closed.
- A domain-neutral model registry stores separately identified predictions, fit
  settings, per-model failures, and a freeze-before-truth grouped validation index.
  The nested export retains each declared lead as a separate frozen draw case.
- A versioned relative-prior source schema and pure point-in-time weighted
  calculation are available, with explicit non-production fixture fallback.
- Candidate, ballot party, modeled side, and caucus affiliation have separate
  generic identities. Market mappings expose an audit record; ambiguous mappings
  disable themselves. An internal decomposition schema and artifact-lineage check
  are available for future integration.

None of these generic modules has populated a new current prior, decomposition,
market store, forecast, or OOF result. The current artifacts remain stale relative
to this code. The existing production-facing prior construction has not yet been
replaced by a sourced series.

Still required before any completion claim:

1. Run genuine nested OOF for the separately identified model candidates across the
   formal production lead times, with the broader diagnostic grid kept distinct.
2. Refit and reproduce stack weights from that OOF artifact.
3. Acquire and ingest a dated, attributable source series for the measured prior,
   then connect the new framework to the production race builder and historical snapshots.
4. Rebuild the market store with the new parser and audit every skipped
   or ambiguously mapped event.
5. Connect per-race prior provenance, explicit caucus metadata, and decomposition
   diagnostics to the real-data pipeline after source validation.
6. Regenerate the research forecast and all dependent numerical,
   independent-rebuild, coherence, validation, and G1–G11 artifacts in order.
7. Run the full relevant test suite and audit repository junk separately.

`PUBLIC_LIVE_ENABLED` remains false and the configured publication surface
remains `research_only`.
