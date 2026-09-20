"""Generic empirical predictive-mixture CRPS on frozen draw collections.

This module has no domain-specific outcome semantics. A case is any immutable
key, and a model is any stable identifier. Draw arrays must be frozen before
the corresponding truth is supplied to the fitting step.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from scipy.optimize import minimize

DrawMap = Mapping[str, Mapping[str, Sequence[float]]]


def prediction_fingerprint(
    draws: DrawMap, truths: Mapping[str, float], *, seed: int, max_draws: int
) -> str:
    """Hash every frozen draw and truth, including optimizer sampling settings."""
    payload = {
        "draws": {
            model: {case: [float(x) for x in values] for case, values in sorted(cases.items())}
            for model, cases in sorted(draws.items())
        },
        "truths": {case: float(value) for case, value in sorted(truths.items())},
        "seed": int(seed),
        "max_draws": int(max_draws),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _sample(values: Sequence[float], *, seed: int, max_draws: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or len(array) < 2 or not np.isfinite(array).all():
        raise ValueError("each predictive distribution needs at least two finite draws")
    array = np.sort(array)
    if len(array) <= max_draws:
        return array
    indices = np.random.default_rng(seed).choice(len(array), size=max_draws, replace=False)
    return array[np.sort(indices)]


def empirical_crps(draws: Sequence[float], truth: float) -> float:
    """Exact empirical CRPS with independent draws from the same distribution."""
    x = np.asarray(draws, dtype=np.float64)
    if x.ndim != 1 or len(x) < 2 or not np.isfinite(x).all() or not np.isfinite(truth):
        raise ValueError("finite one-dimensional draws and truth are required")
    return float(np.abs(x - truth).mean() - 0.5 * np.abs(x[:, None] - x[None, :]).mean())


def fit_predictive_mixture(
    draws: DrawMap,
    truths: Mapping[str, float],
    *,
    seed: int = 0,
    max_draws: int = 128,
    min_cases: int = 2,
) -> dict[str, Any]:
    """Fit nonnegative simplex weights using empirical mixture CRPS.

    Only cases with a truth and a frozen distribution for every model enter the
    common objective. Missing model predictions never receive surrogate draws.
    """
    if max_draws < 2:
        raise ValueError("max_draws must be at least two")
    models = sorted(draws)
    if not models:
        raise ValueError("no model distributions supplied")
    cases = sorted(set(truths).intersection(*(set(draws[m]) for m in models)))
    if len(cases) < min_cases:
        raise ValueError(f"only {len(cases)} complete cases; need {min_cases}")
    a = np.empty((len(cases), len(models)), dtype=np.float64)
    b = np.empty((len(cases), len(models), len(models)), dtype=np.float64)
    selected_counts: dict[str, dict[str, int]] = {}
    for case_index, case in enumerate(cases):
        truth = float(truths[case])
        if not np.isfinite(truth):
            raise ValueError(f"nonfinite truth for {case}")
        samples = []
        for model_index, model in enumerate(models):
            # Stable per-case seed, independent of Python's randomized hash().
            # Equal empirical distributions with equal draw counts get the
            # same index subset. Common indices also reduce Monte Carlo noise
            # when comparing distinct models fitted with matching draw counts.
            digest = hashlib.sha256(f"{seed}:{case}:{len(draws[model][case])}".encode()).digest()
            local_seed = int.from_bytes(digest[:8], "little")
            sample = _sample(draws[model][case], seed=local_seed, max_draws=max_draws)
            samples.append(sample)
            a[case_index, model_index] = np.abs(sample - truth).mean()
            selected_counts.setdefault(model, {})[case] = len(sample)
        for i, left in enumerate(samples):
            for j in range(i, len(samples)):
                distance = np.abs(left[:, None] - samples[j][None, :]).mean()
                b[case_index, i, j] = distance
                b[case_index, j, i] = distance

    mean_a = a.mean(axis=0)
    mean_b = b.mean(axis=0)

    def objective(weights: np.ndarray) -> float:
        return float(mean_a @ weights - 0.5 * weights @ mean_b @ weights)

    def gradient(weights: np.ndarray) -> np.ndarray:
        return mean_a - mean_b @ weights

    if len(models) == 1:
        weights = np.ones(1)
    else:
        starts = [np.full(len(models), 1.0 / len(models))]
        starts.extend(np.eye(len(models)))
        solutions = []
        for start in starts:
            result = minimize(
                objective,
                start,
                jac=gradient,
                method="SLSQP",
                bounds=[(0.0, 1.0)] * len(models),
                constraints=[{"type": "eq", "fun": lambda w: float(w.sum() - 1.0),
                              "jac": lambda w: np.ones_like(w)}],
                options={"ftol": 1e-12, "maxiter": 2000},
            )
            if result.success and np.isfinite(result.fun):
                solutions.append(result)
        if not solutions:
            raise RuntimeError("predictive-mixture CRPS optimization failed")
        best = min(solutions, key=lambda result: (float(result.fun), tuple(result.x)))
        weights = np.clip(best.x, 0.0, 1.0)
        weights = weights / weights.sum()

    return {
        "method": "empirical_predictive_mixture_crps_v1",
        "weights": {model: float(weight) for model, weight in zip(models, weights)},
        "objective_crps": objective(weights),
        "individual_crps": {
            model: float(mean_a[i] - 0.5 * mean_b[i, i]) for i, model in enumerate(models)
        },
        "n_complete_cases": len(cases),
        "case_ids": cases,
        "model_ids": models,
        "selected_draw_counts": selected_counts,
        "seed": int(seed),
        "max_draws": int(max_draws),
        "prediction_sha256": prediction_fingerprint(draws, truths, seed=seed, max_draws=max_draws),
    }
