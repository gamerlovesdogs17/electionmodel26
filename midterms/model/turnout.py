"""Auxiliary turnout / multi-candidate translation (blueprint §7.3)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TurnoutLayerConfig:
    enabled: bool = False
    version: str = "turnout-interface-v1"
    propagate_to_primary_simulation: bool = False


class TurnoutModel(Protocol):
    def draw(self, races: pd.DataFrame, *, n_draws: int, seed: int) -> dict[str, Any]: ...


def run_turnout_interface(
    races: pd.DataFrame,
    *,
    config: TurnoutLayerConfig | None = None,
    model: TurnoutModel | None = None,
    n_draws: int = 0,
    seed: int = 0,
) -> dict[str, Any]:
    """Opt-in future interface; the current auxiliary foil cannot alter seat math."""
    cfg = config or TurnoutLayerConfig()
    if not cfg.enabled:
        return {
            "config": asdict(cfg),
            "status": "disabled",
            "primary_simulation_affected": False,
        }
    if model is None:
        raise ValueError("enabled turnout layer requires a validated model implementation")
    if cfg.propagate_to_primary_simulation:
        raise ValueError("turnout propagation is not validated for production")
    result = model.draw(races, n_draws=int(n_draws), seed=int(seed))
    return {
        "config": asdict(cfg),
        "status": "auxiliary_only",
        "primary_simulation_affected": False,
        "draws": result,
    }


def undecided_allocation(
    races: pd.DataFrame,
    mean_margin: np.ndarray,
    *,
    undecided_share: float = 5.0,
    other_share: float = 2.0,
) -> list[dict[str, Any]]:
    """
    Map two-party margin → illustrative all-candidate shares.

    Undecideds lean toward the trailing side slightly (classic late-decider foil);
    `other` is a residual that does not flip two-party winners in the core sim.
    """
    out = []
    for i, (_, row) in enumerate(races.iterrows()):
        m = float(mean_margin[i]) if i < len(mean_margin) else 0.0
        dem_tw = 50.0 + m / 2.0
        rep_tw = 100.0 - dem_tw
        # Soft late-decider: undecideds tilt 55/45 toward trailing major party
        trail_dem = dem_tw < rep_tw
        u_dem = undecided_share * (0.55 if trail_dem else 0.45)
        u_rep = undecided_share - u_dem
        dem = dem_tw * (1 - (undecided_share + other_share) / 100.0) + u_dem
        rep = rep_tw * (1 - (undecided_share + other_share) / 100.0) + u_rep
        oth = other_share
        tot = dem + rep + oth
        out.append(
            {
                "race_id": row["race_id"],
                "state": row["state"],
                "dem_share_all": round(100.0 * dem / tot, 2),
                "rep_share_all": round(100.0 * rep / tot, 2),
                "other_share_all": round(100.0 * oth / tot, 2),
                "undecided_model": undecided_share,
            }
        )
    return out


def turnout_layer(
    races: pd.DataFrame,
    *,
    base_turnout: float = 0.52,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Auxiliary turnout foil; it does not propagate into primary seat math."""
    rng = np.random.default_rng(seed)
    rows = []
    for _, row in races.iterrows():
        urban = float(row.get("demo_urban") or 0.7)
        t = float(np.clip(base_turnout + 0.06 * (urban - 0.7) + rng.normal(0, 0.015), 0.35, 0.75))
        rows.append(
            {
                "race_id": row["race_id"],
                "state": row["state"],
                "expected_turnout": round(t, 3),
                "turnout_sd": 0.03,
            }
        )
    return rows
