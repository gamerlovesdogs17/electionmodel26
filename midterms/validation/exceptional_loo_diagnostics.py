"""Leave-one-poll-out influence diagnostics for sparse exceptional races."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.model.non_major_adapter import PRIOR_SD, _posterior_location


def leave_one_poll_out_diagnostics(
    polls: pd.DataFrame,
    *,
    race_id: str,
    as_of: str | date,
    prior_mean: float = 0.0,
    prior_sd: float = PRIOR_SD,
    max_polls: int = 3,
) -> dict[str, Any]:
    """Cheap LOO influence for exceptional races with <= max_polls compatible polls."""
    cutoff = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    rp = polls[polls["race_id"].astype(str) == str(race_id)].copy() if len(polls) else polls
    if rp.empty:
        return {
            "schema_version": "exceptional-loo-v1",
            "race_id": race_id,
            "n_polls": 0,
            "eligible": False,
            "reason": "no_polls",
            "polls": [],
        }
    if len(rp) > max_polls:
        return {
            "schema_version": "exceptional-loo-v1",
            "race_id": race_id,
            "n_polls": len(rp),
            "eligible": False,
            "reason": f"more_than_{max_polls}_polls",
            "polls": [],
        }

    def _fit(frame: pd.DataFrame) -> dict[str, Any]:
        post = _posterior_location(
            frame, as_of=cutoff, prior_mean=prior_mean, prior_sd=prior_sd
        )
        mean = float(post["mean"])
        sd = float(post["predictive_sd"])
        p_win = float(norm.cdf(mean / sd)) if np.isfinite(mean) and np.isfinite(sd) and sd > 0 else None
        return {
            "mean": mean,
            "sd": sd,
            "posterior_sd": float(post["posterior_sd"]),
            "p_win": p_win,
            "measurement_detail": {
                "n_polls": post["n_polls"],
                "enop": post["enop"],
                "house_effect_treatment": post.get("house_effect_treatment"),
            },
        }

    full = _fit(rp)
    rows = []
    for idx in rp.index:
        held = rp.drop(index=[idx])
        loo = _fit(held) if len(held) else {
            "mean": float(prior_mean),
            "sd": float(prior_sd),
            "posterior_sd": float(prior_sd),
            "p_win": None,
        }
        row = rp.loc[idx]
        delta_mean = float(full["mean"]) - float(loo["mean"])
        delta_p = (
            None
            if full.get("p_win") is None or loo.get("p_win") is None
            else float(full["p_win"]) - float(loo["p_win"])
        )
        # Effective measurement SD proxy from sample size / quality / partisan flag.
        try:
            n = float(row.get("sample_size") or 500.0)
        except (TypeError, ValueError):
            n = 500.0
        try:
            qw = float(row.get("quality_weight") or 1.0)
        except (TypeError, ValueError):
            qw = 1.0
        partisan = bool(row.get("partisan")) if row.get("partisan") is not None else False
        # Generic quality: partisan/internal polls get inflated measurement variance
        # when metadata marks them partisan (no directional I-correction invented).
        partisan_var_mult = 1.5 if partisan else 1.0
        meas_sd = float(np.sqrt(((100.0 / np.sqrt(max(n, 50.0))) ** 2) / max(qw, 0.05) * partisan_var_mult + 5.0**2))
        rows.append(
            {
                "poll_id": str(row.get("poll_id") or ""),
                "pollster": str(row.get("pollster_id") or row.get("pollster") or ""),
                "sponsor": str(row.get("sponsor_id") or ""),
                "partisan": partisan,
                "sample_size": row.get("sample_size"),
                "population": row.get("population"),
                "raw_candidate_margin": row.get("modeled_margin", row.get("two_party_margin")),
                "quality_score": row.get("quality_weight"),
                "house_effect_prior": row.get("house_effect_prior"),
                "effective_measurement_sd": meas_sd,
                "partisan_variance_multiplier": partisan_var_mult,
                "posterior_all_polls": {k: full[k] for k in ("mean", "sd", "p_win")},
                "posterior_excluding_poll": {k: loo[k] for k in ("mean", "sd", "p_win")},
                "delta_posterior_mean": delta_mean,
                "delta_win_probability": delta_p,
                "prior_location": float(prior_mean),
                "prior_sd": float(prior_sd),
            }
        )
    return {
        "schema_version": "exceptional-loo-v1",
        "model_version": MODEL_VERSION,
        "race_id": race_id,
        "n_polls": len(rp),
        "eligible": True,
        "prior_mean": float(prior_mean),
        "prior_sd": float(prior_sd),
        "full_posterior": {k: full[k] for k in ("mean", "sd", "p_win")},
        "polls": rows,
    }


def write_exceptional_loo_artifact(
    polls: pd.DataFrame,
    race_ids: list[str],
    *,
    as_of: str | date,
    prior_mean: float = 0.0,
    prior_sd: float = PRIOR_SD,
) -> dict[str, Any]:
    races = [
        leave_one_poll_out_diagnostics(
            polls, race_id=rid, as_of=as_of, prior_mean=prior_mean, prior_sd=prior_sd
        )
        for rid in race_ids
    ]
    payload = {
        "schema_version": "exceptional-loo-bundle-v1",
        "model_version": MODEL_VERSION,
        "races": races,
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "exceptional_loo_diagnostics_v0924.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
