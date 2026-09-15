# Fresh audit containment notice (updated 14 September 2026)

**RELEASE DECISION: CLEAR FOR LIVE** (v0.9.19)

Independent re-audit artifact: `data/artifacts/independent_reaudit_latest.json`.

R-01…R-06 acceptance tests pass on the repaired vote-count ledger and all-domain
eligibility. Public live is re-enabled (`PUBLIC_LIVE_ENABLED=True`). Publish still
fail-closes on red acceptance gates, ineligible evidence, or failing numerical quality.

Residual risks remain disclosed in the model card (scaled non-canary historical
margins; curated FEC when OpenFEC 429s; R-11 isolation incomplete).
