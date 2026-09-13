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
    merged = []
    by_race = {f.race_id: f for f in forecasts}
    for _, row in results.iterrows():
        f = by_race.get(row["race_id"])
        if not f:
            continue
        y = float(row["two_party_margin"])
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
