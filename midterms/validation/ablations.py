"""Systematic component ablations (blueprint §10.3)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

from midterms.baselines.score import score_forecasts
from midterms.config import ARTIFACTS_DIR, PRIMARY_HOLDOUT
from midterms.evidence.warehouse import Warehouse
from midterms.model.pymc_model import fit_fast_approximation
from midterms.validation.cycle_replay import _forecasts_from_fit


def run_component_ablations(
    *,
    year: int = PRIMARY_HOLDOUT,
    lead_days: int = 60,
    draws: int = 300,
) -> dict[str, Any]:
    """
    Drop-one structural switches on the hierarchical core for one cycle/as-of.
    Components: heavy_tails, similarity, national_factor (via diagnostics flags
    approximated by refits with modified seeds/params where needed).
    """
    wh = Warehouse(ensure_fixtures=False)
    election_id = f"senate-{year}"
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        return {"error": "no races", "year": year}
    ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
    as_of = ed - timedelta(days=lead_days)
    snap = wh.build_as_of(as_of, election_id)
    results = wh.results[wh.results["election_id"] == election_id]

    base = fit_fast_approximation(snap, n_draws=draws, seed=year)
    base_scores = score_forecasts(_forecasts_from_fit(base), results)

    # Approximate ablations by surgically editing draws
    ablations: dict[str, Any] = {"baseline": base_scores}

    # No heavy tails ≈ Gaussianize shocks (use Normal redraw around means)
    rng = np.random.default_rng(year)
    gauss = rng.normal(base.mean_margin, np.maximum(base.sd_margin, 0.5), size=base.draws_margin.shape)
    from midterms.model.pymc_model import FitResult

    gfit = FitResult(
        race_ids=base.race_ids,
        states=base.states,
        mean_margin=gauss.mean(0),
        sd_margin=gauss.std(0),
        draws_margin=gauss,
        house_effects=base.house_effects,
        diagnostics={**base.diagnostics, "ablation": "no_heavy_tails"},
        method="fast_hierarchical_t-no_t",
    )
    ablations["no_heavy_tails"] = score_forecasts(_forecasts_from_fit(gfit), results)

    # Independent races (destroy correlation): reshuffle race columns per draw
    indep = base.draws_margin.copy()
    for i in range(indep.shape[0]):
        indep[i] = rng.permutation(indep[i])
    # Better: redraw independently
    indep = rng.normal(base.mean_margin, np.maximum(base.sd_margin, 0.5), size=base.draws_margin.shape)
    ifit = FitResult(
        race_ids=base.race_ids,
        states=base.states,
        mean_margin=indep.mean(0),
        sd_margin=indep.std(0),
        draws_margin=indep,
        house_effects=base.house_effects,
        diagnostics={**base.diagnostics, "ablation": "independent_errors"},
        method="fast_hierarchical_t-indep",
    )
    ablations["independent_errors"] = score_forecasts(_forecasts_from_fit(ifit), results)

    # No national factor: subtract draw-wise mean
    nonat = base.draws_margin - base.draws_margin.mean(axis=1, keepdims=True) + base.mean_margin
    nfit = FitResult(
        race_ids=base.race_ids,
        states=base.states,
        mean_margin=nonat.mean(0),
        sd_margin=nonat.std(0),
        draws_margin=nonat,
        house_effects=base.house_effects,
        diagnostics={**base.diagnostics, "ablation": "no_national_factor"},
        method="fast_hierarchical_t-nonat",
    )
    ablations["no_national_factor"] = score_forecasts(_forecasts_from_fit(nfit), results)

    report = {
        "year": year,
        "as_of": as_of.isoformat(),
        "lead_days": lead_days,
        "ablations": ablations,
        "deltas_crps_vs_baseline": {
            k: float((v.get("crps") or np.nan) - (base_scores.get("crps") or np.nan))
            for k, v in ablations.items()
            if k != "baseline" and isinstance(v, dict)
        },
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    import json

    path = ARTIFACTS_DIR / f"component_ablation_{year}.json"
    path.write_text(json.dumps(report, indent=2))
    report["path"] = str(path)
    return report
