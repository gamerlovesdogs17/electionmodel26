"""Joint chamber simulator — correlated race margins → seats → control.

2025–2029: Republican Vice President breaks a 50–50 Senate, so exactly 50
Democratic seats counts as Republican chamber control (not a separate tie
outcome for the control estimand).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from midterms.evidence.tickets import ticket_for_state
from midterms.model.pymc_model import FitResult
from midterms.model.overlays import rating_from_probability


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
    p_fifty_fifty: float  # P(exactly 50 D) — counted inside Rep control via VP
    expected_dem_seats: float
    seat_histogram: dict[str, int]
    vp_tiebreak_party: str = "R"


def simulate_chamber(
    fit: FitResult,
    races: pd.DataFrame,
    *,
    majority_threshold: int = 51,
    vp_tiebreak_party: str = "R",
) -> tuple[ChamberSimulation, list[dict]]:
    """
    Translate joint margin draws into seat outcomes and chamber control.

    Held seats (not_up) are fixed from `held_by`. Contested seats flip by sign of
    the joint margin draw. Independents caucusing with Democrats count as Dem seats.

    With a Republican VP, Dem control requires >=51 seats; dem_seats <= 50 is
    Republican control (including 50–50).
    """
    contested = races[~races["not_up"]]
    held = races[races["not_up"]]
    held_dem = int((held["held_by"] == "D").sum()) + int((held["held_by"] == "I").sum())
    held_rep = int((held["held_by"] == "R").sum())

    margins = fit.draws_margin
    n_draws, n_races = margins.shape
    wins = (margins > 0).astype(int)
    dem_seats = held_dem + wins.sum(axis=1)
    total = held_dem + held_rep + n_races
    if total != 100:
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

    p_fifty = float((dem_seats == 50).mean())
    # VP tiebreak: 50–50 → chamber control for the Vice President's party
    if vp_tiebreak_party == "R":
        p_dem_maj = float((dem_seats >= majority_threshold).mean())
        p_rep_maj = float((dem_seats < majority_threshold).mean())  # includes 50–50
    elif vp_tiebreak_party == "D":
        p_dem_maj = float((dem_seats >= 50).mean())  # includes 50–50
        p_rep_maj = float((dem_seats <= 49).mean())
    else:
        p_dem_maj = float((dem_seats >= majority_threshold).mean())
        p_rep_maj = float((dem_seats <= 49).mean())

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
        p_fifty_fifty=p_fifty,
        expected_dem_seats=float(dem_seats.mean()),
        seat_histogram=hist,
        vp_tiebreak_party=vp_tiebreak_party,
    )

    contested_ix = contested.set_index("race_id")
    summaries = []
    for i, rid in enumerate(fit.race_ids):
        draw_i = margins[:, i]
        p_dem = float(wins[:, i].mean())
        state = fit.states[i]
        row = contested_ix.loc[rid] if rid in contested_ix.index else None
        held_by = None if row is None else str(row["held_by"])
        prior_lean = None if row is None else float(row["prior_lean"])
        incumbent = None if row is None else row["incumbent_party"]
        is_open = None if row is None else bool(row["is_open"])
        dem_name, rep_name = ticket_for_state(state)
        mean_m = float(fit.mean_margin[i])
        # Two-party shares from expected margin (pp)
        dem_share = 50.0 + mean_m / 2.0
        rep_share = 100.0 - dem_share
        rating = rating_from_probability(p_dem)
        favored = "D" if p_dem >= 0.5 else "R"
        is_flip = bool(held_by and favored != held_by)
        summaries.append(
            {
                "race_id": rid,
                "state": state,
                "p_dem": p_dem,
                "p_rep": float(1.0 - p_dem),
                "mean_margin": mean_m,
                "sd_margin": float(fit.sd_margin[i]),
                "ci05": float(np.quantile(draw_i, 0.05)),
                "ci95": float(np.quantile(draw_i, 0.95)),
                "prior_lean": prior_lean,
                "incumbent_party": incumbent,
                "is_open": is_open,
                "held_by": held_by,
                "dem_candidate": dem_name,
                "rep_candidate": rep_name,
                "dem_share": round(dem_share, 1),
                "rep_share": round(rep_share, 1),
                "rating": rating,
                "is_flip": is_flip,
                "favored_party": favored,
            }
        )
    summaries.sort(key=lambda x: abs(x["p_dem"] - 0.5))
    return sim, summaries


def independent_bernoulli_foil(
    summaries: list[dict], held_dem: int, n_draws: int = 5000, seed: int = 1
) -> np.ndarray:
    """Documented foil: independent Bernoulli from marginals (NOT used for production totals)."""
    rng = np.random.default_rng(seed)
    ps = np.array([s["p_dem"] for s in summaries])
    wins = rng.random((n_draws, len(ps))) < ps
    return held_dem + wins.sum(axis=1)
