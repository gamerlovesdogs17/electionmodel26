"""Optional expert-rating and prediction-market overlays (ablatable)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# Full IE-style ladder. Cook "Safe" maps to Solid; Tilt is first-class (not Lean).
RATING_ORDER = (
    "Solid D",
    "Likely D",
    "Lean D",
    "Tilt D",
    "Tossup",
    "Tilt R",
    "Lean R",
    "Likely R",
    "Solid R",
)

# Soft margin anchors (Dem−Rep pp) implied by categorical ratings.
RATING_MARGIN = {
    "Solid D": 18.0,
    "Likely D": 10.0,
    "Lean D": 4.5,
    "Tilt D": 2.0,
    "Tossup": 0.0,
    "Tilt R": -2.0,
    "Lean R": -4.5,
    "Likely R": -10.0,
    "Solid R": -18.0,
}


def rating_from_probability(p_dem: float) -> str:
    """Map P(Dem caucus win) onto the Solid/Likely/Lean/Tilt/Tossup ladder."""
    p = float(p_dem)
    if p >= 0.92:
        return "Solid D"
    if p >= 0.78:
        return "Likely D"
    if p >= 0.62:
        return "Lean D"
    if p >= 0.55:
        return "Tilt D"
    if p >= 0.45:
        return "Tossup"
    if p >= 0.38:
        return "Tilt R"
    if p >= 0.22:
        return "Lean R"
    if p >= 0.08:
        return "Likely R"
    return "Solid R"


def ratings_table_from_forecasts(race_summaries: list[dict]) -> pd.DataFrame:
    rows = []
    for s in race_summaries:
        rows.append(
            {
                "race_id": s["race_id"],
                "state": s["state"],
                "rating": s.get("rating") or rating_from_probability(s["p_dem"]),
                "source": "model_derived",
            }
        )
    return pd.DataFrame(rows)


def apply_rating_overlay(
    mean_margin: np.ndarray,
    race_ids: list[str],
    ratings: pd.DataFrame,
    *,
    weight: float = 0.15,
) -> np.ndarray:
    """Shrink race means toward rating-implied margins. weight=0 leaves unchanged."""
    if weight <= 0 or ratings is None or ratings.empty:
        return mean_margin
    by_id = {}
    for _, r in ratings.iterrows():
        label = str(r["rating"])
        if label not in RATING_MARGIN:
            continue
        by_id[str(r["race_id"])] = float(RATING_MARGIN[label])
    out = mean_margin.copy()
    w = float(np.clip(weight, 0.0, 1.0))
    for i, rid in enumerate(race_ids):
        if rid in by_id:
            out[i] = (1.0 - w) * out[i] + w * by_id[rid]
    return out


def apply_market_overlay(
    mean_margin: np.ndarray,
    race_ids: list[str],
    markets: pd.DataFrame,
    *,
    weight: float = 0.10,
) -> np.ndarray:
    """
    Blend toward market-implied margins.

    `markets` columns: race_id, p_dem (0-1), optional liquidity in [0,1].
    Convert p_dem → margin via a mild logistic inverse around ±12pp at 90%.
    """
    if weight <= 0 or markets is None or markets.empty:
        return mean_margin
    out = mean_margin.copy()
    w0 = float(np.clip(weight, 0.0, 1.0))
    by = markets.set_index("race_id")
    for i, rid in enumerate(race_ids):
        if rid not in by.index:
            continue
        row = by.loc[rid]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        p = float(np.clip(row["p_dem"], 0.02, 0.98))
        # Rough pp mapping: logit scale
        implied = 4.0 * np.log(p / (1.0 - p))  # ~±12pp at ~95%
        liq = float(row["liquidity"]) if "liquidity" in by.columns and pd.notna(row.get("liquidity")) else 1.0
        w = w0 * float(np.clip(liq, 0.0, 1.0))
        out[i] = (1.0 - w) * out[i] + w * implied
    return out


def apply_control_market_overlay(
    mean_margin: np.ndarray,
    *,
    control_p_dem: float | None,
    weight: float = 0.25,
) -> np.ndarray:
    """
    Soft national location pull toward chamber-control market environment.

    Maps Kalshi CONTROLS p_dem → an implied national Dem margin via a mild
    logit, then shifts all race means by `weight * (market_nat − model_nat)`.
    Does not replace race markets; it only debiases the national level when
    control is priced near a coin flip and the model is not.
    """
    if weight <= 0 or control_p_dem is None:
        return mean_margin
    try:
        p = float(control_p_dem)
    except (TypeError, ValueError):
        return mean_margin
    if not np.isfinite(p):
        return mean_margin
    p = float(np.clip(p, 0.05, 0.95))
    market_nat = 6.0 * float(np.log(p / (1.0 - p)))  # ~0 at 50%, ~±4 at 65/35
    finite = mean_margin[np.isfinite(mean_margin)]
    if len(finite) == 0:
        return mean_margin
    # Competitive races dominate the control estimand
    competitive = finite[np.abs(finite) < 15.0]
    model_nat = float(np.mean(competitive if len(competitive) else finite))
    w = float(np.clip(weight, 0.0, 1.0))
    shift = w * (market_nat - model_nat)
    out = mean_margin.astype(float, copy=True) + shift
    return out


def calibrate_draws_to_control(
    draws: np.ndarray,
    *,
    held_dem: int,
    control_p_dem: float | None,
    weight: float = 0.55,
    vp_tiebreak_party: str = "R",
    n_steps: int = 5,
) -> tuple[np.ndarray, dict[str, Any]]:
    """
    Shared national shift so joint P(Dem control) moves toward CONTROLS market.

    `weight` blends toward the target (1.0 = match market; 0 = unchanged).
    Preserves relative race structure / correlation.
    """
    meta: dict[str, Any] = {
        "enabled": False,
        "weight": float(weight),
        "target_p_dem": control_p_dem,
        "shift_pp": 0.0,
    }
    if weight <= 0 or control_p_dem is None or draws.size == 0:
        return draws, meta
    try:
        target = float(np.clip(float(control_p_dem), 0.05, 0.95))
    except (TypeError, ValueError):
        return draws, meta

    def _p_control(arr: np.ndarray) -> float:
        dem_seats = held_dem + (arr > 0).sum(axis=1)
        if vp_tiebreak_party == "D":
            return float((dem_seats >= 50).mean())
        return float((dem_seats >= 51).mean())

    out = draws.astype(float, copy=True)
    p0 = _p_control(out)
    aim = (1.0 - float(np.clip(weight, 0.0, 1.0))) * p0 + float(np.clip(weight, 0.0, 1.0)) * target
    shift = 0.0
    for _ in range(max(int(n_steps), 1)):
        p_now = _p_control(out + shift)
        err = aim - p_now
        if abs(err) < 0.01:
            break
        shift += float(np.clip(err * 10.0, -5.0, 5.0))
    out = out + shift
    meta.update(
        {
            "enabled": True,
            "p_before": p0,
            "p_after": _p_control(out),
            "aim_p_dem": float(aim),
            "shift_pp": float(shift),
        }
    )
    return out, meta


def shift_draws_to_means(draws: np.ndarray, new_means: np.ndarray) -> np.ndarray:
    """Preserve joint shocks while moving location to `new_means`."""
    old = draws.mean(axis=0)
    # Avoid NaN propagation when a race column is all-NaN
    old = np.where(np.isfinite(old), old, 0.0)
    new = np.where(np.isfinite(new_means), new_means, old)
    return draws - old + new


def markets_from_probabilities(
    race_summaries: list[dict], *, liquidity: float = 0.4
) -> pd.DataFrame:
    """Placeholder market layer seeded from model probs (for ablation plumbing)."""
    return pd.DataFrame(
        [
            {
                "race_id": s["race_id"],
                "p_dem": float(s["p_dem"]),
                "liquidity": liquidity,
                "source": "model_seed_placeholder",
            }
            for s in race_summaries
        ]
    )


def overlay_report(
    *,
    used_ratings: bool,
    used_markets: bool,
    rating_weight: float,
    market_weight: float,
    control_weight: float = 0.0,
    used_control: bool = False,
) -> dict[str, Any]:
    return {
        "ratings": {"enabled": used_ratings, "weight": rating_weight},
        "markets": {"enabled": used_markets, "weight": market_weight},
        "control_market": {"enabled": used_control, "weight": control_weight},
        "note": "Overlays are optional; unadjusted hierarchical core remains in diagnostics when disabled.",
    }
