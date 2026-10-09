"""Poll influence weights: recency, quality, pollster caps, study clustering, ENOP."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def attach_poll_weights(
    polls: pd.DataFrame,
    *,
    as_of: date,
    half_life_days: float = 28.0,
    max_pollster_share: float = 0.35,
    study_cluster_power: float = 0.55,
    max_weight_ratio: float = 8.0,
) -> pd.DataFrame:
    """
    Attach normalized influence weights and ENOP diagnostics.

    Blueprint-faithful information entry (v0.9.24):
    - Sample size and pollster quality enter the measurement variance once in
      the likelihood / state-space filter — not again through influence weights.
    - Absolute calendar recency is preserved: within-race renormalization applies
      only to non-recency factors, then absolute recency is multiplied back so a
      lone 100-day-old poll stays weaker than a 10-day-old poll in another race.
    - Pollster caps and shared-study clustering remain relative adjustments.
    """
    if polls.empty:
        out = polls.copy()
        out["influence_weight"] = pd.Series(dtype=float)
        out["enop_race"] = pd.Series(dtype=float)
        out["absolute_recency"] = pd.Series(dtype=float)
        return out

    out = polls.reset_index(drop=True).copy()
    as_of_ts = pd.Timestamp(as_of)
    field_end = pd.to_datetime(out["field_end"], errors="coerce")
    age = (as_of_ts - field_end).dt.days.clip(lower=0).astype(float)
    recency = np.exp(-np.log(2.0) * age / max(half_life_days, 1.0))
    out["absolute_recency"] = recency.to_numpy()

    if "partisan" in out.columns:
        partisan = out["partisan"].fillna(False).astype(bool).to_numpy()
    else:
        partisan = np.zeros(len(out), dtype=bool)
    partisan_w = np.where(partisan, 0.35, 1.0)

    # Non-recency factors only — n and quality intentionally excluded here.
    structural = np.asarray(partisan_w, dtype=float)
    structural = np.nan_to_num(structural, nan=0.0, posinf=0.0, neginf=0.0)
    out["raw_weight"] = structural * recency.to_numpy()

    capped = structural.copy()
    for _, g in out.groupby("race_id", sort=False):
        ix = g.index.to_numpy()
        race_raw = capped[ix]
        total = float(race_raw.sum())
        if total <= 0:
            continue
        for _, pg in g.groupby("pollster_id", sort=False):
            pix = pg.index.to_numpy()
            psum = float(capped[pix].sum())
            share = psum / total
            if share > max_pollster_share and psum > 0:
                scale = max((max_pollster_share * total) / psum, 0.15)
                capped[pix] = capped[pix] * scale

    clustered = capped.copy()
    if "study_id" in out.columns:
        for _, sg in out.groupby("study_id", sort=False):
            six = sg.index.to_numpy()
            if len(six) <= 1:
                continue
            factor = len(six) ** study_cluster_power
            clustered[six] = clustered[six] / factor

    enop = np.zeros(len(out), dtype=float)
    normed = clustered.copy()
    for _, g in out.groupby("race_id", sort=False):
        ix = g.index.to_numpy()
        w = clustered[ix].astype(float, copy=True)
        pos = w[w > 0]
        if len(pos) and max_weight_ratio > 0:
            med = float(np.median(pos))
            if med > 0:
                w = np.minimum(w, med * float(max_weight_ratio))
        # Normalize only non-recency structure within the race...
        mean_w = float(w.mean()) if len(w) else 1.0
        if mean_w > 0:
            structural_norm = w / mean_w
        else:
            structural_norm = w
        # ...then restore absolute calendar recency so age is not erased.
        final = structural_norm * recency.to_numpy()[ix]
        s = float(final.sum())
        ss = float(np.square(final).sum())
        race_enop = (s * s / ss) if ss > 0 else 0.0
        enop[ix] = race_enop
        normed[ix] = final

    out["enop_race"] = enop
    out["influence_weight"] = np.nan_to_num(normed, nan=0.0, posinf=0.0, neginf=0.0)
    return out


def race_enop_summary(polls: pd.DataFrame) -> dict[str, float]:
    """Map race_id → ENOP for diagnostics."""
    if polls.empty or "enop_race" not in polls.columns:
        return {}
    return {
        str(rid): float(g["enop_race"].iloc[0])
        for rid, g in polls.groupby("race_id", sort=False)
    }


def global_enop(polls: pd.DataFrame) -> float:
    if polls.empty or "influence_weight" not in polls.columns:
        return 0.0
    w = polls["influence_weight"].to_numpy(dtype=float)
    w = w[np.isfinite(w) & (w > 0)]
    if not len(w):
        return 0.0
    s = float(w.sum())
    ss = float(np.square(w).sum())
    return float(s * s / ss) if ss > 0 else 0.0
