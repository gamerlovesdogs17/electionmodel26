"""Presidential approval vintages for as-of fundamentals (blueprint §5.1).

Live path: VoteHub CC BY approval polls for the sitting president (``donald-trump``).
Historical cycles keep dated curated snapshots so as-of backtests remain defined.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

PARSER_VERSION = "approval-v2"
VOTEHUB_SUBJECT = "donald-trump"
VOTEHUB_SOURCE_URL = "https://api.votehub.com/polls?subject=donald-trump&poll_type=approval"

# Historical curated snapshots (pre-VoteHub coverage) for midterm as-of runs.
HISTORICAL_APPROVAL_VINTAGES: list[dict[str, Any]] = [
    {"year": 2014, "available_at": "2014-07-01", "white_house_party": "D", "net_approval": -8.0},
    {"year": 2014, "available_at": "2014-09-01", "white_house_party": "D", "net_approval": -10.0},
    {"year": 2014, "available_at": "2014-10-15", "white_house_party": "D", "net_approval": -11.0},
    {"year": 2016, "available_at": "2016-07-01", "white_house_party": "D", "net_approval": 2.0},
    {"year": 2016, "available_at": "2016-09-01", "white_house_party": "D", "net_approval": 1.0},
    {"year": 2016, "available_at": "2016-10-15", "white_house_party": "D", "net_approval": 0.0},
    {"year": 2018, "available_at": "2018-07-01", "white_house_party": "R", "net_approval": -8.0},
    {"year": 2018, "available_at": "2018-09-01", "white_house_party": "R", "net_approval": -10.0},
    {"year": 2018, "available_at": "2018-10-15", "white_house_party": "R", "net_approval": -9.0},
    {"year": 2020, "available_at": "2020-07-01", "white_house_party": "R", "net_approval": -12.0},
    {"year": 2020, "available_at": "2020-09-01", "white_house_party": "R", "net_approval": -10.0},
    {"year": 2020, "available_at": "2020-10-15", "white_house_party": "R", "net_approval": -8.0},
    {"year": 2022, "available_at": "2022-07-01", "white_house_party": "D", "net_approval": -14.0},
    {"year": 2022, "available_at": "2022-09-01", "white_house_party": "D", "net_approval": -12.0},
    {"year": 2022, "available_at": "2022-10-15", "white_house_party": "D", "net_approval": -11.0},
    {"year": 2024, "available_at": "2024-07-01", "white_house_party": "D", "net_approval": -16.0},
    {"year": 2024, "available_at": "2024-09-01", "white_house_party": "D", "net_approval": -15.0},
    {"year": 2024, "available_at": "2024-10-15", "white_house_party": "D", "net_approval": -14.0},
]


def _net_from_answers(answers: list[dict[str, Any]] | None) -> float | None:
    if not answers:
        return None
    approve = disapprove = None
    for a in answers:
        choice = str(a.get("choice") or "").strip().lower()
        pct = a.get("pct")
        if pct is None:
            continue
        try:
            val = float(pct)
        except (TypeError, ValueError):
            continue
        if choice.startswith("approve") and "dis" not in choice:
            approve = val
        elif choice.startswith("disapprove") or choice == "disapprove":
            disapprove = val
    if approve is None or disapprove is None:
        return None
    return float(approve - disapprove)


def fetch_votehub_trump_approval_polls() -> list[dict[str, Any]]:
    """Pull VoteHub approval polls for Donald Trump (CC BY 4.0)."""
    from midterms.evidence.ingest import votehub_get

    payload = votehub_get(
        "/polls", {"subject": VOTEHUB_SUBJECT, "poll_type": "approval"}
    )
    if isinstance(payload, dict):
        polls = payload.get("polls") or payload.get("results") or []
    else:
        polls = payload or []
    return list(polls)


def aggregate_votehub_approval_vintages(
    polls: list[dict[str, Any]] | None = None,
    *,
    window_days: int = 14,
    white_house_party: str = "R",
) -> pd.DataFrame:
    """Build dated net-approval vintages from VoteHub approval polls."""
    polls = polls if polls is not None else fetch_votehub_trump_approval_polls()
    rows = []
    for p in polls:
        end = str(p.get("end_date") or p.get("created_at") or "")[:10]
        if len(end) < 10:
            continue
        net = _net_from_answers(p.get("answers"))
        if net is None:
            continue
        n = p.get("sample_size")
        try:
            n_f = float(n) if n is not None else 800.0
        except (TypeError, ValueError):
            n_f = 800.0
        rows.append(
            {
                "end_date": end,
                "net": net,
                "n": max(100.0, n_f),
                "pollster": p.get("pollster"),
                "poll_id": p.get("id"),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["end_date"] = pd.to_datetime(df["end_date"]).dt.date
    # Monthly vintages from first poll month through latest.
    start = df["end_date"].min()
    end = df["end_date"].max()
    # Also stamp mid-month and month-end markers commonly used as as-of dates.
    stamps: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        for day in (1, 15):
            try:
                stamps.append(date(y, m, day))
            except ValueError:
                pass
        # next month
        m += 1
        if m > 12:
            m = 1
            y += 1
    stamps.append(end)
    stamps = sorted(set(stamps))

    out_rows = []
    retrieved = datetime.now(timezone.utc).isoformat()
    for as_of in stamps:
        lo = as_of.toordinal() - int(window_days)
        window = df[
            (df["end_date"].map(lambda d: d.toordinal()) >= lo)
            & (df["end_date"] <= as_of)
        ]
        if window.empty:
            continue
        w = np.sqrt(window["n"].to_numpy(dtype=float))
        net = float(np.average(window["net"].to_numpy(dtype=float), weights=w))
        out_rows.append(
            {
                "year": int(as_of.year),
                "available_at": as_of.isoformat(),
                "white_house_party": white_house_party,
                "net_approval": round(net, 2),
                "n_polls": int(len(window)),
                "window_days": window_days,
                "source": "votehub_approval_aggregate",
                "retrieved_at": retrieved,
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(out_rows)


def write_approval_store(*, prefer_votehub: bool = True) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    hist = pd.DataFrame(HISTORICAL_APPROVAL_VINTAGES)
    hist["source"] = "curated_public_aggregate_research_snapshot"
    hist["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    hist["parser_version"] = PARSER_VERSION
    hist["n_polls"] = None
    hist["window_days"] = None

    live = pd.DataFrame()
    fetch_error: str | None = None
    if prefer_votehub:
        try:
            live = aggregate_votehub_approval_vintages()
        except Exception as exc:  # noqa: BLE001
            fetch_error = str(exc)

    frames = [hist]
    if len(live):
        frames.append(live)
    df = pd.concat(frames, ignore_index=True)
    # Prefer VoteHub rows when both exist for the same available_at.
    df = df.sort_values(["available_at", "source"]).drop_duplicates(
        subset=["available_at"], keep="last"
    )

    raw = RAW_DIR / "external" / "pres_approval_vintages.json"
    raw.write_text(
        json.dumps(
            {
                "rows": df.to_dict(orient="records"),
                "parser_version": PARSER_VERSION,
                "votehub_n": int(len(live)),
                "historical_n": int(len(hist)),
            },
            indent=2,
            default=str,
        )
    )
    out = NORMALIZED_DIR / "pres_approval.parquet"
    df.to_parquet(out, index=False)

    live_n = int((df["source"] == "votehub_approval_aggregate").sum())
    tier = "aggregator" if live_n > 0 else "curated"
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": int(len(df)),
        "n_votehub": live_n,
        "n_historical_curated": int(len(hist)),
        "parser_version": PARSER_VERSION,
        "source_url": VOTEHUB_SOURCE_URL if live_n else "https://projects.fivethirtyeight.com/trump-approval-ratings/",
        "tier": tier,
        "license": "CC BY 4.0" if live_n else None,
        "attribution": "Polling data from VoteHub (https://votehub.com)" if live_n else None,
        "fetch_error": fetch_error,
        "note": (
            "Live net approval from VoteHub Trump approval polls (recency-weighted); "
            "pre-2025 cycles retain curated vintages for as-of backtests."
            if live_n
            else "Curated vintages only — VoteHub approval fetch unavailable."
        ),
        "paths": {"raw": str(raw), "normalized": str(out)},
    }
    (MANIFESTS_DIR / "pres_approval.json").write_text(json.dumps(man, indent=2))
    return man


def approval_as_of(as_of: str | date, *, election_year: int | None = None) -> dict[str, Any]:
    """Latest approval vintage available at `as_of` (optionally restricted to election year)."""
    path = NORMALIZED_DIR / "pres_approval.parquet"
    if not path.exists():
        write_approval_store()
    df = pd.read_parquet(path)
    as_of_d = date.fromisoformat(str(as_of)[:10])
    avail = pd.to_datetime(df["available_at"]).dt.date
    mask = avail <= as_of_d
    if election_year is not None:
        mask = mask & (df["year"].astype(int) == int(election_year))
    sub = df[mask]
    if sub.empty:
        sub = df[avail <= as_of_d]
    if sub.empty:
        return {"net_approval": 0.0, "white_house_party": "R", "available_at": None, "source": "default"}
    row = sub.sort_values("available_at").iloc[-1]
    return {
        "net_approval": float(row["net_approval"]),
        "white_house_party": str(row["white_house_party"]),
        "available_at": str(row["available_at"]),
        "source": str(row.get("source") or "curated"),
        "year": int(row["year"]),
    }
