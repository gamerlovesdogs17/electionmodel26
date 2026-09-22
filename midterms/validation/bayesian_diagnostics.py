"""Reusable Bayesian diagnostic utilities with no election-specific claims."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np


DIAGNOSTIC_VERSION = "bayesian-diagnostics-v1"


def sample_prior_predictive(model: Any, *, draws: int = 500, seed: int = 20260901) -> Any:
    """Draw from any PyMC model's prior predictive distribution deterministically."""
    import pymc as pm

    with model:
        return pm.sample_prior_predictive(samples=int(draws), random_seed=int(seed))


def _values(data: Any, name: str) -> np.ndarray | None:
    group = getattr(data, "prior", data)
    try:
        value = group[name]
    except (KeyError, TypeError):
        return None
    return np.asarray(getattr(value, "values", value), dtype=float)


def _summary(values: np.ndarray | None) -> dict[str, Any]:
    if values is None or values.size == 0:
        return {"available": False}
    x = values[np.isfinite(values)]
    if x.size == 0:
        return {"available": False, "reason": "no finite draws"}
    return {
        "available": True,
        "n": int(x.size),
        "mean": float(np.mean(x)),
        "sd": float(np.std(x)),
        "q01": float(np.quantile(x, 0.01)),
        "q50": float(np.quantile(x, 0.50)),
        "q99": float(np.quantile(x, 0.99)),
        "max_abs": float(np.max(np.abs(x))),
    }


def prior_predictive_diagnostics(
    prior: Any,
    *,
    variable_map: Mapping[str, str] | None = None,
    extreme_margin: float = 50.0,
    max_extreme_fraction: float = 0.05,
) -> dict[str, Any]:
    """Summarize prior draws and flag, without hiding, implausible margin mass."""
    names = {
        "race_margins": "mu_final",
        "national_shock": "national",
        "house_effects": "house",
        "mode_effects": "mode_eff",
        "population_effects": "pop_eff",
        "future_movement": "future",
        "terminal_national": "terminal_nat",
        "terminal_race": "terminal_race",
        **dict(variable_map or {}),
    }
    summaries = {label: _summary(_values(prior, name)) for label, name in names.items()}
    margins = _values(prior, names["race_margins"])
    extreme_fraction = None
    if margins is not None and margins.size:
        finite = margins[np.isfinite(margins)]
        extreme_fraction = float(np.mean(np.abs(finite) > float(extreme_margin))) if finite.size else None
    flags = []
    if extreme_fraction is None:
        flags.append("race margin prior predictive is unavailable")
    elif extreme_fraction > max_extreme_fraction:
        flags.append(
            f"extreme margin fraction {extreme_fraction:.4f} exceeds {max_extreme_fraction:.4f}"
        )
    return {
        "schema_version": DIAGNOSTIC_VERSION,
        "extreme_margin_threshold": float(extreme_margin),
        "extreme_margin_fraction": extreme_fraction,
        "max_extreme_fraction": float(max_extreme_fraction),
        "summaries": summaries,
        "flags": flags,
        "ok": not flags,
    }


def posterior_predictive_checks(
    observed: np.ndarray,
    predictive: np.ndarray,
    *,
    groups: Mapping[str, np.ndarray] | None = None,
    interval: float = 0.90,
    extreme_z: float = 3.0,
) -> dict[str, Any]:
    """Generic PPC residual, coverage and grouped-residual diagnostics."""
    y = np.asarray(observed, dtype=float).ravel()
    p = np.asarray(predictive, dtype=float)
    if p.ndim != 2 or p.shape[1] != len(y):
        raise ValueError("predictive must have shape (draw, observation)")
    mean = p.mean(axis=0)
    sd = np.maximum(p.std(axis=0), 1e-9)
    residual = y - mean
    z = residual / sd
    alpha = (1.0 - float(interval)) / 2.0
    lo, hi = np.quantile(p, [alpha, 1.0 - alpha], axis=0)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for group_name, values in (groups or {}).items():
        g = np.asarray(values, dtype=object).ravel()
        if len(g) != len(y):
            raise ValueError(f"group {group_name} length differs from observed")
        rows = []
        for level in sorted({str(v) for v in g}):
            mask = np.asarray([str(v) == level for v in g])
            rows.append({
                "level": level,
                "n": int(mask.sum()),
                "mean_residual": float(np.mean(residual[mask])),
                "rmse": float(np.sqrt(np.mean(np.square(residual[mask])))),
                "coverage": float(np.mean((y[mask] >= lo[mask]) & (y[mask] <= hi[mask]))),
            })
        grouped[group_name] = rows
    return {
        "schema_version": DIAGNOSTIC_VERSION,
        "n_observations": int(len(y)),
        "residual_mean": float(np.mean(residual)),
        "residual_sd": float(np.std(residual)),
        "standardized_residual_mean": float(np.mean(z)),
        "standardized_residual_sd": float(np.std(z)),
        "extreme_residual_fraction": float(np.mean(np.abs(z) > extreme_z)),
        "interval": float(interval),
        "coverage": float(np.mean((y >= lo) & (y <= hi))),
        "groups": grouped,
    }


def run_sbc(
    *,
    prior_sampler: Callable[[np.random.Generator], Any],
    simulator: Callable[[Any, np.random.Generator], Any],
    refit: Callable[[Any, np.random.Generator], np.ndarray],
    replications: int = 20,
    seed: int = 20260901,
    credible_mass: float = 0.90,
) -> dict[str, Any]:
    """Small deterministic SBC harness; callers may provide analytic or PyMC refits."""
    rng = np.random.default_rng(seed)
    ranks: list[int] = []
    n_draws: list[int] = []
    covered: list[bool] = []
    alpha = (1.0 - credible_mass) / 2.0
    for _ in range(int(replications)):
        truth = float(prior_sampler(rng))
        observed = simulator(truth, rng)
        posterior = np.asarray(refit(observed, rng), dtype=float).ravel()
        posterior = posterior[np.isfinite(posterior)]
        if posterior.size < 2:
            raise ValueError("SBC refit must return at least two finite draws")
        ranks.append(int(np.sum(posterior < truth)))
        n_draws.append(int(posterior.size))
        lo, hi = np.quantile(posterior, [alpha, 1.0 - alpha])
        covered.append(bool(lo <= truth <= hi))
    return {
        "schema_version": "sbc-v1",
        "seed": int(seed),
        "replications": int(replications),
        "ranks": ranks,
        "posterior_draw_counts": n_draws,
        "credible_mass": float(credible_mass),
        "coverage": float(np.mean(covered)),
        "note": "Synthetic SBC checks implementation calibration; it does not validate real-data model adequacy.",
    }


def gaussian_location_sbc(
    *, replications: int = 20, observations: int = 8, posterior_draws: int = 200, seed: int = 7
) -> dict[str, Any]:
    """CI-safe conjugate-normal SBC example with an exact small-model refit."""
    prior_sd, obs_sd = 2.0, 1.5

    def prior(rng: np.random.Generator) -> float:
        return float(rng.normal(0.0, prior_sd))

    def simulate(theta: float, rng: np.random.Generator) -> np.ndarray:
        return rng.normal(theta, obs_sd, size=observations)

    def refit(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        precision = 1.0 / prior_sd**2 + observations / obs_sd**2
        variance = 1.0 / precision
        mean = variance * float(np.sum(y)) / obs_sd**2
        return rng.normal(mean, np.sqrt(variance), size=posterior_draws)

    return run_sbc(
        prior_sampler=prior, simulator=simulate, refit=refit,
        replications=replications, seed=seed,
    )
