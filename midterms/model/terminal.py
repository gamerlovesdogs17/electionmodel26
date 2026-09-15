"""Election-Day terminal error layers (audit P1.3 / Finding 4).

Budget: national + race/state + demographic-similarity shocks that do not
contract with calendar. Defaults keep total variance near the legacy single
common terminal (~2.5 pp): nat² + race² + sim² ≈ 6.25.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import ARTIFACTS_DIR
from midterms.model.similarity import correlated_shocks

# Documented source defaults (overridden by sealed calibration JSON when present).
TERMINAL_NAT_SD = 1.8
TERMINAL_RACE_SD = 1.8
SIM_SCALE = 1.0
LENGTH_SCALE = 1.75
NU = 5.0

CALIBRATED_DEFAULTS_PATH = ARTIFACTS_DIR / "terminal_defaults.json"


def _load_persisted_defaults() -> None:
    global TERMINAL_NAT_SD, TERMINAL_RACE_SD, SIM_SCALE, LENGTH_SCALE, NU
    if not CALIBRATED_DEFAULTS_PATH.exists():
        return
    try:
        payload = json.loads(CALIBRATED_DEFAULTS_PATH.read_text(encoding="utf-8"))
        scales = payload.get("scales") or payload
        if "terminal_nat_sd" in scales:
            TERMINAL_NAT_SD = float(scales["terminal_nat_sd"])
        if "terminal_race_sd" in scales:
            TERMINAL_RACE_SD = float(scales["terminal_race_sd"])
        if "sim_scale" in scales:
            SIM_SCALE = float(scales["sim_scale"])
        if "length_scale" in scales:
            LENGTH_SCALE = float(scales["length_scale"])
        if "nu" in scales:
            NU = float(scales["nu"])
    except Exception:  # noqa: BLE001
        return


_load_persisted_defaults()


def scales_kwargs(d: dict[str, float] | None) -> dict[str, float]:
    """Filter a dict to recognized terminal scale keys."""
    if not d:
        return {}
    keys = {"terminal_nat_sd", "terminal_race_sd", "sim_scale", "length_scale", "nu"}
    return {k: float(v) for k, v in d.items() if k in keys and v is not None}


def active_scales(
    *,
    terminal_nat_sd: float | None = None,
    terminal_race_sd: float | None = None,
    sim_scale: float | None = None,
    length_scale: float | None = None,
    nu: float | None = None,
) -> dict[str, float]:
    return {
        "terminal_nat_sd": float(TERMINAL_NAT_SD if terminal_nat_sd is None else terminal_nat_sd),
        "terminal_race_sd": float(TERMINAL_RACE_SD if terminal_race_sd is None else terminal_race_sd),
        "sim_scale": float(SIM_SCALE if sim_scale is None else sim_scale),
        "length_scale": float(LENGTH_SCALE if length_scale is None else length_scale),
        "nu": float(NU if nu is None else nu),
    }


def error_budget_block(scales: dict[str, float] | None = None) -> dict[str, Any]:
    s = scales or active_scales()
    rss = float(
        np.sqrt(s["terminal_nat_sd"] ** 2 + s["terminal_race_sd"] ** 2 + s["sim_scale"] ** 2)
    )
    return {
        "terminal_layers": "national+race+similarity",
        "terminal_nat_sd": s["terminal_nat_sd"],
        "terminal_race_sd": s["terminal_race_sd"],
        "similarity_terminal_sd": s["sim_scale"],
        "similarity_length_scale": s["length_scale"],
        "terminal_nu": s["nu"],
        "terminal_rss": rss,
        "legacy_common_terminal_sd": 2.5,
        "note": (
            "Layered Election-Day terminal (audit P1.3). Similarity applied as "
            "post-draw correlated Student-t shocks; nat/race may be in-model."
        ),
    }


def add_terminal_layers(
    draws: np.ndarray,
    races: pd.DataFrame,
    rng: np.random.Generator,
    *,
    terminal_nat_sd: float | None = None,
    terminal_race_sd: float | None = None,
    sim_scale: float | None = None,
    length_scale: float | None = None,
    nu: float | None = None,
    include_nat: bool = True,
    include_race: bool = True,
    include_similarity: bool = True,
) -> np.ndarray:
    """
    Add terminal ED layers to margin draws (n_draws, n_races).

    National: common Student-t shock. Race: independent Student-t. Similarity:
    continuous RBF covariance via ``correlated_shocks``.
    """
    out = np.asarray(draws, dtype=float).copy()
    if out.ndim != 2:
        raise ValueError("draws must be (n_draws, n_races)")
    n_draws, n_races = out.shape
    if n_races == 0 or n_draws == 0:
        return out
    if len(races) != n_races:
        raise ValueError(f"races length {len(races)} != n_races {n_races}")

    s = active_scales(
        terminal_nat_sd=terminal_nat_sd,
        terminal_race_sd=terminal_race_sd,
        sim_scale=sim_scale,
        length_scale=length_scale,
        nu=nu,
    )
    nu_t = max(float(s["nu"]), 2.1)

    if include_nat and s["terminal_nat_sd"] > 0:
        out += rng.standard_t(nu_t, size=(n_draws, 1)) * s["terminal_nat_sd"]
    if include_race and s["terminal_race_sd"] > 0:
        out += rng.standard_t(nu_t, size=(n_draws, n_races)) * s["terminal_race_sd"]
    if include_similarity and s["sim_scale"] > 0:
        out += correlated_shocks(
            races,
            n_draws,
            rng,
            scale=s["sim_scale"],
            length_scale=s["length_scale"],
            nu=nu_t,
        )
    return out


def add_similarity_terminal(
    draws: np.ndarray,
    races: pd.DataFrame,
    rng: np.random.Generator,
    **kwargs: Any,
) -> np.ndarray:
    """Post-draw similarity terminal only (nat/race already in PyMC)."""
    return add_terminal_layers(
        draws,
        races,
        rng,
        include_nat=False,
        include_race=False,
        include_similarity=True,
        **kwargs,
    )


def set_defaults_from_calibration(scales: dict[str, float]) -> None:
    """Update module defaults after a successful nested calibration gate."""
    global TERMINAL_NAT_SD, TERMINAL_RACE_SD, SIM_SCALE, LENGTH_SCALE, NU
    if "terminal_nat_sd" in scales:
        TERMINAL_NAT_SD = float(scales["terminal_nat_sd"])
    if "terminal_race_sd" in scales:
        TERMINAL_RACE_SD = float(scales["terminal_race_sd"])
    if "sim_scale" in scales:
        SIM_SCALE = float(scales["sim_scale"])
    if "length_scale" in scales:
        LENGTH_SCALE = float(scales["length_scale"])
    if "nu" in scales:
        NU = float(scales["nu"])
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATED_DEFAULTS_PATH.write_text(
        json.dumps(
            {
                "scales": active_scales(),
                "source": "covariance_calibration",
                "note": "Persisted winning scales so subsequent processes load the same defaults.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
