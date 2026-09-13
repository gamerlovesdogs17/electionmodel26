"""Restrained congressional fundamentals prior (Election-Day anchor)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def fundamentals_mean(races: pd.DataFrame, generic_ballot: float = 0.0) -> pd.Series:
    """
    Parsimonious structural mean margin (dem - rep pp).

    prior_lean + 0.35 * generic_ballot + incumbency bump.
    Wired as the Election-Day prior mean inside the hierarchical model — not a
    second additive correction on top of the same evidence.
    """
    contested = races[~races["not_up"]].copy()
    mu = contested["prior_lean"].astype(float) + 0.35 * float(generic_ballot)
    inc = contested["incumbent_party"]
    open_mask = contested["is_open"].astype(bool)
    bump = np.where(open_mask, 0.0, np.where(inc == "D", 2.0, np.where(inc == "R", -2.0, 0.0)))
    return pd.Series(mu.to_numpy() + bump, index=contested["race_id"], name="fundamentals_mean")
