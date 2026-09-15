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


def fetch_fred_public_csv(series_id: str = DEFAULT_SERIES) -> pd.DataFrame:
    """Download public FRED graph CSV (no API key). Observation dates only — no ALFRED vintages."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.9 (research)"})
    with urlopen(req, timeout=45) as resp:
        text = resp.read().decode("utf-8")
    from io import StringIO

    raw = pd.read_csv(StringIO(text))
    if raw.empty or series_id not in raw.columns:
        # column may be the series id
        cols = [c for c in raw.columns if c != "observation_date"]
        if not cols:
            return pd.DataFrame()
        value_col = cols[0]
    else:
        value_col = series_id
    rows = []
    retrieved = datetime.now(timezone.utc).isoformat()
    for _, r in raw.iterrows():
        val = r.get(value_col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        obs = str(r["observation_date"])[:10]
        rows.append(
            {
                "series_id": series_id,
                "observation_date": obs,
                "realtime_start": obs,
                "realtime_end": "9999-12-31",
                "available_at": obs,
                "value": v,
                "revision": 0,
                "vintage_id": f"{series_id}|{obs}|public_csv",
                "transformation": "level",
                "retrieved_at": retrieved,
                "parser_version": PARSER_VERSION,
                "status": "fred_public_csv",
                "source_url": url,
            }
        )
    return pd.DataFrame(rows)


def _yoy_rows_from_levels(levels: pd.DataFrame, *, series_id: str = DEFAULT_SERIES) -> pd.DataFrame:
    if levels.empty or len(levels) < 13:
        return pd.DataFrame()
    g = levels.sort_values("observation_date").reset_index(drop=True)
    rows = []
    for i in range(12, len(g)):
        latest = float(g.iloc[i]["value"])
        year_ago = float(g.iloc[i - 12]["value"])
        if year_ago == 0:
            continue
        yoy = 100.0 * (latest / year_ago - 1.0)
        obs = str(g.iloc[i]["observation_date"])[:10]
        rows.append(
            {
                "series_id": f"{series_id}_YOY",
                "observation_date": obs,
                "realtime_start": obs,
                "realtime_end": "9999-12-31",
                "available_at": obs,
                "value": yoy,
                "revision": 0,
                "vintage_id": f"{series_id}_YOY|{obs}|r0",
                "transformation": "yoy_pct",
                "election_year": int(obs[:4]),
                "lead_days": None,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "parser_version": PARSER_VERSION,
                "status": "fred_public_csv_yoy",
                "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
            }
        )
    return pd.DataFrame(rows)


def write_economic_store(df: pd.DataFrame | None = None) -> dict[str, str]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    norm_path = NORMALIZED_DIR / "economics_vintages.parquet"
    if df is None or len(df) == 0:
        # Preserve an existing production store; never silently replace with fixtures.
        if norm_path.exists():
            try:
                existing = pd.read_parquet(norm_path)
                if len(existing) and existing["series_id"].astype(str).str.contains("FIXTURE").eq(False).any():
                    df = existing
            except Exception:  # noqa: BLE001
                df = None
        if df is None or len(df) == 0:
            df = build_fixture_vintages()
    raw_path = RAW_DIR / "economics_vintages.csv"
    df.to_csv(raw_path, index=False)
    try:
        from midterms.ops.fsutil import safe_to_parquet

        safe_to_parquet(df, norm_path, index=False)
    except OSError:
        if not norm_path.exists():
            raise
    n_rev = int((df["revision"] > 0).sum()) if "revision" in df.columns else 0
    series = sorted(df["series_id"].astype(str).unique().tolist())
    production_series = [s for s in series if "FIXTURE" not in s]
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": int(len(df)),
        "n_revisions": n_rev,
        "series": series,
        "production_series": production_series,
        "source_url": "https://fred.stlouisfed.org/series/A229RX0",
        "tier": "first_party" if production_series else "synthetic",
        "parser_version": PARSER_VERSION,
        "note": (
            "Production YoY from FRED/ALFRED public levels when available; "
            "RDPI_YOY_FIXTURE retained only for historical leakage canaries."
        ),
    }
    man_path = MANIFESTS_DIR / "economics_vintages.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {"raw": str(raw_path), "normalized": str(norm_path), "manifest": str(man_path)}


def yoy_growth_as_of(as_of: str | date, *, election_year: int | None = None) -> float | None:
    """
    Return vintage YoY real-income growth (%) known by as_of.

    Prefers non-fixture production series; fixtures are leakage-canary only.
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
    if usable.empty:
        return None
    # Prefer production YoY series over fixtures
    prod = usable[~usable["series_id"].astype(str).str.contains("FIXTURE")]
    pool = prod if len(prod) else usable
    if election_year is not None and "election_year" in pool.columns:
        year_hit = pool[pool["election_year"] == election_year]
        if len(year_hit):
            pool = year_hit
    yoy = pool[pool["series_id"].astype(str).str.contains("YOY")]
    if len(yoy):
        pool = yoy
    sort_cols = [c for c in ("observation_date", "revision", "available_at") if c in pool.columns]
    pool = pool.sort_values(sort_cols)
    return float(pool.iloc[-1]["value"])


