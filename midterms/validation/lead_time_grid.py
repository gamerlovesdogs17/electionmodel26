"""Lead-time grid replay + nested df/era lite search (blueprint §10.1)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import numpy as np

from midterms.baselines.score import score_forecasts
from midterms.config import ARTIFACTS_DIR, CYCLES, LEAD_DAYS, PRIMARY_HOLDOUT
from midterms.evidence.warehouse import Warehouse
from midterms.model.pymc_model import fit_fast_approximation
from midterms.model.state_space import fit_state_space
from midterms.validation.cycle_replay import _forecasts_from_fit, _realized_chamber
from midterms.validation.metrics import energy_score, score_margins_extended
from midterms.baselines.score import score_chamber_draws
from midterms.simulate.chamber import simulate_chamber


def replay_lead_time_grid(
    *,
    year: int = PRIMARY_HOLDOUT,
    lead_days: tuple[int, ...] = LEAD_DAYS,
    draws: int = 250,
    method: str = "fast",
) -> dict[str, Any]:
    wh = Warehouse(ensure_fixtures=False)
    election_id = f"senate-{year}"
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        return {"error": "no races", "year": year}
    ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
    results = wh.results[wh.results["election_id"] == election_id]
    by_lead = []
    for lead in lead_days:
        as_of = ed - timedelta(days=int(lead))
        snap = wh.build_as_of(as_of, election_id)
        if method == "state_space":
            fit = fit_state_space(snap, n_draws=draws, seed=year * 100 + lead)
        else:
            fit = fit_fast_approximation(snap, n_draws=draws, seed=year * 100 + lead)
        scores = score_forecasts(_forecasts_from_fit(fit), results)
        # Extended metrics on overlapping races
        if len(results) and fit.race_ids:
            res_map = results.set_index("race_id")["two_party_margin"].to_dict()
            ys, mus, sds = [], [], []
            for i, rid in enumerate(fit.race_ids):
                if rid in res_map:
                    ys.append(float(res_map[rid]))
                    mus.append(float(fit.mean_margin[i]))
                    sds.append(float(fit.sd_margin[i]))
            ext = score_margins_extended(np.array(mus), np.array(sds), np.array(ys)) if ys else {"n": 0}
        else:
            ext = {"n": 0}
        sim, _ = simulate_chamber(fit, snap.races)
        realized_seats, realized_ctl = _realized_chamber(snap.races, results)
        chamber = score_chamber_draws(
            sim.seat_draws,
            realized_dem_seats=realized_seats,
            realized_dem_control=realized_ctl,
        )
        # Energy score on realized margins vector
        y_vec = []
        idx = []
        res_map = results.set_index("race_id")["two_party_margin"].to_dict() if len(results) else {}
        for i, rid in enumerate(fit.race_ids):
            if rid in res_map:
                y_vec.append(float(res_map[rid]))
                idx.append(i)
        if len(y_vec) >= 2:
            es = energy_score(fit.draws_margin[:, idx], np.array(y_vec))
        else:
            es = float("nan")
        by_lead.append(
            {
                "lead_days": int(lead),
                "as_of": as_of.isoformat(),
                "n_polls": int(len(snap.polls)),
                "scores": scores,
                "extended": ext,
                "chamber": chamber,
                "energy_score": es,
            }
        )
    report = {"year": year, "method": method, "by_lead": by_lead}
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / f"lead_time_grid_{year}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    report["path"] = str(path)
    return report


def nested_df_era_search(
    *,
    holdout_year: int = PRIMARY_HOLDOUT,
    train_years: tuple[int, ...] | None = None,
    draws: int = 200,
) -> dict[str, Any]:
    """
    Lite nested search over Student-t df and era_weight using training cycles only.
    Holdout sealed for final score.
    """
    train_years = train_years or tuple(y for y in CYCLES if y != holdout_year)
    grid_df = (4.0, 5.0, 8.0)
    grid_era = (0.85, 1.0, 1.15)
    wh = Warehouse(ensure_fixtures=False)

    def _cycle_crps(year: int, df: float, era: float) -> float:
        election_id = f"senate-{year}"
        races = wh.races[wh.races["election_id"] == election_id]
        if races.empty:
            return 1e6
        ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
        as_of = ed - timedelta(days=60)
        snap = wh.build_as_of(as_of, election_id)
        fit = fit_state_space(
            snap, n_draws=draws, seed=year, student_t_df=df, era_weight=era
        )
        results = wh.results[wh.results["election_id"] == election_id]
        sc = score_forecasts(_forecasts_from_fit(fit), results)
        return float(sc.get("crps") if sc.get("crps") is not None else 1e6)

    rows = []
    best = None
    for df in grid_df:
        for era in grid_era:
            scores = [_cycle_crps(y, df, era) for y in train_years]
            mean_crps = float(np.mean(scores))
            row = {"student_t_df": df, "era_weight": era, "train_mean_crps": mean_crps, "train_scores": scores}
            rows.append(row)
            if best is None or mean_crps < best["train_mean_crps"]:
                best = row

    holdout_crps = _cycle_crps(holdout_year, best["student_t_df"], best["era_weight"]) if best else None
    report = {
        "holdout_year": holdout_year,
        "train_years": list(train_years),
        "grid": rows,
        "selected": best,
        "holdout_crps": holdout_crps,
        "note": "Hyperparameters selected without seeing holdout cycle",
    }
    path = ARTIFACTS_DIR / f"nested_df_era_{holdout_year}.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    report["path"] = str(path)
    return report
