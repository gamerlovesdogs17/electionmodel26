"""Structurally distinct stackable challengers (blueprint §9.2)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from midterms.baselines.models import RaceForecast
from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.fundamentals import fundamentals_mean
from midterms.model.pymc_model import FitResult, draws_from_baseline_forecasts
from midterms.model.state_space import fit_state_space


def fit_poll_only_state_space(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 11,
) -> FitResult:
    """State-space with flat fundamentals (poll-only challenger)."""
    return fit_state_space(
        snapshot,
        n_draws=n_draws,
        seed=seed,
        generic_ballot=0.0,
        student_t_df=5.0,
        era_weight=1.0,
    )


def fit_ridge_fundamentals(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 22,
    generic_ballot: float = 0.0,
) -> FitResult:
    """Parsimonious fundamentals-only predictive distribution."""
    from midterms.evidence.schema import is_active_ballot_row

    races = snapshot.races.copy()
    races = races[races.apply(is_active_ballot_row, axis=1)].reset_index(drop=True)
    mu = fundamentals_mean(snapshot.races, generic_ballot=generic_ballot)
    means = np.array([float(mu.get(rid, 0.0)) for rid in races["race_id"]], dtype=float)
    # Sparse-race uncertainty larger
    sds = np.full(len(means), 7.5)
    forecasts = [
        RaceForecast(
            race_id=rid,
            state=str(st),
            mean_margin=float(means[i]),
            sd=float(sds[i]),
            p_dem=float(norm.sf(0, loc=means[i], scale=sds[i])),
        )
        for i, (rid, st) in enumerate(zip(races["race_id"], races["state"]))
    ]
    draws = draws_from_baseline_forecasts(forecasts, n_draws=n_draws, seed=seed)
    return FitResult(
        race_ids=list(races["race_id"]),
        states=list(races["state"].astype(str)),
        mean_margin=means,
        sd_margin=sds,
        draws_margin=draws,
        house_effects={},
        diagnostics={"n_races": len(means), "draws": n_draws, "seed": seed},
        method="ridge_fundamentals",
    )


def build_challenger_draws(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int,
    seed: int,
    generic_ballot: float,
) -> dict[str, np.ndarray]:
    """Named draw tensors for stacking."""
    ss = fit_state_space(snapshot, n_draws=n_draws, seed=seed + 1, generic_ballot=generic_ballot)
    poll_only = fit_poll_only_state_space(snapshot, n_draws=n_draws, seed=seed + 2)
    # Force poll-only by re-fitting with zero GB and ignoring fund pull — already similar;
    # use ridge as distinct
    ridge = fit_ridge_fundamentals(
        snapshot, n_draws=n_draws, seed=seed + 3, generic_ballot=generic_ballot
    )
    return {
        "state_space": ss.draws_margin,
        "poll_only_state_space": poll_only.draws_margin,
        "ridge_fundamentals": ridge.draws_margin,
    }
