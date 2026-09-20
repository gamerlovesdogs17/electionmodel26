"""Kalshi prediction-market ingest for Senate races + chamber control."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

PARSER_VERSION = "kalshi-v2-candidate-aware"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Class II 2026 contested set + specials (mirrors fixtures)
SENATE_2026_STATES = [
    "AL", "AK", "AR", "CO", "DE", "FL", "GA", "ID", "IL", "IA", "KS", "KY", "LA",
    "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH", "NJ", "NM", "NC", "OH", "OK",
    "OR", "RI", "SC", "SD", "TN", "TX", "VA", "WV", "WY",
]

# Kalshi uses SENATE{ST}S-{yy} for special elections (FL / OH 2026).
SPECIAL_EVENT_SUFFIX: dict[str, str] = {
    "FL": "S",
    "OH": "S",
}


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


def _name_tokens(value: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z]+", plain.lower())


def _matches_name(ticker: str, title: str, candidate_name: str) -> bool:
    """Use a title or a candidate-specific ticker code, never a party suffix alone."""
    tokens = _name_tokens(candidate_name)
    if not tokens:
        return False
    surname = tokens[-1]
    title_tokens = _name_tokens(title)
    if surname in title_tokens and (len(tokens) == 1 or tokens[0] in title_tokens):
        return True
    suffix = str(ticker).rsplit("-", 1)[-1].upper()
    if len(surname) >= 4:
        stem = surname[:3].upper()
        return len(suffix) >= 4 and suffix.endswith(stem) and suffix[0] == tokens[0][0].upper()
    return False


def map_race_event(
    *, race_id: str, event_ticker: str, markets: list[dict[str, Any]], ticket: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Map a priced event to the candidate represented by the model's non-R side.

    Candidate identity and party are explicit. Chamber caucus accounting is a
    separate model assumption and is not inferred from market tickers.
    """
    target_name = str(ticket.get("dem_name") or "").strip()
    target_party = str(ticket.get("dem_party") or "").upper()
    rep_name = str(ticket.get("rep_name") or "").strip()
    if not target_name or not rep_name or target_party not in {"D", "I"}:
        return None, "missing modeled candidate identity"
    contracts: list[dict[str, Any]] = []
    for m in markets:
        ticker = str(m.get("ticker") or "")
        title = str(m.get("title") or "")
        if not ticker.startswith(event_ticker + "-"):
            continue
        p = _mid(_f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("last_price_dollars")))
        if p is None or not 0 <= p <= 1:
            return None, f"missing or invalid price for {ticker}"
        suffix = ticker[len(event_ticker) + 1 :].upper()
        target_by_name = _matches_name(ticker, title, target_name)
        rep_by_name = _matches_name(ticker, title, rep_name)
        target_by_party = target_party == "D" and suffix == "D"
        rep_by_party = suffix == "R"
        if target_by_name and rep_by_name:
            return None, f"ambiguous candidate contract {ticker}"
        modeled_side = bool(target_by_name or target_by_party)
        republican_side = bool(rep_by_name or rep_by_party)
        # An Independent's party marker alone is not a candidate identity.
        if target_party == "I" and not target_by_name:
            modeled_side = False
        contracts.append({
            "ticker": ticker, "title": title, "p_yes": float(p),
            "candidate_name": target_name if modeled_side else (rep_name if republican_side else None),
            "candidate_party": target_party if modeled_side else ("R" if republican_side else None),
            "modeled_side": modeled_side,
            "republican_side": republican_side,
        })
    targets = [c for c in contracts if c["modeled_side"]]
    reps = [c for c in contracts if c["republican_side"]]
    if len(targets) != 1 or len(reps) != 1 or targets[0] is reps[0]:
        return None, "modeled candidate or Republican contract is absent or ambiguous"
    if len(contracts) < 2:
        return None, "incomplete candidate event"
    total = sum(c["p_yes"] for c in contracts)
    if total <= 0:
        return None, "candidate event has zero priced mass"
    for c in contracts:
        c["p_normalized"] = c["p_yes"] / total
    return {
        "race_id": race_id,
        "event_ticker": event_ticker,
        "p_dem": targets[0]["p_normalized"],  # legacy overlay field: modeled non-R side
        "modeled_side_probability": targets[0]["p_normalized"],
        "modeled_candidate_name": target_name,
        "modeled_candidate_party": target_party,
        "modeled_candidate_ticker": targets[0]["ticker"],
        "republican_candidate_name": rep_name,
        "republican_candidate_ticker": reps[0]["ticker"],
        "candidate_contracts": contracts,
        "normalization_contracts": len(contracts),
        "market_mapping": "candidate_identity",
    }, None


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
    """Candidate-aware race events → modeled-side market overlay evidence."""
    from midterms.evidence.tickets import TICKETS_2026

    states = states or SENATE_2026_STATES
    stamp = available_at or datetime.now(timezone.utc).date().isoformat()
    rows = []
    errors = []
    for st in states:
        candidates = [f"SENATE{st}-{cycle_suffix}"]
        extra = SPECIAL_EVENT_SUFFIX.get(st)
        if extra:
            candidates.insert(0, f"SENATE{st}{extra}-{cycle_suffix}")
        data = None
        event = candidates[0]
        last_err: Exception | None = None
        for event in candidates:
            try:
                data = _get("/markets", {"limit": 100, "status": "open", "event_ticker": event})
                if data.get("markets"):
                    break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                data = None
        if data is None:
            errors.append({"state": st, "error": str(last_err or "no markets")})
            continue
        if not (data.get("markets") or []):
            continue
        ticket = TICKETS_2026.get(st) if cycle_suffix == "26" else None
        if ticket is None:
            errors.append({"state": st, "error": "no candidate identity registry for this cycle"})
            continue
        mapped, reason = map_race_event(
            race_id=f"senate-20{cycle_suffix}-{st}",
            event_ticker=event,
            markets=data.get("markets") or [],
            ticket=ticket,
        )
        if mapped is None:
            errors.append({"state": st, "error": str(reason), "event_ticker": event})
            continue
        vol = max((_f(m.get("volume_fp")) or 0.0 for m in data.get("markets") or []), default=0.0)
        liq = min(1.0, vol / 250_000.0)  # race markets thinner than control
        rows.append(
            {
                **mapped,
                "state": st,
                "liquidity": float(liq),
                "volume": float(vol),
                "tickers": ",".join(c["ticker"] for c in mapped["candidate_contracts"]),
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
        "source_url": "https://api.elections.kalshi.com/",
        "tier": "aggregator" if len(races) or control.get("p_dem") is not None else "curated",
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
    # A prior parser can carry a wrong candidate mapping. Never reuse it after
    # a refresh failure or when loading an older workspace artifact.
    if "parser_version" not in df.columns:
        return df.head(0)
    df = df[df["parser_version"] == PARSER_VERSION]
    if as_of and "available_at" in df.columns and len(df):
        df = df[pd.to_datetime(df["available_at"]).dt.date <= pd.Timestamp(as_of).date()]
    return df


def load_control_market() -> dict[str, Any]:
    path = NORMALIZED_DIR / "markets_control.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())
