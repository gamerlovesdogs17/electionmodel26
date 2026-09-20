# Incomplete validation status — 2026-09-20

This completion pass is incomplete. The current `forecast_latest.json`,
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

Still required before any completion claim:

1. Replace the stack optimizer's blended-mean approximation with true
   predictive-mixture CRPS using each candidate model's frozen distribution.
2. Run genuine nested OOF for the separate dynamic PyMC candidate across the
   formal production lead times, with the broader diagnostic grid kept distinct.
3. Refit and reproduce stack weights from that OOF artifact.
4. Rebuild the live market store with the new parser and audit every skipped
   or ambiguously mapped event.
5. Add per-race prior provenance and decomposition diagnostics.
6. Regenerate the publication-quality forecast and all dependent numerical,
   independent-rebuild, coherence, validation, and G1–G11 artifacts in order.
7. Run the full relevant test suite and audit repository junk separately.

`PUBLIC_LIVE_ENABLED` remains false and the configured publication surface
remains `research_only`.
