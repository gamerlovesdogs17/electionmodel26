"""Poll measurement effect categories + documented fixed-effect registry (audit P1.2)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Prior means for hierarchical mode/population effects (pp on Dem−Rep margin).
# Estimated effects shrink toward these; sigma is learned in PyMC.
MODE_PRIOR: dict[str, float] = {
    "live": 0.0,
    "ivr": -0.4,
    "online": 0.3,
    "other": 0.0,
}
POP_PRIOR: dict[str, float] = {
    "LV": 0.0,
    "RV": 0.5,
    "A": 0.8,
}

MODE_ORDER = ("live", "ivr", "online", "other")
POP_ORDER = ("LV", "RV", "A")

# Effects that remain intentionally fixed (not free parameters) with rationale.
FIXED_EFFECTS_REGISTRY: dict[str, dict[str, Any]] = {
    "prior_lean_scale": {
        "value": 1.0,
        "rationale": "Identity map of vintaged lean into margin units; lean construction is the calibrated object.",
        "validated_by": "official ballot + certified margins; not a free slope in production",
    },
    "recency_half_life_days": {
        "value": 28.0,
        "rationale": "Influence weight half-life; nested lead-time grid can ablate 14/28/45.",
        "validated_by": "lead_time_grid / influence weight diagnostics",
    },
    "max_pollster_share": {
        "value": 0.35,
        "rationale": "Soft cap so one firm cannot dominate a race's ENOP.",
        "validated_by": "poll_weights unit tests + ENOP monitor",
    },
}


def mode_category(mode: object) -> str:
    if mode is None or (isinstance(mode, float) and np.isnan(mode)):
        return "other"
    m = str(mode).lower()
    if "live" in m:
        return "live"
    if "ivr" in m:
        return "ivr"
    if "online" in m or "web" in m:
        return "online"
    return "other"


def population_category(population: object) -> str:
    if population is None or (isinstance(population, float) and np.isnan(population)):
        return "LV"
    p = str(population).upper()
    if "LV" in p:
        return "LV"
    if "RV" in p or "REGISTERED" in p:
        return "RV"
    if p in {"A", "ADULT", "ADULTS"} or "ADULT" in p:
        return "A"
    return "LV"


def encode_mode_pop(polls: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (mode_idx, pop_idx, mode_prior_vec, pop_prior_vec)."""
    if polls is None or len(polls) == 0:
        return (
            np.zeros(0, dtype=int),
            np.zeros(0, dtype=int),
            np.array([MODE_PRIOR[m] for m in MODE_ORDER], dtype=float),
            np.array([POP_PRIOR[p] for p in POP_ORDER], dtype=float),
        )
    mode_idx = np.array(
        [MODE_ORDER.index(mode_category(v)) for v in polls.get("mode", pd.Series([None] * len(polls)))],
        dtype=int,
    )
    pop_idx = np.array(
        [
            POP_ORDER.index(population_category(v))
            for v in polls.get("population", pd.Series(["LV"] * len(polls)))
        ],
        dtype=int,
    )
    return (
        mode_idx,
        pop_idx,
        np.array([MODE_PRIOR[m] for m in MODE_ORDER], dtype=float),
        np.array([POP_PRIOR[p] for p in POP_ORDER], dtype=float),
    )


def fixed_mode_offset(mode: object) -> float:
    """Legacy point estimate (prior mean) — prefer hierarchical effects in PyMC."""
    return float(MODE_PRIOR[mode_category(mode)])


def fixed_population_offset(population: object) -> float:
    """Legacy point estimate (prior mean) — prefer hierarchical effects in PyMC."""
    return float(POP_PRIOR[population_category(population)])
