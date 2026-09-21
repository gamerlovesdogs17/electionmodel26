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
from midterms.evidence.schema import is_active_ballot_row
from midterms.model.pymc_model import FitResult
from midterms.model.overlays import rating_from_probability


def vp_tiebreak_for_election_year(year: int) -> str:
    """Vice President's party on November election day of `year`."""
    y = int(year)
    if y >= 2025:
        return "R"  # Vance
    if y >= 2021:
        return "D"  # Harris
    if y >= 2017:
        return "R"  # Pence
    if y >= 2009:
        return "D"  # Biden
    return "R"


@dataclass
class ChamberSimulation:
    seat_draws: np.ndarray  # dem caucus seats per draw (length n_draws)
    race_win: np.ndarray  # (n_draws, n_races) 1 if dem-caucus win
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
    held_ind: int = 0
    n_joint_sims: int = 0
    n_posterior_margin_draws: int = 0


def expand_joint_draws(
    margins: np.ndarray,
    *,
    n_sims: int,
    seed: int = 0,
) -> np.ndarray:
    """
    Expand posterior predictive margin draws to ``n_sims`` joint election sims.

    Resamples existing correlated draws with replacement (preserves dependence).
    Does **not** invent independent Bernoulli race outcomes.
    """
    margins = np.asarray(margins, dtype=float)
    if margins.ndim != 2 or margins.shape[0] == 0:
        raise ValueError("margins must be (n_draws, n_races) with n_draws > 0")
    n_post, n_races = margins.shape
    target = int(n_sims)
    if target <= n_post:
        return margins[:target].copy()
    rng = np.random.default_rng(int(seed))
    idx = rng.integers(0, n_post, size=target)
    return margins[idx]


