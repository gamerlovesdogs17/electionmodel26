"""Parsimonious fundamentals-only predictive distribution with real ridge fit."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from midterms.baselines.models import RaceForecast
from midterms.evidence.schema import is_active_ballot_row
from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.fundamentals import (
    PRIOR_COEF,
    SHRINKABLE_KEYS,
    fundamentals_mean,
)
from midterms.model.pymc_model import FitResult, draws_from_baseline_forecasts
from midterms.model.state_space import fit_state_space


def _ridge_closed_form(
    X: np.ndarray,
    y: np.ndarray,
    *,
    alpha: float,
    prior: np.ndarray,
) -> np.ndarray:
    """Ridge toward ``prior``: argmin ||y - Xb||^2 + alpha ||b - prior||^2."""
    n_feat = X.shape[1]
    xtx = X.T @ X + alpha * np.eye(n_feat)
    xty = X.T @ y + alpha * prior
    return np.linalg.solve(xtx, xty)


def fit_ridge_coefficients(
    train_races: pd.DataFrame,
    train_margins: np.ndarray,
    *,
    generic_ballot: float,
    alpha: float = 25.0,
) -> dict[str, float]:
    """
    Estimate shrinkable fundamentals coefficients on a training fold.

    ``prior_lean`` stays fixed at 1.0. Returns a full COEF dict.
    """
    from midterms.model.fundamentals import feature_row

    keys = list(SHRINKABLE_KEYS)
    rows = []
    for (_, race), y in zip(train_races.iterrows(), train_margins):
        feat = feature_row(race, generic_ballot=generic_ballot)
        # Residualize lean (fixed identity)
        lean = float(feat.get("prior_lean") or 0.0)
        rows.append([float(feat.get(k) or 0.0) for k in keys] + [float(y) - lean])
    if len(rows) < max(6, len(keys) + 1):
        return dict(PRIOR_COEF)
    mat = np.asarray(rows, dtype=float)
    X = mat[:, :-1]
    y = mat[:, -1]
    prior = np.array([float(PRIOR_COEF[k]) for k in keys], dtype=float)
    beta = _ridge_closed_form(X, y, alpha=alpha, prior=prior)
    out = dict(PRIOR_COEF)
    for k, b in zip(keys, beta):
        out[k] = float(b)
    out["prior_lean"] = 1.0
    return out


def fit_ridge_fundamentals(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 22,
    generic_ballot: float = 0.0,
    train_races: pd.DataFrame | None = None,
    train_margins: np.ndarray | None = None,
    alpha: float = 25.0,
    coefs: dict[str, float] | None = None,
) -> FitResult:
    """
    Fundamentals-only predictive distribution.

    When historical ``train_races`` / ``train_margins`` are supplied (or can be
    recovered from the warehouse), coefficients are fold-specific ridge estimates
    shrunk toward ``PRIOR_COEF``. Otherwise falls back to prior means and records
    ``ridge_fit=False``.
    """
    from midterms.model import fundamentals as fund_mod

    races = snapshot.races.copy()
    races = races[races.apply(is_active_ballot_row, axis=1)].reset_index(drop=True)

    fitted = coefs
    ridge_fit = False
    if fitted is None and train_races is not None and train_margins is not None:
        fitted = fit_ridge_coefficients(
            train_races, np.asarray(train_margins, dtype=float),
            generic_ballot=generic_ballot, alpha=alpha,
        )
        ridge_fit = True
    elif fitted is None:
        # Attempt multi-cycle historical design matrix from warehouse results
        try:
            from midterms.evidence.warehouse import Warehouse

            wh = Warehouse(ensure_fixtures=False)
            hist = wh.races.merge(
                wh.results[
                    [
                        c
                        for c in (
                            "race_id",
                            "two_party_margin",
                            "score_eligible",
                            "margin_value",
                        )
                        if c in wh.results.columns
                    ]
                ],
                on="race_id",
                how="inner",
            )
            hist = hist[hist["election_id"].astype(str) != str(snapshot.election_id)]
            hist = hist[hist.apply(is_active_ballot_row, axis=1)]
            from midterms.evidence.score_targets import filter_score_eligible_results

            hist = filter_score_eligible_results(hist)
            ycol = "margin_value" if "margin_value" in hist.columns else "two_party_margin"
            if len(hist) >= 40:
                fitted = fit_ridge_coefficients(
                    hist,
                    hist[ycol].astype(float).to_numpy(dtype=float),
                    generic_ballot=generic_ballot,
                    alpha=alpha,
                )
                ridge_fit = True
        except Exception:  # noqa: BLE001
            fitted = None

    old = fund_mod.set_coefs(fitted)
    try:
        mu = fundamentals_mean(snapshot.races, generic_ballot=generic_ballot)
    finally:
        fund_mod.set_coefs(old)

    means = np.array([float(mu.get(rid, 0.0)) for rid in races["race_id"]], dtype=float)
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
        diagnostics={
            "n_races": len(means),
            "draws": n_draws,
            "seed": seed,
            "ridge_fit": ridge_fit,
            "ridge_alpha": alpha,
            "coefs": fitted or dict(PRIOR_COEF),
        },
        method="ridge_fundamentals",
    )


def fit_poll_only_state_space(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 11,
) -> FitResult:
    """State-space with flat fundamentals and no ED fund pull (poll-only challenger)."""
    return fit_state_space(
        snapshot,
        n_draws=n_draws,
        seed=seed,
        generic_ballot=0.0,
        student_t_df=5.0,
        era_weight=1.0,
        fund_pull=0.0,
        flat_prior=True,
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
    ridge = fit_ridge_fundamentals(
        snapshot, n_draws=n_draws, seed=seed + 3, generic_ballot=generic_ballot
    )
    return {
        "state_space": ss.draws_margin,
        "poll_only_state_space": poll_only.draws_margin,
        "ridge_fundamentals": ridge.draws_margin,
    }
