"""Honest static vs dynamic hierarchical comparison (audit P1.1)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import ARTIFACTS_DIR, PRIMARY_HOLDOUT
from midterms.evidence.warehouse import Warehouse
from midterms.model.pymc_model import fit_pymc, fit_pymc_dynamic
from midterms.baselines.score import score_forecasts


def compare_static_vs_dynamic(
    year: int = PRIMARY_HOLDOUT,
    *,
    lead_days: tuple[int, ...] = (90, 60, 30, 7),
    draws: int = 150,
    tune: int = 150,
    chains: int = 2,
    seed: int = 2022,
) -> dict[str, Any]:
    """
    Score static Election-Day PyMC vs weekly-RW dynamic PyMC at historical leads.

    Both models see the same as-of snapshot; outcomes are certified margins.
    """
    wh = Warehouse(ensure_fixtures=False)
    election_id = f"senate-{year}"
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        return {"ok": False, "error": f"no races for {election_id}"}
    ed = __import__("datetime").date.fromisoformat(str(races["election_day"].iloc[0])[:10])
    results = wh.results[wh.results["election_id"] == election_id]
    if results.empty:
        return {"ok": False, "error": f"no results for {election_id}"}
    from midterms.evidence.score_targets import truth_margin_map

    truth = pd.Series(truth_margin_map(results), dtype=float)

    by_lead: dict[str, Any] = {}
    for lead in lead_days:
        as_of = ed - timedelta(days=int(lead))
        snap = wh.build_as_of(as_of, election_id)
        static = fit_pymc(
            snap, draws=draws, tune=tune, chains=chains, seed=seed + lead, generic_ballot=0.0
        )
        dynamic = fit_pymc_dynamic(
            snap, draws=draws, tune=tune, chains=chains, seed=seed + lead + 17, generic_ballot=0.0
        )

        def _score(fit) -> dict[str, Any]:
            from midterms.baselines.models import RaceForecast
            from scipy.stats import norm

            fcs = []
            rows = []
            for i, rid in enumerate(fit.race_ids):
                if rid not in truth.index:
                    continue
                y = float(truth.loc[rid])
                if not np.isfinite(y):
                    continue
                mu = float(fit.mean_margin[i])
                sd = float(max(fit.sd_margin[i], 0.5))
                fcs.append(
                    RaceForecast(
                        race_id=rid,
                        state=fit.states[i],
                        mean_margin=mu,
                        sd=sd,
                        p_dem=float(norm.sf(0, loc=mu, scale=sd)),
                    )
                )
                rows.append({"race_id": rid, "two_party_margin": y})
            if not fcs:
                return {"n": 0}
            import pandas as pd

            return score_forecasts(fcs, pd.DataFrame(rows))

        s_static = _score(static)
        s_dyn = _score(dynamic)
        by_lead[str(lead)] = {
            "as_of": as_of.isoformat(),
            "static": {
                "method": static.method,
                "latent_path": (static.diagnostics or {}).get("latent_path"),
                "scores": s_static,
            },
            "dynamic": {
                "method": dynamic.method,
                "latent_path": (dynamic.diagnostics or {}).get("latent_path"),
                "n_weeks": (dynamic.diagnostics or {}).get("n_weeks"),
                "scores": s_dyn,
            },
            "delta_crps_dynamic_minus_static": (
                float(s_dyn.get("crps", np.nan) - s_static.get("crps", np.nan))
                if "crps" in s_dyn and "crps" in s_static
                else None
            ),
        }

    deltas = [
        v["delta_crps_dynamic_minus_static"]
        for v in by_lead.values()
        if v.get("delta_crps_dynamic_minus_static") is not None
    ]
    report = {
        "ok": True,
        "year": year,
        "election_id": election_id,
        "draws": draws,
        "by_lead": by_lead,
        "mean_delta_crps_dynamic_minus_static": float(np.mean(deltas)) if deltas else None,
        "note": (
            "Negative delta_crps means dynamic beats static. "
            "Both models are scored on the same as-of snapshots and certified margins."
        ),
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / f"static_vs_dynamic_{year}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    report["path"] = str(path)
    return report