def try_refresh_alfred(as_of: str | date | None = None) -> dict[str, Any]:
    """Best-effort live ALFRED/FRED pull; fixtures kept only as leakage canaries."""
    fixtures = build_fixture_vintages()
    # Prefer API vintage slice when keyed; else public CSV levels → YoY.
    live = fetch_alfred_observations(realtime_end=as_of)
    public = pd.DataFrame()
    if live.empty:
        try:
            public = fetch_fred_public_csv()
        except Exception as exc:  # noqa: BLE001
            # Keep any existing production store; only fall back to fixtures if empty.
            existing_path = NORMALIZED_DIR / "economics_vintages.parquet"
            if existing_path.exists():
                try:
                    existing = pd.read_parquet(existing_path)
                    if len(existing) and existing["series_id"].astype(str).str.contains("FIXTURE").eq(False).any():
                        paths = write_economic_store(existing)
                        return {
                            "live_rows": 0,
                            "used_fixtures": False,
                            "preserved_existing": True,
                            "error": str(exc),
                            **paths,
                        }
                except Exception:  # noqa: BLE001
                    pass
            paths = write_economic_store(fixtures)
            return {"live_rows": 0, "used_fixtures": True, "error": str(exc), **paths}

    frames = [fixtures]
    yoy = None
    if not live.empty:
        frames.append(live)
        live_s = live.sort_values("observation_date")
        if len(live_s) >= 13:
            latest = float(live_s.iloc[-1]["value"])
            year_ago = float(live_s.iloc[-13]["value"])
            yoy = 100.0 * (latest / year_ago - 1.0) if year_ago else None
        source = "alfred_api"
    elif not public.empty:
        frames.append(public)
        yoy_df = _yoy_rows_from_levels(public)
        if len(yoy_df):
            frames.append(yoy_df)
            yoy = float(yoy_df.iloc[-1]["value"])
        source = "fred_public_csv"
    else:
        paths = write_economic_store(fixtures)
        return {"live_rows": 0, "used_fixtures": True, **paths}

    if yoy is not None and live.empty is False:
        as_of_d = as_of or date.today()
        if not isinstance(as_of_d, str):
            as_of_d = as_of_d.isoformat()
        frames.append(
            pd.DataFrame(
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
                        "source_url": f"https://fred.stlouisfed.org/series/{DEFAULT_SERIES}",
                    }
                ]
            )
        )

    merged = pd.concat(frames, ignore_index=True)
    paths = write_economic_store(merged)
    return {
        "live_rows": int(len(live) + len(public)),
        "yoy": yoy,
        "used_fixtures": False,
        "source": source,
        **paths,
    }
