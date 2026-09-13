"""ALFRED / FRED real-time vintage economics for as-of fundamentals."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

# Real disposable personal income per capita, Chained 2017 $, Seasonally Adjusted
# ALFRED series commonly used in election fundamentals work.
DEFAULT_SERIES = "A229RX0"  # Real Disposable Personal Income: Per Capita
PARSER_VERSION = "alfred-v1"


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
                "value": float(obs["value"]),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def build_fixture_vintages() -> pd.DataFrame:
    """Offline synthetic vintages shaped like YoY RDPI growth by cycle/as-of."""
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
        # first Tuesday after first Monday
        while ed.weekday() != 0:
            ed = date.fromordinal(ed.toordinal() + 1)
        ed = date.fromordinal(ed.toordinal() + 1)
        for lead, yoy in leads.items():
            as_of = date.fromordinal(ed.toordinal() - lead)
            rows.append(
                {
                    "series_id": "RDPI_YOY_FIXTURE",
                    "observation_date": as_of.isoformat(),
                    "realtime_start": as_of.isoformat(),
                    "realtime_end": as_of.isoformat(),
                    "available_at": as_of.isoformat(),
                    "value": float(yoy),
                    "transformation": "yoy_pct",
                    "election_year": year,
                    "lead_days": lead,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "parser_version": PARSER_VERSION,
                    "status": "fixture",
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
        # OneDrive lock: keep existing parquet if present
        if not norm_path.exists():
            raise
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": int(len(df)),
        "series": sorted(df["series_id"].astype(str).unique().tolist()),
        "parser_version": PARSER_VERSION,
        "note": "ALFRED live fetch used when FRED_API_KEY is set; else fixture vintages.",
    }
    man_path = MANIFESTS_DIR / "economics_vintages.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {"raw": str(raw_path), "normalized": str(norm_path), "manifest": str(man_path)}


def yoy_growth_as_of(as_of: str | date, *, election_year: int | None = None) -> float | None:
    """Return vintage YoY real-income growth (%) known by as_of."""
    path = NORMALIZED_DIR / "economics_vintages.parquet"
    if not path.exists():
        write_economic_store()
    df = pd.read_parquet(path)
    as_of_d = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    if "available_at" in df.columns:
        avail = pd.to_datetime(df["available_at"]).dt.date
        usable = df[avail <= as_of_d]
    else:
        usable = df
    if election_year is not None and "election_year" in usable.columns:
        year_hit = usable[usable["election_year"] == election_year]
        if len(year_hit):
            usable = year_hit
    if usable.empty:
        return None
    # Prefer fixture lead-matched rows, else latest observation_date
    usable = usable.sort_values("observation_date")
    return float(usable.iloc[-1]["value"])


def try_refresh_alfred(as_of: str | date | None = None) -> dict[str, Any]:
    """Best-effort live ALFRED pull merged onto the fixture store."""
    live = fetch_alfred_observations(realtime_end=as_of)
    fixtures = build_fixture_vintages()
    if live.empty:
        paths = write_economic_store(fixtures)
        return {"live_rows": 0, "used_fixtures": True, **paths}
    # Derive simple YoY from last two annual points if monthly/quarterly levels
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
