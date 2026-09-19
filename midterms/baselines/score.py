"""Proper scores for baseline / model evaluation."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from midterms.baselines.models import RaceForecast


def crps_gaussian(y: float, mean: float, sd: float) -> float:
    """CRPS for Normal(mean, sd) predictive distribution."""
    sd = max(sd, 1e-6)
    z = (y - mean) / sd
    return float(sd * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi)))


def brier(p: float, outcome: int) -> float:
    return float((p - outcome) ** 2)


def log_score_gaussian(y: float, mean: float, sd: float) -> float:
    return float(norm.logpdf(y, loc=mean, scale=max(sd, 1e-6)))


def score_forecasts(
    forecasts: list[RaceForecast], results: pd.DataFrame
) -> dict[str, float]:
    if results is None or results.empty:
        return {"n": 0}
    from midterms.evidence.score_targets import filter_score_eligible_results

    eligible = filter_score_eligible_results(results)
    if eligible is None or eligible.empty:
        return {"n": 0}
    ycol = "margin_value" if "margin_value" in eligible.columns else "two_party_margin"
    merged = []
    by_race = {f.race_id: f for f in forecasts}
    for _, row in eligible.iterrows():
        f = by_race.get(row["race_id"])
        if not f:
            continue
        raw_y = row.get(ycol)
        if raw_y is None or (isinstance(raw_y, float) and pd.isna(raw_y)):
            raw_y = row.get("two_party_margin")
        if raw_y is None or (isinstance(raw_y, float) and pd.isna(raw_y)):
            continue
        y = float(raw_y)
        outcome = int(y > 0)
        merged.append(
            {
                "crps": crps_gaussian(y, f.mean_margin, f.sd),
                "log_score": log_score_gaussian(y, f.mean_margin, f.sd),
                "brier": brier(f.p_dem, outcome),
                "abs_err": abs(y - f.mean_margin),
            }
        )
    if not merged:
        return {"n": 0}
    df = pd.DataFrame(merged)
    return {
        "n": int(len(df)),
        "crps": float(df["crps"].mean()),
        "log_score": float(df["log_score"].mean()),
        "brier": float(df["brier"].mean()),
        "mae": float(df["abs_err"].mean()),
    }


def discrete_crps(samples: np.ndarray, y: float) -> float:
    """Empirical CRPS for predictive samples of a scalar (e.g. seat total)."""
    s = np.sort(np.asarray(samples, dtype=float))
    n = len(s)
    if n == 0:
        return float("nan")
    term1 = float(np.mean(np.abs(s - y)))
    i = np.arange(1, n + 1)
    term2 = float((2.0 / (n * n)) * np.sum((2 * i - n - 1) * s))
    return term1 - 0.5 * term2


def score_chamber_draws(
    dem_seat_draws: np.ndarray,
    *,
    realized_dem_seats: float,
    realized_dem_control: int,
    majority_threshold: int = 51,
    vp_tiebreak_party: str = "R",
) -> dict[str, float]:
    """Joint chamber scores from seat draws (blueprint Table 5)."""
    draws = np.asarray(dem_seat_draws, dtype=float)
    if vp_tiebreak_party == "R":
        p_dem_ctl = float((draws >= majority_threshold).mean())
    else:
        p_dem_ctl = float((draws >= 50).mean())
    return {
        "seat_crps": discrete_crps(draws, realized_dem_seats),
        "seat_mae": float(np.mean(np.abs(draws - realized_dem_seats))),
        "control_brier": brier(p_dem_ctl, int(realized_dem_control)),
        "p_dem_control": p_dem_ctl,
        "expected_dem_seats": float(draws.mean()),
        "n_draws": int(len(draws)),
    }
