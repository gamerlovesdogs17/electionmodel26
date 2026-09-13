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


def shift_draws_to_means(draws: np.ndarray, new_means: np.ndarray) -> np.ndarray:
    """Preserve joint shocks while moving location to `new_means`."""
    old = draws.mean(axis=0)
    return draws - old + new_means


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
) -> dict[str, Any]:
    return {
        "ratings": {"enabled": used_ratings, "weight": rating_weight},
        "markets": {"enabled": used_markets, "weight": market_weight},
        "note": "Overlays are optional; unadjusted hierarchical core remains in diagnostics when disabled.",
    }
