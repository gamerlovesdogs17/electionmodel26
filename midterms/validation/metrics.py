"""Extended proper scores and diagnostics (blueprint Table 5)."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.stats import norm

from midterms.baselines.score import brier, crps_gaussian


def interval_score(y: float, lo: float, hi: float, *, alpha: float = 0.1) -> float:
    """Gneiting interval score for (1-alpha) central predictive interval."""
    w = hi - lo
    return float(w + (2 / alpha) * (lo - y) * (y < lo) + (2 / alpha) * (y - hi) * (y > hi))


def interval_score_gaussian(y: float, mean: float, sd: float, *, alpha: float = 0.1) -> float:
    sd = max(sd, 1e-6)
    z = abs(norm.ppf(alpha / 2))
    lo, hi = mean - z * sd, mean + z * sd
    return interval_score(y, lo, hi, alpha=alpha)


def reliability_bins(
    probs: np.ndarray,
    outcomes: np.ndarray,
    *,
    n_bins: int = 10,
) -> list[dict[str, float]]:
    """Calibration / reliability diagram data."""
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0, 1, n_bins + 1)
    rows = []
    for i in range(n_bins):
        mask = (probs >= edges[i]) & (probs < edges[i + 1] if i < n_bins - 1 else probs <= edges[i + 1])
        if not mask.any():
            continue
        rows.append(
            {
                "bin_lo": float(edges[i]),
                "bin_hi": float(edges[i + 1]),
                "n": float(mask.sum()),
                "mean_p": float(probs[mask].mean()),
                "mean_y": float(outcomes[mask].mean()),
            }
        )
    return rows


def energy_score(samples: np.ndarray, y: np.ndarray) -> float:
    """
    Energy score for multivariate predictive samples.
    samples: (n_draws, d), y: (d,)
    """
    s = np.asarray(samples, dtype=float)
    y = np.asarray(y, dtype=float)
    if s.ndim != 2 or len(y) != s.shape[1] or s.shape[0] < 2:
        return float("nan")
    term1 = float(np.mean(np.linalg.norm(s - y[None, :], axis=1)))
    # pairwise
    n = min(s.shape[0], 400)
    sub = s[:n]
    diffs = sub[:, None, :] - sub[None, :, :]
    term2 = float(np.mean(np.linalg.norm(diffs, axis=2)))
    return term1 - 0.5 * term2


def variogram_score(samples: np.ndarray, y: np.ndarray, *, p: float = 0.5) -> float:
    """Simple pairwise variogram score (p=0.5)."""
    s = np.asarray(samples, dtype=float)
    y = np.asarray(y, dtype=float)
    d = len(y)
    if s.ndim != 2 or s.shape[1] != d or d < 2:
        return float("nan")
    score = 0.0
    n_pairs = 0
    for i in range(d):
        for j in range(i + 1, d):
            obs = abs(y[i] - y[j]) ** p
            pred = float(np.mean(np.abs(s[:, i] - s[:, j]) ** p))
            score += (obs - pred) ** 2
            n_pairs += 1
    return float(score / max(n_pairs, 1))


def score_margins_extended(
    means: np.ndarray,
    sds: np.ndarray,
    y: np.ndarray,
    probs: np.ndarray | None = None,
) -> dict[str, Any]:
    """Race-margin score block including interval score + reliability."""
    means = np.asarray(means, dtype=float)
    sds = np.asarray(sds, dtype=float)
    y = np.asarray(y, dtype=float)
    n = min(len(means), len(y), len(sds))
    if n == 0:
        return {"n": 0}
    crps = [crps_gaussian(y[i], means[i], sds[i]) for i in range(n)]
    ints = [interval_score_gaussian(y[i], means[i], sds[i]) for i in range(n)]
    outcomes = (y[:n] > 0).astype(float)
    if probs is None:
        probs = np.array([float(norm.sf(0, loc=means[i], scale=max(sds[i], 0.5))) for i in range(n)])
    return {
        "n": n,
        "crps": float(np.mean(crps)),
        "interval_score_90": float(np.mean(ints)),
        "brier": float(np.mean([(probs[i] - outcomes[i]) ** 2 for i in range(n)])),
        "mae": float(np.mean(np.abs(y[:n] - means[:n]))),
        "coverage_90": float(
            np.mean(
                [
                    (y[i] >= means[i] - 1.64485 * sds[i]) and (y[i] <= means[i] + 1.64485 * sds[i])
                    for i in range(n)
                ]
            )
        ),
        "reliability": reliability_bins(probs[:n], outcomes),
    }
