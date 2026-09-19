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


def calibration_slope_intercept(
    probs: np.ndarray,
    outcomes: np.ndarray,
) -> dict[str, Any]:
    """
    Linear calibration: E[y] ≈ intercept + slope * p.

    Ideal: intercept ≈ 0, slope ≈ 1. Uses OLS on finite probs in (0, 1).
    """
    p = np.asarray(probs, dtype=float).ravel()
    y = np.asarray(outcomes, dtype=float).ravel()
    n = min(len(p), len(y))
    if n < 3:
        return {"n": int(n), "slope": float("nan"), "intercept": float("nan"), "ok": False}
    p = p[:n]
    y = y[:n]
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]
    if len(p) < 3 or float(np.std(p)) < 1e-8:
        return {"n": int(len(p)), "slope": float("nan"), "intercept": float("nan"), "ok": False}
    # OLS: [1, p] @ [a, b] = y
    x = np.column_stack([np.ones(len(p)), p])
    coef, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    intercept, slope = float(coef[0]), float(coef[1])
    return {
        "n": int(len(p)),
        "slope": slope,
        "intercept": intercept,
        "ok": True,
        "ideal": {"slope": 1.0, "intercept": 0.0},
    }


def pit_gaussian(y: float, mean: float, sd: float) -> float:
    """Probability integral transform under Normal(mean, sd)."""
    sd = max(float(sd), 1e-6)
    return float(norm.cdf(y, loc=mean, scale=sd))


def pit_summary(
    means: np.ndarray,
    sds: np.ndarray,
    y: np.ndarray,
    *,
    n_bins: int = 10,
) -> dict[str, Any]:
    """PIT histogram + uniformity diagnostics for Gaussian margin forecasts."""
    means = np.asarray(means, dtype=float)
    sds = np.asarray(sds, dtype=float)
    y = np.asarray(y, dtype=float)
    n = min(len(means), len(sds), len(y))
    if n == 0:
        return {"n": 0, "ok": False}
    pits = np.array([pit_gaussian(y[i], means[i], sds[i]) for i in range(n)])
    pits = pits[np.isfinite(pits)]
    if len(pits) == 0:
        return {"n": 0, "ok": False}
    edges = np.linspace(0, 1, n_bins + 1)
    hist = []
    for i in range(n_bins):
        mask = (pits >= edges[i]) & (pits < edges[i + 1] if i < n_bins - 1 else pits <= edges[i + 1])
        hist.append({"bin_lo": float(edges[i]), "bin_hi": float(edges[i + 1]), "n": float(mask.sum())})
    # Simple uniformity: mean near 0.5, var near 1/12
    return {
        "n": int(len(pits)),
        "mean": float(np.mean(pits)),
        "var": float(np.var(pits)),
        "expected_mean": 0.5,
        "expected_var": 1.0 / 12.0,
        "histogram": hist,
        "ok": True,
    }


def reliability_overconfidence(
    bins: list[dict[str, float]],
    *,
    min_bin_n: float = 5.0,
    gap_threshold: float = 0.15,
    min_total_n: float = 80.0,
    min_adequate_bins: int = 4,
) -> dict[str, Any]:
    """
    Flag material overconfidence: mean_p ≫ mean_y in disclosed bins.

    G7: calibration claim fails if overconfident bins lack sample-size disclosure
    or gap exceeds threshold on bins with adequate n. Thin samples (audit P1)
    never allow a calibration claim even when no overconfident bin is flagged.
    """
    issues: list[dict[str, Any]] = []
    disclosed = True
    total_n = 0.0
    adequate_bins = 0
    for b in bins or []:
        n = float(b.get("n") or 0)
        total_n += n
        if "n" not in b:
            disclosed = False
        if n >= min_bin_n:
            adequate_bins += 1
        mean_p = float(b.get("mean_p") or 0)
        mean_y = float(b.get("mean_y") or 0)
        gap = mean_p - mean_y
        if n >= min_bin_n and gap > gap_threshold:
            issues.append(
                {
                    "bin_lo": b.get("bin_lo"),
                    "bin_hi": b.get("bin_hi"),
                    "n": n,
                    "mean_p": mean_p,
                    "mean_y": mean_y,
                    "gap": gap,
                }
            )
    thin = total_n < min_total_n or adequate_bins < min_adequate_bins
    return {
        "sample_sizes_disclosed": disclosed,
        "overconfident_bins": issues,
        "n_overconfident": len(issues),
        "total_n": total_n,
        "n_adequate_bins": adequate_bins,
        "thin_sample": thin,
        "calibration_claim_allowed": (
            disclosed and len(issues) == 0 and not thin and bool(bins)
        ),
    }


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
    rel = reliability_bins(probs[:n], outcomes)
    return {
        "n": n,
        "crps": float(np.mean(crps)),
        "interval_score_90": float(np.mean(ints)),
        "brier": float(np.mean([(probs[i] - outcomes[i]) ** 2 for i in range(n)])),
        "log_score": float(
            np.mean(
                [
                    np.log(max(float(probs[i]) if outcomes[i] > 0.5 else 1.0 - float(probs[i]), 1e-12))
                    for i in range(n)
                ]
            )
        ),
        "mae": float(np.mean(np.abs(y[:n] - means[:n]))),
        "coverage_90": float(
            np.mean(
                [
                    (y[i] >= means[i] - 1.64485 * sds[i]) and (y[i] <= means[i] + 1.64485 * sds[i])
                    for i in range(n)
                ]
            )
        ),
        "reliability": rel,
        "calibration_slope_intercept": calibration_slope_intercept(probs[:n], outcomes),
        "pit": pit_summary(means[:n], sds[:n], y[:n]),
        "reliability_gate": reliability_overconfidence(rel),
    }
