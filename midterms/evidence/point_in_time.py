"""Reusable point-in-time integrity helpers for as-of evidence reads."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def parse_as_of(as_of: str | date) -> date:
    if isinstance(as_of, date):
        return as_of
    return date.fromisoformat(str(as_of)[:10])


def filter_available_at(
    df: pd.DataFrame,
    as_of: str | date,
    *,
    column: str = "available_at",
) -> pd.DataFrame:
    """Keep rows with ``available_at <= as_of``; drop unknown timestamps."""
    if df is None or df.empty or column not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()
    as_of_d = parse_as_of(as_of)
    avail = pd.to_datetime(df[column], errors="coerce").dt.date
    return df[avail.notna() & (avail <= as_of_d)].copy()


def assert_no_future_rows(
    df: pd.DataFrame,
    as_of: str | date,
    *,
    column: str = "available_at",
    label: str = "evidence",
) -> None:
    """Fail closed if any row post-dates the as-of cutoff."""
    if df is None or df.empty or column not in df.columns:
        return
    as_of_d = parse_as_of(as_of)
    avail = pd.to_datetime(df[column], errors="coerce").dt.date
    bad = avail.notna() & (avail > as_of_d)
    if bad.any():
        raise RuntimeError(
            f"Point-in-time leakage in {label}: {int(bad.sum())} rows with "
            f"{column} after {as_of_d.isoformat()}"
        )


def refuse_current_fallback(
    *,
    historical_empty: bool,
    attempted_current: bool,
    domain: str,
) -> dict[str, Any]:
    """Document / assert that empty historical lookups must not call current."""
    if historical_empty and attempted_current:
        raise RuntimeError(
            f"Point-in-time integrity violation in {domain}: empty historical "
            "lookup must not fall back to current/live data"
        )
    return {
        "domain": domain,
        "historical_empty": historical_empty,
        "attempted_current": attempted_current,
        "ok": not (historical_empty and attempted_current),
    }
