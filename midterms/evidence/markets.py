"""Kalshi prediction-market ingest for Senate races + chamber control."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

PARSER_VERSION = "kalshi-v1"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Class II 2026 contested set (mirrors fixtures)
SENATE_2026_STATES = [
    "AL", "AK", "AR", "CO", "DE", "GA", "ID", "IL", "IA", "KS", "KY", "LA",
    "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH", "NJ", "NM", "NC", "OK",
    "OR", "RI", "SC", "SD", "TN", "TX", "VA", "WV", "WY",
]


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{KALSHI}{path}"
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.3 (research)"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mid(bid: float | None, ask: float | None, last: float | None) -> float | None:
    vals = [v for v in (bid, ask) if v is not None]
    if len(vals) == 2:
        return 0.5 * (vals[0] + vals[1])
    if last is not None:
        return last
    if vals:
        return vals[0]
    return None


def _f(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_control_market(cycle: int = 2026) -> dict[str, Any]:
    """Chamber-control market: CONTROLS-{year}-D / -R."""
    event = f"CONTROLS-{cycle}"
    data = _get("/markets", {"limit": 10, "status": "open", "event_ticker": event})
    out: dict[str, Any] = {
        "event_ticker": event,
        "p_dem": None,
        "p_rep": None,
        "liquidity": 0.0,
        "markets": [],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    for m in data.get("markets") or []:
        ticker = str(m.get("ticker") or "")
        p = _mid(
            _f(m.get("yes_bid_dollars")),
            _f(m.get("yes_ask_dollars")),
            _f(m.get("last_price_dollars")),
        )
        vol = _f(m.get("volume_fp")) or 0.0
        row = {
            "ticker": ticker,
            "title": m.get("title"),
            "p_yes": p,
            "volume": vol,
            "volume_24h": _f(m.get("volume_24h_fp")),
            "yes_bid": _f(m.get("yes_bid_dollars")),
            "yes_ask": _f(m.get("yes_ask_dollars")),
            "last": _f(m.get("last_price_dollars")),
        }
        out["markets"].append(row)
        if ticker.endswith("-D") and p is not None:
            out["p_dem"] = p
        if ticker.endswith("-R") and p is not None:
            out["p_rep"] = p
        out["liquidity"] = max(float(out["liquidity"]), min(1.0, vol / 5_000_000.0))
    # Normalize if both present
    if out["p_dem"] is not None and out["p_rep"] is not None:
        s = out["p_dem"] + out["p_rep"]
        if s > 0:
            out["p_dem"] /= s
            out["p_rep"] /= s
    return out


def fetch_race_markets(
    states: list[str] | None = None,
    cycle_suffix: str = "26",
    *,
    available_at: str | None = None,
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    """Race-level SENATE{ST}-{yy}-D markets → p_dem + liquidity."""
    states = states or SENATE_2026_STATES
    stamp = available_at or datetime.now(timezone.utc).date().isoformat()
    rows = []
    errors = []
    for st in states:
        event = f"SENATE{st}-{cycle_suffix}"
        try:
            data = _get("/markets", {"limit": 10, "status": "open", "event_ticker": event})
        except Exception as exc:  # noqa: BLE001
            errors.append({"state": st, "error": str(exc)})
            continue
        p_dem = None
        p_rep = None
        vol = 0.0
        tickers = []
        for m in data.get("markets") or []:
            ticker = str(m.get("ticker") or "")
            tickers.append(ticker)
            p = _mid(
                _f(m.get("yes_bid_dollars")),
                _f(m.get("yes_ask_dollars")),
                _f(m.get("last_price_dollars")),
            )
            vol = max(vol, _f(m.get("volume_fp")) or 0.0)
            if ticker.endswith("-D"):
                p_dem = p
            elif ticker.endswith("-R"):
                p_rep = p
        if p_dem is None and p_rep is not None:
            p_dem = 1.0 - p_rep
        if p_dem is None:
            continue
        if p_rep is not None:
            s = p_dem + p_rep
            if s > 0:
                p_dem = p_dem / s
        liq = min(1.0, vol / 250_000.0)  # race markets thinner than control
        rows.append(
            {
                "state": st,
                "race_id": f"senate-20{cycle_suffix}-{st}",
                "event_ticker": event,
                "p_dem": float(p_dem),
                "liquidity": float(liq),
                "volume": float(vol),
                "tickers": ",".join(tickers),
                "source": "kalshi",
                "available_at": stamp,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows), errors


def write_markets_store(
    election_id: str = "senate-2026", *, available_at: str | None = None
) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    m = re.search(r"(\d{4})", election_id)
    year = int(m.group(1)) if m else 2026
    suffix = str(year)[-2:]
    stamp = available_at or datetime.now(timezone.utc).date().isoformat()

    control = {}
    control_err = None
    try:
        control = fetch_control_market(year)
    except Exception as exc:  # noqa: BLE001
        control_err = str(exc)

    races, errors = fetch_race_markets(cycle_suffix=suffix, available_at=stamp)
    if len(races):
        races = races.copy()
        races["race_id"] = [f"{election_id}-{st}" for st in races["state"]]
        races["election_id"] = election_id
        races["available_at"] = stamp

    raw_path = RAW_DIR / "external" / "kalshi_senate.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(
            {
                "control": control,
                "races": races.to_dict(orient="records") if len(races) else [],
                "errors": errors,
                "control_error": control_err,
            },
            indent=2,
        )
    )
    share_path = NORMALIZED_DIR / "markets.parquet"
    if len(races):
        races.to_parquet(share_path, index=False)
    else:
        pd.DataFrame(
            columns=[
                "state",
                "race_id",
                "event_ticker",
                "p_dem",
                "liquidity",
                "volume",
                "tickers",
                "source",
                "available_at",
                "retrieved_at",
                "parser_version",
                "election_id",
            ]
        ).to_parquet(share_path, index=False)

    control_path = NORMALIZED_DIR / "markets_control.json"
    control_path.write_text(json.dumps(control, indent=2))

    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "available_at": stamp,
        "n_races": int(len(races)),
        "control_p_dem": control.get("p_dem"),
        "control_p_rep": control.get("p_rep"),
        "errors": errors[:10],
        "control_error": control_err,
        "parser_version": PARSER_VERSION,
        "paths": {
            "raw": str(raw_path),
            "races": str(share_path),
            "control": str(control_path),
        },
    }
    man_path = MANIFESTS_DIR / "markets_kalshi.json"
    man_path.write_text(json.dumps(man, indent=2))
    return man


def load_race_markets(as_of: str | None = None) -> pd.DataFrame:
    path = NORMALIZED_DIR / "markets.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if as_of and "available_at" in df.columns and len(df):
        df = df[pd.to_datetime(df["available_at"]).dt.date <= pd.Timestamp(as_of).date()]
    return df


def load_control_market() -> dict[str, Any]:
    path = NORMALIZED_DIR / "markets_control.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())
