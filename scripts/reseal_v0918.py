"""One-shot reseal: independent rebuild + acceptance gates for v0.9.18."""
from __future__ import annotations

import json
from pathlib import Path

from midterms.ops.reproducibility import independent_rebuild, verify_rebuild
from midterms.validation.acceptance_gates import evaluate_acceptance_gates

art = json.loads(Path("data/artifacts/forecast_latest.json").read_text(encoding="utf-8"))
print("version", art.get("model_version"))
print("run_id", art.get("run_id"))
print(
    "surface",
    art.get("publication_surface"),
    "publishable",
    art.get("publishable"),
    "run_class",
    art.get("run_class"),
)
print("nq_ok", (art.get("numerical_quality") or {}).get("ok"))
print(
    "chamber",
    {k: art["chamber"][k] for k in ("p_dem_majority", "expected_dem_seats", "p_fifty_fifty")},
)
print("lite_rebuild", verify_rebuild().get("ok"))
ind = independent_rebuild()
print("independent", ind.get("ok"), ind.get("failures") or ind.get("notes"))
Path("data/artifacts/independent_rebuild_latest.json").write_text(
    json.dumps(ind, indent=2, default=str),
    encoding="utf-8",
)
gates = evaluate_acceptance_gates(write=True)
print(
    "gates",
    gates["ok"],
    f"{gates['n_pass']}/11",
    "fail",
    gates.get("failures"),
    "partial",
    gates.get("partials"),
)
print("live_milestone", gates.get("milestone", {}).get("public_live_probabilities"))
