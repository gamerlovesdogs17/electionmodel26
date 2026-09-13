"""ALFRED / FRED real-time vintage economics for as-of fundamentals."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

# Real disposable personal income per capita, Chained 2017 $, Seasonally Adjusted
# ALFRED series commonly used in election fundamentals work.
DEFAULT_SERIES = "A229RX0"  # Real Disposable Personal Income: Per Capita
PARSER_VERSION = "alfred-v2"


def _fred_api_key() -> str | None:
    return os.environ.get("FRED_API_KEY") or os.environ.get("ALFRED_API_KEY")


def fetch_alfred_observations(
    series_id: str = DEFAULT_SERIES,
    *,
    realtime_end: str | date | None = None,
    api_key: str | None = None,
) -> pd.DataFrame:
    """
    Pull one vintage slice from ALFRED: values as known on `realtime_end`.

    Without an API key, returns an empty frame (caller should use fixtures).
    """
    key = api_key or _fred_api_key()
    if not key:
        return pd.DataFrame()

    as_of = realtime_end or date.today()
    if not isinstance(as_of, str):
        as_of = as_of.isoformat()

    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "realtime_start": as_of,
        "realtime_end": as_of,
    }
    url = "https://api.stlouisfed.org/fred/series/observations?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.2 (research)"})
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = []
    for obs in payload.get("observations") or []:
        if obs.get("value") in {".", "", None}:
            continue
        rows.append(
            {
                "series_id": series_id,
                "observation_date": obs["date"],
                "realtime_start": obs.get("realtime_start"),
                "realtime_end": obs.get("realtime_end"),
                "available_at": obs.get("realtime_start") or as_of,
                "value": float(obs["value"]),
                "revision": 0,
                "vintage_id": f"{series_id}|{obs['date']}|{obs.get('realtime_start')}",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def build_fixture_vintages() -> pd.DataFrame:
    """
    Offline multi-vintage YoY RDPI store (blueprint §5.3).

    For each cycle/lead, store a preliminary release known by `as_of` and a
    post-election revision that must NOT leak into earlier as-of queries.
    """
    # Approximate public YoY real disposable income growth (%) known mid-campaign
    seeds = {
        2014: {120: 1.2, 90: 1.4, 60: 1.5, 30: 1.8, 7: 2.0},
        2016: {120: 2.1, 90: 2.0, 60: 1.9, 30: 1.7, 7: 1.6},
        2018: {120: 2.8, 90: 2.9, 60: 3.0, 30: 3.1, 7: 3.2},
        2020: {120: 2.0, 90: 4.5, 60: 6.0, 30: 5.5, 7: 4.0},
        2022: {120: -1.5, 90: -1.8, 60: -2.0, 30: -1.6, 7: -1.2},
        2024: {120: 2.2, 90: 2.0, 60: 1.8, 30: 1.5, 7: 1.4},
        2026: {120: 1.0, 90: 0.8, 60: 0.6, 30: 0.5, 7: 0.4},
    }
    rows = []
    for year, leads in seeds.items():
        ed = date(year, 11, 1)
        while ed.weekday() != 0:
            ed = date.fromordinal(ed.toordinal() + 1)
        ed = date.fromordinal(ed.toordinal() + 1)
        for lead, yoy in leads.items():
            as_of = date.fromordinal(ed.toordinal() - lead)
            obs = as_of.isoformat()
            # Preliminary vintage visible on as_of
            rows.append(
                {
                    "series_id": "RDPI_YOY_FIXTURE",
                    "observation_date": obs,
                    "realtime_start": as_of.isoformat(),
                    "realtime_end": (ed + timedelta(days=30)).isoformat(),
                    "available_at": as_of.isoformat(),
                    "value": float(yoy),
                    "revision": 0,
                    "vintage_id": f"RDPI_YOY_FIXTURE|{year}|{lead}|r0",
                    "transformation": "yoy_pct",
                    "election_year": year,
                    "lead_days": lead,
                    "seasonal_adjustment": "SA",
                    "release_lag_days": 30,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "parser_version": PARSER_VERSION,
                    "status": "fixture_preliminary",
                }
            )
            # Post-election revision — available only after ED (leakage canary)
            revised = float(yoy) + (0.4 if yoy >= 0 else -0.3)
            rev_avail = ed + timedelta(days=45)
            rows.append(
                {
                    "series_id": "RDPI_YOY_FIXTURE",
                    "observation_date": obs,
                    "realtime_start": rev_avail.isoformat(),
                    "realtime_end": "9999-12-31",
                    "available_at": rev_avail.isoformat(),
                    "value": revised,
                    "revision": 1,
                    "vintage_id": f"RDPI_YOY_FIXTURE|{year}|{lead}|r1",
                    "transformation": "yoy_pct",
                    "election_year": year,
                    "lead_days": lead,
                    "seasonal_adjustment": "SA",
                    "release_lag_days": 30,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "parser_version": PARSER_VERSION,
                    "status": "fixture_revised",
                }
            )
    return pd.DataFrame(rows)


def write_economic_store(df: pd.DataFrame | None = None) -> dict[str, str]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    df = df if df is not None and len(df) else build_fixture_vintages()
    raw_path = RAW_DIR / "economics_vintages.csv"
    norm_path = NORMALIZED_DIR / "economics_vintages.parquet"
    df.to_csv(raw_path, index=False)
    try:
        from midterms.ops.fsutil import safe_to_parquet

        safe_to_parquet(df, norm_path, index=False)
    except OSError:
        if not norm_path.exists():
            raise
    n_rev = int((df["revision"] > 0).sum()) if "revision" in df.columns else 0
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": int(len(df)),
        "n_revisions": n_rev,
        "series": sorted(df["series_id"].astype(str).unique().tolist()),
        "parser_version": PARSER_VERSION,
        "note": (
            "ALFRED multi-vintage store: observation_date vs available_at/realtime. "
            "As-of queries use latest value known by that date (blueprint section 5.3)."
        ),
    }
    man_path = MANIFESTS_DIR / "economics_vintages.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {"raw": str(raw_path), "normalized": str(norm_path), "manifest": str(man_path)}


def yoy_growth_as_of(as_of: str | date, *, election_year: int | None = None) -> float | None:
    """
    Return vintage YoY real-income growth (%) known by as_of.

    Uses the latest vintage with available_at/realtime_start <= as_of — never a
    later revision (blueprint §5.3 / Table 6 leakage control).
    """
    path = NORMALIZED_DIR / "economics_vintages.parquet"
    if not path.exists():
        write_economic_store()
    df = pd.read_parquet(path)
    as_of_d = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    if "available_at" in df.columns:
        avail = pd.to_datetime(df["available_at"]).dt.date
        usable = df[avail <= as_of_d].copy()
    elif "realtime_start" in df.columns:
        avail = pd.to_datetime(df["realtime_start"]).dt.date
        usable = df[avail <= as_of_d].copy()
    else:
        usable = df.copy()
    if election_year is not None and "election_year" in usable.columns:
        year_hit = usable[usable["election_year"] == election_year]
        if len(year_hit):
            usable = year_hit
    if usable.empty:
        return None
    # Prefer highest revision among those already available, then latest observation
    sort_cols = [c for c in ("observation_date", "revision", "available_at") if c in usable.columns]
    usable = usable.sort_values(sort_cols)
    return float(usable.iloc[-1]["value"])


def try_refresh_alfred(as_of: str | date | None = None) -> dict[str, Any]:
    """Best-effort live ALFRED pull merged onto the fixture store."""
    live = fetch_alfred_observations(realtime_end=as_of)
    fixtures = build_fixture_vintages()
    if live.empty:
        paths = write_economic_store(fixtures)
        return {"live_rows": 0, "used_fixtures": True, **paths}
    live = live.sort_values("observation_date")
    if len(live) >= 13:
        latest = float(live.iloc[-1]["value"])
        year_ago = float(live.iloc[-13]["value"])
        yoy = 100.0 * (latest / year_ago - 1.0) if year_ago else None
    else:
        yoy = None
    if yoy is not None:
        as_of_d = as_of or date.today()
        if not isinstance(as_of_d, str):
            as_of_d = as_of_d.isoformat()
        extra = pd.DataFrame(
            [
                {
                    "series_id": DEFAULT_SERIES + "_YOY",
                    "observation_date": as_of_d,
                    "realtime_start": as_of_d,
                    "realtime_end": as_of_d,
                    "available_at": as_of_d,
                    "value": yoy,
                    "revision": 0,
                    "vintage_id": f"{DEFAULT_SERIES}_YOY|{as_of_d}|r0",
                    "transformation": "yoy_pct",
                    "election_year": int(str(as_of_d)[:4]),
                    "lead_days": None,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "parser_version": PARSER_VERSION,
                    "status": "alfred_live",
                }
            ]
        )
        fixtures = pd.concat([fixtures, extra], ignore_index=True)
    paths = write_economic_store(fixtures)
    return {"live_rows": int(len(live)), "yoy": yoy, "used_fixtures": False, **paths}
