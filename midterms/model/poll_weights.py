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

    ENOP = (sum w)^2 / sum(w^2)  — effective number of independent poll signals.
    Within a race, prolific pollsters are soft-capped and shared study_id rows
    are down-weighted so repeated releases add sublinear information.
    Per-race max/median weight ratio is capped so a single recent poll cannot
    dominate (~half-life 28d default).

    Weights use recency, sample size, quality, and partisan status only.
    Mode / population corrections are applied as additive offsets in the
    measurement model (see midterms.model.pymc_model), not here — avoiding
    double-counting the same information as both bias and weight.
    """
    if polls.empty:
        out = polls.copy()
        out["influence_weight"] = pd.Series(dtype=float)
        out["enop_race"] = pd.Series(dtype=float)
        return out

    out = polls.reset_index(drop=True).copy()
    as_of_ts = pd.Timestamp(as_of)
    field_end = pd.to_datetime(out["field_end"], errors="coerce")
    age = (as_of_ts - field_end).dt.days.clip(lower=0).astype(float)
    recency = np.exp(-np.log(2.0) * age / max(half_life_days, 1.0))

    n_raw = pd.to_numeric(out["sample_size"], errors="coerce")
    n = n_raw.where(np.isfinite(n_raw) & (n_raw > 0), 500.0).clip(lower=50.0)
    size_w = np.sqrt(n / 600.0).clip(0.35, 2.0)

    qw = (
        out["quality_weight"].astype(float)
        if "quality_weight" in out.columns
        else pd.Series(np.ones(len(out)), index=out.index)
    ).fillna(1.0).clip(0.2, 1.25)

    partisan = out["partisan"].fillna(False).astype(bool) if "partisan" in out.columns else False
    partisan_w = np.where(partisan, 0.35, 1.0)

    # Mode / population enter as additive measurement offsets in the likelihood
    # (pymc_model._mode_offset / _population_offset) — not again as weights.
    raw = recency.to_numpy() * size_w.to_numpy() * qw.to_numpy() * partisan_w
    raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
    out["raw_weight"] = raw

    capped = raw.copy()
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
        # Cap extreme within-race weights (single recent poll domination)
        pos = w[w > 0]
        if len(pos) and max_weight_ratio > 0:
            med = float(np.median(pos))
            if med > 0:
                w = np.minimum(w, med * float(max_weight_ratio))
        s = float(w.sum())
        ss = float(np.square(w).sum())
        race_enop = (s * s / ss) if ss > 0 else 0.0
        enop[ix] = race_enop
        mean_w = float(w.mean()) if len(w) else 1.0
        if mean_w > 0:
            normed[ix] = w / mean_w
        else:
            normed[ix] = w

    out["enop_race"] = enop
    out["influence_weight"] = np.nan_to_num(normed, nan=0.0, posinf=0.0, neginf=0.0)
    return out


def race_enop_summary(polls: pd.DataFrame) -> dict[str, float]:
    """Map race_id → ENOP for diagnostics."""
    if polls.empty or "enop_race" not in polls.columns:
        return {}
    return {
        str(rid): float(g["enop_race"].iloc[0])
        for rid, g in polls.groupby("race_id")
    }


def global_enop(polls: pd.DataFrame) -> float:
    if polls.empty or "influence_weight" not in polls.columns:
        return 0.0
    w = polls["influence_weight"].astype(float).to_numpy()
    s = float(w.sum())
    ss = float(np.square(w).sum())
    return float(s * s / ss) if ss > 0 else 0.0
