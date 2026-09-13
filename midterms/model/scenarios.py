"""Scenario sensitivity (blueprint §12.2) — labeled foils, not new forecasts."""

from __future__ import annotations

from typing import Any

import numpy as np

from midterms.model.pymc_model import FitResult
from midterms.simulate.chamber import simulate_chamber


def run_scenarios(
    fit: FitResult,
    races,
    *,
    vp_tiebreak_party: str = "R",
) -> dict[str, Any]:
    """
    National / regional / reduced-quality miss scenarios on joint draws.
    Published as sensitivity, not production forecasts.
    """
    base_sim, _ = simulate_chamber(fit, races, vp_tiebreak_party=vp_tiebreak_party)
    out: dict[str, Any] = {
        "note": "Scenario sensitivities — not production forecasts",
        "baseline": {
            "p_dem_majority": base_sim.p_dem_majority,
            "expected_dem_seats": base_sim.expected_dem_seats,
        },
    }

    def _shift(delta: float, mask: np.ndarray | None = None) -> dict[str, float]:
        draws = fit.draws_margin.copy()
        if mask is None:
            draws = draws + delta
        else:
            draws[:, mask] = draws[:, mask] + delta
        shifted = FitResult(
            race_ids=fit.race_ids,
            states=fit.states,
            mean_margin=draws.mean(axis=0),
            sd_margin=draws.std(axis=0),
            draws_margin=draws,
            house_effects=fit.house_effects,
            diagnostics=fit.diagnostics,
            method=fit.method + "+scenario",
        )
        sim, _ = simulate_chamber(shifted, races, vp_tiebreak_party=vp_tiebreak_party)
        return {
            "p_dem_majority": sim.p_dem_majority,
            "expected_dem_seats": sim.expected_dem_seats,
            "delta_p_dem": float(sim.p_dem_majority - base_sim.p_dem_majority),
        }

    out["national_dem_miss_m3"] = _shift(-3.0)
    out["national_rep_miss_m3"] = _shift(3.0)

    # Regional: South
    south = np.array([str(s) for s in fit.states])
    # Map via races region if available
    region = None
    if "region" in races.columns:
        by_id = races.set_index("race_id")["region"].to_dict()
        region = np.array([str(by_id.get(rid, "")) for rid in fit.race_ids])
        south_mask = region == "South"
        if south_mask.any():
            out["south_dem_miss_m4"] = _shift(-4.0, south_mask)

    # Reduced poll quality → inflate residual sd (approximate via extra noise)
    rng = np.random.default_rng(7)
    noisy = fit.draws_margin + rng.standard_t(5, size=fit.draws_margin.shape) * 2.0
    noisy_fit = FitResult(
        race_ids=fit.race_ids,
        states=fit.states,
        mean_margin=noisy.mean(axis=0),
        sd_margin=noisy.std(axis=0),
        draws_margin=noisy,
        house_effects=fit.house_effects,
        diagnostics=fit.diagnostics,
        method=fit.method + "+low_quality",
    )
    sim_q, _ = simulate_chamber(noisy_fit, races, vp_tiebreak_party=vp_tiebreak_party)
    out["reduced_poll_quality"] = {
        "p_dem_majority": sim_q.p_dem_majority,
        "expected_dem_seats": sim_q.expected_dem_seats,
        "delta_p_dem": float(sim_q.p_dem_majority - base_sim.p_dem_majority),
    }
    return out
