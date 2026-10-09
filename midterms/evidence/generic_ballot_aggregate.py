"""Shared generic-ballot aggregation for live VoteHub and historical archives.

Live and historical resolvers must use the same statistical summary. The only
difference is which point-in-time rows are eligible.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np

DEFAULT_WINDOW_DAYS = 21
DEFAULT_WINSOR_ABS = 8.0
AGGREGATION_CONFIG_ID = "trailing_weighted_headline_v1"


def aggregate_generic_ballot(
    poll_rows: list[dict[str, Any]],
    *,
    as_of: str | date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    winsor_abs: float = DEFAULT_WINSOR_ABS,
) -> dict[str, Any] | None:
    """Aggregate Dem−Rep headline margins with recency × √N weights.

    Expected row keys (missing values use explicit defaults, not a hidden formula):
      - margin (headline Dem−Rep pp) **or** dem + rep shares
      - field_end (ISO date)
      - available_at (ISO date; required for eligibility filtering upstream)
      - sample_size (optional; default 1000)
      - poll_id (optional; returned in lineage)
    """
    cutoff = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    rows: list[dict[str, Any]] = []
    for raw in poll_rows:
        margin = raw.get("margin")
        if margin is None:
            dem = raw.get("dem")
            rep = raw.get("rep")
            if dem is None or rep is None:
                continue
            margin = float(dem) - float(rep)
        end = str(raw.get("field_end") or raw.get("end") or "")[:10]
        if not end:
            continue
        try:
            end_d = date.fromisoformat(end)
        except ValueError:
            continue
        sample = raw.get("sample_size")
        try:
            n = float(sample) if sample is not None else 1000.0
        except (TypeError, ValueError):
            n = 1000.0
        if not np.isfinite(n) or n <= 0:
            n = 1000.0
        rows.append(
            {
                "end": end,
                "end_d": end_d,
                "margin_headline": float(margin),
                "n": n,
                "poll_id": str(raw.get("poll_id") or raw.get("source_id") or ""),
                "available_at": str(raw.get("available_at") or "")[:10],
                "pollster": str(raw.get("pollster") or ""),
            }
        )
    if not rows:
        return None

    windowed = [
        r
        for r in rows
        if 0 <= (cutoff - r["end_d"]).days <= window_days
    ]
    if not windowed:
        windowed = sorted(rows, key=lambda r: r["end"], reverse=True)[:5]

    margins: list[float] = []
    weights: list[float] = []
    for r in windowed:
        m = float(np.clip(r["margin_headline"], -winsor_abs, winsor_abs))
        age = max((cutoff - r["end_d"]).days, 0)
        recency = math.exp(-math.log(2.0) * age / max(window_days / 2.0, 1.0))
        w = recency * math.sqrt(max(r["n"], 100.0) / 1000.0)
        margins.append(m)
        weights.append(w)
    w_arr = np.asarray(weights, dtype=float)
    m_arr = np.asarray(margins, dtype=float)
    if float(w_arr.sum()) <= 0:
        margin_out = float(np.median(m_arr))
    else:
        margin_out = float(np.average(m_arr, weights=w_arr))
    return {
        "margin": margin_out,
        "n_polls": len(windowed),
        "window_days": window_days,
        "winsor_abs": winsor_abs,
        "as_of": cutoff.isoformat(),
        "ref_date": cutoff.isoformat(),
        "raw_median": float(np.median(m_arr)),
        "raw_mean": float(np.mean(m_arr)),
        "method": AGGREGATION_CONFIG_ID,
        "poll_ids": [r["poll_id"] for r in windowed if r["poll_id"]],
        "field_dates": [r["end"] for r in windowed],
        "available_at_dates": [r["available_at"] for r in windowed if r["available_at"]],
        "note": (
            "Headline D-R pp (not two-party renorm); Winsorized; "
            "recency*sqrt(N) weights; shared live/historical aggregator"
        ),
    }


__all__ = [
    "AGGREGATION_CONFIG_ID",
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_WINSOR_ABS",
    "aggregate_generic_ballot",
]
