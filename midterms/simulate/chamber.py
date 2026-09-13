"""Joint chamber simulator — correlated race margins → seats → majority."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from midterms.model.pymc_model import FitResult


@dataclass
class ChamberSimulation:
    seat_draws: np.ndarray  # dem seats per draw (length n_draws)
    race_win: np.ndarray  # (n_draws, n_races) 1 if dem win
    race_ids: list[str]
    states: list[str]
    held_dem: int
    held_rep: int
    majority_threshold: int
    p_dem_majority: float
    p_rep_majority: float
    p_tie: float
    expected_dem_seats: float
    seat_histogram: dict[str, int]

    def race_summaries(self, mean_margin: np.ndarray, sd_margin: np.ndarray) -> list[dict]:
        out = []
        for i, rid in enumerate(self.race_ids):
            p = float(self.race_win[:, i].mean())
            out.append(
                {
                    "race_id": rid,
                    "state": self.states[i],
                    "p_dem": p,
                    "p_rep": 1.0 - p,
                    "mean_margin": float(mean_margin[i]),
                    "sd_margin": float(sd_margin[i]),
                    "ci05": float(np.quantile(mean_margin[i] + 0, 0.05)) if False else None,
                }
            )
        # fix CI from draws if we have them externally — filled by simulate()
        return out


def simulate_chamber(
    fit: FitResult,
    races: pd.DataFrame,
    *,
    majority_threshold: int = 51,
) -> tuple[ChamberSimulation, list[dict]]:
    """
    Translate joint margin draws into seat outcomes and chamber totals.

    Held seats (not_up) are fixed from `held_by`. Contested seats flip by sign of
    the joint margin draw. Independents caucusing with Democrats count as Dem seats.
    """
    contested = races[~races["not_up"]]
    held = races[races["not_up"]]
    held_dem = int((held["held_by"] == "D").sum()) + int((held["held_by"] == "I").sum())
    held_rep = int((held["held_by"] == "R").sum())

    margins = fit.draws_margin
    n_draws, n_races = margins.shape
    wins = (margins > 0).astype(int)
    dem_seats = held_dem + wins.sum(axis=1)
    # If total seats != 100 due to fixture construction, scale is still coherent for majority among modeled seats
    total = held_dem + held_rep + n_races
    # renormalize held if needed so contested + held == 100
    if total != 100:
        # keep contested count; adjust held split proportionally to 100 - n_races
        target_held = 100 - n_races
        if held_dem + held_rep > 0:
            ratio = held_dem / (held_dem + held_rep)
        else:
            ratio = 0.5
        held_dem = int(round(target_held * ratio))
        held_rep = target_held - held_dem
        dem_seats = held_dem + wins.sum(axis=1)

    hist: dict[str, int] = {}
    for s in dem_seats.astype(int):
        hist[str(int(s))] = hist.get(str(int(s)), 0) + 1

    p_dem_maj = float((dem_seats >= majority_threshold).mean())
    p_rep_maj = float(((total - dem_seats) >= majority_threshold).mean()) if total else float((dem_seats < majority_threshold).mean())
    # For Senate, 50-50 is a tie (VP break) — treat exactly 50 as tie when threshold is 51
    p_tie = float((dem_seats == 50).mean()) if majority_threshold == 51 else 0.0

    sim = ChamberSimulation(
        seat_draws=dem_seats,
        race_win=wins,
        race_ids=fit.race_ids,
        states=fit.states,
        held_dem=held_dem,
        held_rep=held_rep,
        majority_threshold=majority_threshold,
        p_dem_majority=p_dem_maj,
        p_rep_majority=p_rep_maj,
        p_tie=p_tie,
        expected_dem_seats=float(dem_seats.mean()),
        seat_histogram=hist,
    )

    summaries = []
    for i, rid in enumerate(fit.race_ids):
        draw_i = margins[:, i]
        summaries.append(
            {
                "race_id": rid,
                "state": fit.states[i],
                "p_dem": float(wins[:, i].mean()),
                "p_rep": float(1.0 - wins[:, i].mean()),
                "mean_margin": float(fit.mean_margin[i]),
                "sd_margin": float(fit.sd_margin[i]),
                "ci05": float(np.quantile(draw_i, 0.05)),
                "ci95": float(np.quantile(draw_i, 0.95)),
                "prior_lean": float(contested.set_index("race_id").loc[rid, "prior_lean"])
                if rid in set(contested["race_id"])
                else None,
                "incumbent_party": contested.set_index("race_id").loc[rid, "incumbent_party"]
                if rid in set(contested["race_id"])
                else None,
                "is_open": bool(contested.set_index("race_id").loc[rid, "is_open"])
                if rid in set(contested["race_id"])
                else None,
            }
        )
    summaries.sort(key=lambda x: abs(x["p_dem"] - 0.5))
    return sim, summaries


def independent_bernoulli_foil(summaries: list[dict], held_dem: int, n_draws: int = 5000, seed: int = 1) -> np.ndarray:
    """Documented foil: independent Bernoulli from marginals (NOT used for production totals)."""
    rng = np.random.default_rng(seed)
    ps = np.array([s["p_dem"] for s in summaries])
    wins = rng.random((n_draws, len(ps))) < ps
    return held_dem + wins.sum(axis=1)