def simulate_chamber(
    fit: FitResult,
    races: pd.DataFrame,
    *,
    majority_threshold: int = 51,
    vp_tiebreak_party: str = "R",
    n_sims: int | None = None,
    sim_seed: int | None = None,
) -> tuple[ChamberSimulation, list[dict]]:
    """
    Translate joint margin draws into seat outcomes and chamber control.

    Held seats (not_up) are fixed from `held_by`. Contested seats flip by sign of
    the joint margin draw. Independents caucusing with Democrats count as Dem seats.

    With a Republican VP, Dem control requires >=51 seats; dem_seats <= 50 is
    Republican control (including 50–50).

    ``n_sims`` optionally expands posterior draws to a larger joint-simulation
    count via resampling (correlated structure preserved).
    """
    from midterms.evidence.outcome_identity import require_binary_chamber_compatibility

    require_binary_chamber_compatibility(races)
    contested = races[races.apply(is_active_ballot_row, axis=1)] if len(races) else races
    held = races[races["not_up"]] if len(races) else races
    held_ind = int((held["held_by"] == "I").sum()) if len(held) else 0
    held_dem = int((held["held_by"] == "D").sum()) + held_ind if len(held) else 0
    held_rep = int((held["held_by"] == "R").sum()) if len(held) else 0

    margins = np.asarray(fit.draws_margin, dtype=float)
    n_post = int(margins.shape[0]) if margins.ndim == 2 else 0
    if n_sims is not None and int(n_sims) > n_post > 0:
        seed = int(sim_seed if sim_seed is not None else 0)
        margins = expand_joint_draws(margins, n_sims=int(n_sims), seed=seed)
    n_draws, n_races = margins.shape
    wins = (margins >= 0).astype(int)

    # Contested seats omitted from the fit stay fixed by held_by — never invent seats.
    fit_ids = set(fit.race_ids)
    if len(contested):
        omitted = contested[~contested["race_id"].isin(fit_ids)]
        if len(omitted):
            held_dem += int(omitted["held_by"].isin(["D", "I"]).sum())
            held_rep += int((omitted["held_by"] == "R").sum())

    dem_seats = held_dem + wins.sum(axis=1)
    total = held_dem + held_rep + n_races
    if total != 100:
        raise ValueError(
            f"Senate seat accounting must total 100 (held_dem={held_dem}, "
            f"held_rep={held_rep}, contested_in_fit={n_races}, total={total}). "
            "Check not_up / withdrawn / fit race_ids."
        )

    hist: dict[str, int] = {}
    for s in dem_seats.astype(int):
        hist[str(int(s))] = hist.get(str(int(s)), 0) + 1

    p_fifty = float((dem_seats == 50).mean())
    # VP tiebreak: 50–50 → chamber control for the Vice President's party
    if vp_tiebreak_party == "R":
        p_dem_maj = float((dem_seats >= majority_threshold).mean())
        p_rep_maj = float((dem_seats < majority_threshold).mean())  # includes 50–50
    elif vp_tiebreak_party == "D":
        # Dem VP: Dem needs >=50; Rep needs <=49
        p_dem_maj = float((dem_seats >= 50).mean())
        p_rep_maj = float((dem_seats <= 49).mean())
    else:
        raise ValueError(f"vp_tiebreak_party must be 'R' or 'D', got {vp_tiebreak_party!r}")

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
        held_ind=held_ind,
        n_joint_sims=int(n_draws),
        n_posterior_margin_draws=int(n_post),
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
        incumbent = None
        if row is not None and not pd.isna(row["incumbent_party"]):
            incumbent = str(row["incumbent_party"])
        is_open = None if row is None else bool(row["is_open"])
        seat_class = None if row is None else str(row.get("seat_class", "II"))
        election_phase = None if row is None else str(row.get("election_phase") or "general")
        vacancy_reason = None
        if row is not None and "vacancy_reason" in contested_ix.columns:
            vr = row.get("vacancy_reason")
            vacancy_reason = None if vr is None or (isinstance(vr, float) and pd.isna(vr)) else str(vr)
        ticket = ticket_for_state(state)
        dem_name = str(ticket["dem_name"])
        rep_name = str(ticket["rep_name"])
        row_ballot_party = None if row is None else row.get("modeled_ballot_party")
        dem_party = str(
            row_ballot_party if pd.notna(row_ballot_party) else ticket.get("dem_party") or "D"
        )
        if dem_party not in {"D", "I"}:
            dem_party = "D"
        mean_m = float(fit.mean_margin[i])
        dem_share = 50.0 + mean_m / 2.0
        rep_share = 100.0 - dem_share
        rating = rating_from_probability(p_dem)
        favored_caucus = "D" if p_dem >= 0.5 else "R"
        favored_party = dem_party if favored_caucus == "D" else "R"
        held_caucus = (
            str(row.get("held_caucus")) if held_by == "I" and row is not None
            and pd.notna(row.get("held_caucus")) else held_by if held_by in {"D", "R"} else None
        )
        is_flip = None if held_caucus is None else favored_caucus != held_caucus
        favored_candidate_id = None if row is None else row.get(
            "modeled_candidate_id" if favored_caucus == "D" else "opposing_candidate_id"
        )
        summaries.append(
            {
                "race_id": rid,
                "state": state,
                "seat_class": seat_class,
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
                "election_phase": election_phase,
                "vacancy_reason": vacancy_reason,
                "dem_candidate": dem_name,
                "rep_candidate": rep_name,
                "dem_party": dem_party,
                "modeled_candidate_id": None if row is None else row.get("modeled_candidate_id"),
                "opposing_candidate_id": None if row is None else row.get("opposing_candidate_id"),
                "modeled_ballot_party": dem_party,
                "opposing_ballot_party": "R",
                "modeled_caucus": None if row is None else row.get("modeled_caucus"),
                "opposing_caucus": None if row is None else row.get("opposing_caucus"),
                "modeled_caucus_basis": None if row is None else row.get("modeled_caucus_basis"),
                "opposing_caucus_basis": None if row is None else row.get("opposing_caucus_basis"),
                "favored_candidate_id": favored_candidate_id,
                "caucus": favored_caucus,
                "dem_share": round(dem_share, 1),
                "rep_share": round(rep_share, 1),
                "rating": rating,
                "is_flip": is_flip,
                "favored_party": favored_party,
                "favored_caucus": favored_caucus,
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
