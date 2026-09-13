"""Nested fundamentals coefficient estimation + stability report (audit P1.2)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, CYCLES
from midterms.model.effects import FIXED_EFFECTS_REGISTRY, MODE_PRIOR, POP_PRIOR
from midterms.model.fundamentals import (
    FIXED_COEF_RATIONALE,
    PRIOR_COEF,
    estimate_coefs_nested,
    set_coefs,
)


def run_coefficient_stability(
    *,
    years: tuple[int, ...] | None = None,
    ridge_lambda: float = 25.0,
    out_path: Path | None = None,
    apply_full_sample: bool = False,
) -> dict[str, Any]:
    """
    For each holdout cycle, estimate LOO-shrunk fundamentals coefs and report
    deltas vs PRIOR. Optionally refresh working COEF from all-cycle estimate.
    """
    years = years or tuple(y for y in CYCLES if y >= 2018)
    by_holdout: dict[str, Any] = {}
    for year in years:
        est = estimate_coefs_nested(holdout_year=int(year), ridge_lambda=ridge_lambda)
        by_holdout[str(year)] = {
            "coefs": est["coefs"],
            "delta_vs_prior": est.get("delta_vs_prior", {}),
            "n_rows": est["n_rows"],
            "train_years": est["train_years"],
        }

    full = estimate_coefs_nested(holdout_year=None, train_years=years, ridge_lambda=ridge_lambda)
    if apply_full_sample and full.get("n_rows"):
        set_coefs(full["coefs"])

    # Dispersion of LOO estimates across holdouts
    keys = list(PRIOR_COEF.keys())
    dispersion: dict[str, Any] = {}
    for k in keys:
        vals = [float(by_holdout[str(y)]["coefs"][k]) for y in years if str(y) in by_holdout]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(len(vals), 1)
        dispersion[k] = {
            "loo_mean": mean,
            "loo_sd": var**0.5,
            "prior": float(PRIOR_COEF[k]),
            "full_sample": float(full["coefs"].get(k, PRIOR_COEF[k])),
        }

    report = {
        "audit_item": "P1.2",
        "ridge_lambda": ridge_lambda,
        "prior_coefs": dict(PRIOR_COEF),
        "full_sample": full,
        "by_holdout": by_holdout,
        "dispersion": dispersion,
        "poll_measurement_priors": {
            "mode": dict(MODE_PRIOR),
            "population": dict(POP_PRIOR),
            "estimation": (
                "Hierarchical Normal(prior, sigma) in fit_pymc / fit_pymc_dynamic; "
                "sigma_mode / sigma_pop learned. Fast/state-space use prior means."
            ),
        },
        "remaining_fixed_effects": {
            **FIXED_COEF_RATIONALE,
            **{k: v["rationale"] for k, v in FIXED_EFFECTS_REGISTRY.items()},
        },
        "note": (
            "Fundamentals: nested leave-one-cycle ridge toward PRIOR_COEF. "
            "Poll mode/population: hierarchical in PyMC (no pre-subtracted fixed offsets). "
            "Any remaining fixed effect is listed with rationale."
        ),
    }
    out_path = out_path or (ARTIFACTS_DIR / "coefficient_stability.json")
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    report["path"] = str(out_path)
    return report


def with_nested_coefs(holdout_year: int, *, ridge_lambda: float = 25.0):
    """Context-style helper: patch COEF to LOO estimate for ``holdout_year``."""
    est = estimate_coefs_nested(holdout_year=holdout_year, ridge_lambda=ridge_lambda)
    old = set_coefs(est["coefs"])
    return old, est
