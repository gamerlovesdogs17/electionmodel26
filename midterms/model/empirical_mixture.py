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


def _normalized_weights(
    weights: Mapping[str, float], *, available_models: Sequence[str]
) -> dict[str, float]:
    """Validate and normalize a nonnegative mixture on known model IDs."""
    available = set(available_models)
    unknown = sorted(set(weights) - available)
    if unknown:
        raise ValueError(f"weights reference unknown models: {unknown}")
    normalized = {str(model): float(weight) for model, weight in weights.items()}
    if not normalized or any(not np.isfinite(value) or value < 0 for value in normalized.values()):
        raise ValueError("mixture weights must be finite and nonnegative")
    total = float(sum(normalized.values()))
    if total <= 0:
        raise ValueError("mixture weights must have positive mass")
    return {model: value / total for model, value in normalized.items() if value > 0}


def weighted_mixture_mean(
    draws: Mapping[str, Sequence[float]], weights: Mapping[str, float]
) -> float:
    """Return the exact empirical mean of a weighted draw mixture."""
    w = _normalized_weights(weights, available_models=sorted(draws))
    means = {}
    for model in w:
        values = np.asarray(draws[model], dtype=np.float64)
        if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
            raise ValueError("each predictive distribution needs at least two finite draws")
        means[model] = float(values.mean())
    return float(sum(w[model] * means[model] for model in w))


def widen_mixture_draws(
    draws: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
    *,
    scale: float,
) -> dict[str, np.ndarray]:
    """Scale all distributions around their shared mixture mean.

    The same center is used for every component, so the weighted empirical
    mean is preserved exactly up to floating-point rounding.
    """
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and positive")
    center = weighted_mixture_mean(draws, weights)
    return {
        model: center + float(scale) * (np.asarray(values, dtype=np.float64) - center)
        for model, values in draws.items()
    }


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


def _case_samples(
    draws: DrawMap,
    *,
    models: Sequence[str],
    case: str,
    seed: int,
    max_draws: int,
) -> dict[str, np.ndarray]:
    samples: dict[str, np.ndarray] = {}
    for model in models:
        digest = hashlib.sha256(f"{seed}:{case}:{len(draws[model][case])}".encode()).digest()
        local_seed = int.from_bytes(digest[:8], "little")
        samples[model] = _sample(
            draws[model][case], seed=local_seed, max_draws=max_draws,
        )
    return samples


def mixture_crps_scale_grid(
    draws: DrawMap,
    truths: Mapping[str, float],
    weights: Mapping[str, float],
    scales: Sequence[float],
    *,
    seed: int = 0,
    max_draws: int = 128,
) -> list[dict[str, float]]:
    """Score a predeclared scale grid without resampling for each scale.

    For positive ``s``, pairwise distances after widening equal ``s`` times
    their raw value. This computes that term once per case and keeps scale
    selection deterministic and inexpensive.
    """
    if max_draws < 2:
        raise ValueError("max_draws must be at least two")
    grid = [float(scale) for scale in scales]
    if not grid or any(not np.isfinite(scale) or scale <= 0 for scale in grid):
        raise ValueError("scales must be a nonempty sequence of positive finite values")
    w = _normalized_weights(weights, available_models=sorted(draws))
    models = sorted(w)
    cases = sorted(set(truths).intersection(*(set(draws[model]) for model in models)))
    if not cases:
        raise ValueError("no complete truth/draw cases for mixture evaluation")
    totals = np.zeros(len(grid), dtype=np.float64)
    for case in cases:
        truth = float(truths[case])
        if not np.isfinite(truth):
            raise ValueError(f"nonfinite truth for {case}")
        samples = _case_samples(
            draws, models=models, case=case, seed=seed, max_draws=max_draws,
        )
        center = float(sum(w[model] * samples[model].mean() for model in models))
        pairwise = 0.0
        for left in models:
            for right in models:
                pairwise += (
                    w[left]
                    * w[right]
                    * float(np.abs(samples[left][:, None] - samples[right][None, :]).mean())
                )
        for index, scale in enumerate(grid):
            first = sum(
                w[model]
                * float(np.abs(center + scale * (samples[model] - center) - truth).mean())
                for model in models
            )
            totals[index] += first - 0.5 * scale * pairwise
    return [
        {"scale": scale, "empirical_crps": float(total / len(cases))}
        for scale, total in zip(grid, totals)
    ]


def evaluate_predictive_mixture(
    draws: DrawMap,
    truths: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    scale: float = 1.0,
    seed: int = 0,
    max_draws: int = 128,
) -> dict[str, Any]:
    """Evaluate a fixed empirical mixture with exact empirical event probabilities.

    CRPS uses the same deterministic draw cap as the production stack
    optimizer. Binary probabilities use every frozen draw, avoiding mixture
    resampling noise.
    """
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and positive")
    w = _normalized_weights(weights, available_models=sorted(draws))
    models = sorted(w)
    cases = sorted(set(truths).intersection(*(set(draws[model]) for model in models)))
    if not cases:
        raise ValueError("no complete truth/draw cases for mixture evaluation")
    grid_scores = mixture_crps_scale_grid(
        draws, truths, w, [scale], seed=seed, max_draws=max_draws,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        truth = float(truths[case])
        full = {
            model: np.asarray(draws[model][case], dtype=np.float64)
            for model in models
        }
        center = float(sum(w[model] * full[model].mean() for model in models))
        transformed = {
            model: center + float(scale) * (full[model] - center)
            for model in models
        }
        probability = float(sum(
            w[model] * float(np.mean(transformed[model] > 0.0))
            for model in models
        ))
        outcome = float(truth > 0.0)
        pit = float(sum(
            w[model]
            * float(
                np.mean(transformed[model] < truth)
                + 0.5 * np.mean(transformed[model] == truth)
            )
            for model in models
        ))
        samples = _case_samples(
            draws, models=models, case=case, seed=seed, max_draws=max_draws,
        )
        sample_center = float(sum(w[model] * samples[model].mean() for model in models))
        scaled_samples = {
            model: sample_center + float(scale) * (samples[model] - sample_center)
            for model in models
        }
        first = sum(
            w[model] * float(np.abs(scaled_samples[model] - truth).mean())
            for model in models
        )
        second = sum(
            w[left]
            * w[right]
            * float(np.abs(
                scaled_samples[left][:, None] - scaled_samples[right][None, :]
            ).mean())
            for left in models
            for right in models
        )
        rows.append({
            "case_id": case,
            "truth": truth,
            "outcome": outcome,
            "probability": probability,
            "predictive_mean": center,
            "empirical_crps": float(first - 0.5 * second),
            "pit": pit,
        })
    probs = np.asarray([row["probability"] for row in rows], dtype=np.float64)
    outcomes = np.asarray([row["outcome"] for row in rows], dtype=np.float64)
    selected = np.where(outcomes > 0.5, probs, 1.0 - probs)
    return {
        "n": len(rows),
        "scale": float(scale),
        "weights": w,
        "empirical_crps": float(np.mean([row["empirical_crps"] for row in rows])),
        "grid_consistency_crps": grid_scores[0]["empirical_crps"],
        "brier": float(np.mean((probs - outcomes) ** 2)),
        "log_score": float(np.mean(np.log(np.clip(selected, 1e-12, 1.0)))),
        "log_score_orientation": "higher_is_better",
        "cases": rows,
        "seed": int(seed),
        "max_draws": int(max_draws),
        "probability_method": "exact_weighted_empirical_exceedance",
        "crps_method": "empirical_predictive_mixture_crps_deterministic_subsample",
    }


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
