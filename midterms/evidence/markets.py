"""Kalshi prediction-market ingest for Senate races + chamber control."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

PARSER_VERSION = "kalshi-v4-contract-semantics"
JSON_HASH_MODE = "canonical_json_sha256_v1"
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


def canonical_json_sha256(path: Path) -> str:
    """Hash every parsed JSON value independent of transport serialization."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{KALSHI}{path}"
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.3 (research)"})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code != 429 or attempt == 2:
                raise
            retry_after = _f(exc.headers.get("Retry-After"))
            time.sleep(min(10.0, max(1.0, retry_after or 2 ** (attempt + 1))))
    raise RuntimeError("market request retries exhausted")


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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


def validate_candidate_contract_family(
    event_ticker: str,
    markets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prove a family is one exhaustive set of mutually exclusive candidate wins.

    The ingest adapter must preserve these semantic fields from a source event
    or rules object.  Similar ticker prefixes alone are insufficient proof.
    """
    contracts = [m for m in markets if str(m.get("ticker") or "").startswith(event_ticker + "-")]
    reasons: list[str] = []
    if len(contracts) < 2:
        reasons.append("candidate family has fewer than two contracts")
    if any(str(m.get("event_ticker") or event_ticker) != event_ticker for m in contracts):
        reasons.append("contracts do not share one event")
    if any(str(m.get("outcome_type") or "") != "candidate_win" for m in contracts):
        reasons.append("candidate-win outcome semantics are unverified")
    if any(m.get("mutually_exclusive") is not True for m in contracts):
        reasons.append("mutual exclusivity is unverified")
    if any(m.get("event_exhaustive") is not True for m in contracts):
        reasons.append("event exhaustiveness is unverified")
    candidate_ids = [str(m.get("candidate_id") or "").strip() for m in contracts]
    if any(not value for value in candidate_ids):
        reasons.append("candidate outcome identity is missing")
    elif len(candidate_ids) != len(set(candidate_ids)):
        reasons.append("duplicate candidate outcome")
    if any(str(m.get("contract_scope") or "candidate") != "candidate" for m in contracts):
        reasons.append("unrelated proposition contract is mixed into event")
    return {
        "ok": not reasons,
        "event_ticker": event_ticker,
        "n_contracts": len(contracts),
        "candidate_ids": candidate_ids,
        "reasons": reasons,
        "semantic_version": "candidate-contract-family-v1",
    }


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
    if not target_name or not rep_name or not target_party:
        return None, "missing modeled candidate identity"
    family = validate_candidate_contract_family(event_ticker, markets)
    if not family["ok"]:
        return None, "unsafe contract family: " + "; ".join(family["reasons"])
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
        if target_by_name and rep_by_name:
            return None, f"ambiguous candidate contract {ticker}"
        contracts.append({
            "ticker": ticker, "title": title, "p_yes": float(p),
            "candidate_name": None, "candidate_party": None,
            "modeled_side": False, "republican_side": False,
            "target_name_match": target_by_name,
            "opponent_name_match": rep_by_name,
            "party_marker": suffix if suffix in {"D", "R"} else None,
        })
    if len({c["ticker"] for c in contracts}) != len(contracts):
        return None, "duplicate contract identifier"
    named_targets = [c for c in contracts if c["target_name_match"]]
    named_reps = [c for c in contracts if c["opponent_name_match"]]
    # A named match wins over a generic party marker. A marker may be used for
    # an ordinary major-party contract only when no name match exists.
    for c in contracts:
        suffix = c["party_marker"]
        c["modeled_side"] = bool(
            c["target_name_match"] or (
                not named_targets and target_party == "D" and suffix == "D"
                and not c["opponent_name_match"]
            )
        )
        c["republican_side"] = bool(
            c["opponent_name_match"] or (
                not named_reps and suffix == "R" and not c["target_name_match"]
            )
        )
        if c["modeled_side"]:
            c["candidate_name"], c["candidate_party"] = target_name, target_party
        elif c["republican_side"]:
            c["candidate_name"], c["candidate_party"] = rep_name, "R"
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
        "market_mapping": "named_candidate" if named_targets else "party_contract_with_identity_registry",
        "contract_family_validation": family,
    }, None


def audit_race_event(
    *, race_id: str, event_ticker: str, markets: list[dict[str, Any]], ticket: dict[str, Any]
) -> dict[str, Any]:
    """Record both successful and disabled mappings without silently substituting contracts."""
    mapped, reason = map_race_event(
        race_id=race_id, event_ticker=event_ticker, markets=markets, ticket=ticket,
    )
    from midterms.evidence.outcome_identity import identity_from_ticket

    try:
        identity = identity_from_ticket(race_id, ticket)
        modeled_id, opposing_id = (item.candidate_id for item in identity.contenders)
    except ValueError:
        modeled_id, opposing_id = None, None
    raw_contracts = []
    for market in markets:
        ticker = str(market.get("ticker") or "")
        if ticker.startswith(event_ticker + "-"):
            raw_contracts.append({
                "contract_id": ticker,
                "title": str(market.get("title") or ""),
                "raw_price": _mid(
                    _f(market.get("yes_bid_dollars")),
                    _f(market.get("yes_ask_dollars")),
                    _f(market.get("last_price_dollars")),
                ),
            })
    prices = [row["raw_price"] for row in raw_contracts]
    total = sum(prices) if prices and all(
        price is not None and 0 <= price <= 1 for price in prices
    ) else 0.0
    normalized = (
        {row["contract_id"]: row["raw_price"] / total for row in raw_contracts}
        if mapped is not None and total > 0
        and len({row["contract_id"] for row in raw_contracts}) == len(raw_contracts)
        else None
    )
    return {
        "race_id": race_id,
        "event_id": event_ticker,
        "modeled_entity_id": modeled_id,
        "opposing_entity_id": opposing_id,
        "modeled_candidate_name": ticket.get("dem_name"),
        "opposing_candidate_name": ticket.get("rep_name"),
        "modeled_ballot_party": ticket.get("dem_party"),
        "matched_modeled_contract_id": mapped["modeled_candidate_ticker"] if mapped else None,
        "matched_opposing_contract_id": mapped["republican_candidate_ticker"] if mapped else None,
        "raw_contract_prices": raw_contracts,
        "normalized_event_probabilities": normalized,
        "mapping_method": mapped["market_mapping"] if mapped else None,
        "ambiguous_or_unsafe": mapped is None,
        "overlay_enabled": mapped is not None,
        "fetch_status": "ok",
        "parser_version": PARSER_VERSION,
        "disable_reason": reason,
        "contract_family_validation": (
            mapped.get("contract_family_validation") if mapped
            else validate_candidate_contract_family(event_ticker, markets)
        ),
    }


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
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Candidate-aware race events → modeled-side market overlay evidence."""
    from midterms.evidence.tickets import TICKETS_2026

    states = states or SENATE_2026_STATES
    stamp = available_at or datetime.now(timezone.utc).date().isoformat()
    rows = []
    errors = []
    for st in states:
        race_id = f"senate-20{cycle_suffix}-{st}"
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
            errors.append({"state": st, "race_id": race_id,
                           "error": str(last_err or "no markets"), "event_ticker": event,
                           "kind": "fetch_failed"})
            continue
        if not (data.get("markets") or []):
            errors.append({"state": st, "race_id": race_id,
                           "error": "no priced contracts", "event_ticker": event,
                           "kind": "no_event_contracts"})
            continue
        ticket = TICKETS_2026.get(st) if cycle_suffix == "26" else None
        if ticket is None:
            errors.append({"state": st, "race_id": race_id,
                           "error": "no candidate identity registry for this cycle",
                           "event_ticker": event, "kind": "identity_unavailable"})
            continue
        mapped, reason = map_race_event(
            race_id=race_id,
            event_ticker=event,
            markets=data.get("markets") or [],
            ticket=ticket,
        )
        mapping_audit = audit_race_event(
            race_id=race_id,
            event_ticker=event,
            markets=data.get("markets") or [],
            ticket=ticket,
        )
        if mapped is None:
            errors.append({"state": st, "race_id": race_id,
                           "error": str(reason), "event_ticker": event,
                           "mapping_audit": mapping_audit})
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
                "overlay_enabled": True,
                "disable_reason": None,
                "modeled_entity_id": mapping_audit["modeled_entity_id"],
                "opposing_entity_id": mapping_audit["opposing_entity_id"],
                "matched_modeled_contract_id": mapping_audit["matched_modeled_contract_id"],
                "matched_opposing_contract_id": mapping_audit["matched_opposing_contract_id"],
                "mapping_audit": json.dumps(mapping_audit, sort_keys=True),
            }
        )
    return pd.DataFrame(rows), errors


def write_mapping_audit_store(
    races: pd.DataFrame, errors: list[dict[str, Any]], *, path: Path,
) -> dict[str, Any]:
    """Persist all event mappings, including disabled and unpriced events."""
    audits = []
    for _, row in races.iterrows():
        audits.append(json.loads(row["mapping_audit"]))
    for error in errors:
        audit = error.get("mapping_audit")
        if audit is None:
            audit = {
                "race_id": error.get("race_id"), "event_id": error.get("event_ticker"),
                "modeled_entity_id": None, "opposing_entity_id": None,
                "modeled_candidate_name": None, "opposing_candidate_name": None,
                "modeled_ballot_party": None,
                "matched_modeled_contract_id": None,
                "matched_opposing_contract_id": None,
                "overlay_enabled": False, "ambiguous_or_unsafe": True,
                "disable_reason": error.get("error"), "parser_version": PARSER_VERSION,
                "fetch_status": error.get("kind") or "unclassified_error",
                "raw_contract_prices": [], "normalized_event_probabilities": None,
                "mapping_method": None,
            }
        audits.append(audit)
    payload = {"parser_version": PARSER_VERSION, "events": audits}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        portable_path = path.resolve().relative_to(Path(__file__).resolve().parents[2]).as_posix()
    except ValueError:
        portable_path = path.name
    return {"path": portable_path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "hash_mode": JSON_HASH_MODE,
            "canonical_json_sha256": canonical_json_sha256(path),
            "n_enabled": sum(bool(a.get("overlay_enabled")) for a in audits),
            "n_disabled": sum(not bool(a.get("overlay_enabled")) for a in audits)}


def write_markets_store(
    election_id: str = "senate-2026", *, available_at: str | None = None
) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    m = re.search(r"(\d{4})", election_id)
    year = int(m.group(1)) if m else 2026
    suffix = str(year)[-2:]
    stamp = _utc_today()
    if available_at is not None and available_at != stamp:
        raise ValueError(
            "live market retrieval cannot be backdated or future-dated: "
            f"requested available_at={available_at}, retrieval date={stamp}"
        )

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
    mapping_meta = write_mapping_audit_store(
        races, errors, path=NORMALIZED_DIR / "markets_mapping_audit.json",
    )
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
                "overlay_enabled",
                "disable_reason",
                "modeled_entity_id",
                "opposing_entity_id",
                "matched_modeled_contract_id",
                "matched_opposing_contract_id",
                "mapping_audit",
                "election_id",
            ]
        ).to_parquet(share_path, index=False)

    control_path = NORMALIZED_DIR / "markets_control.json"
    control_path.write_text(json.dumps(control, indent=2))

    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "available_at": stamp,
        "retrieval_date": stamp,
        "n_races": int(len(races)),
        "control_p_dem": control.get("p_dem"),
        "control_p_rep": control.get("p_rep"),
        "errors": errors[:10],
        "control_error": control_err,
        "parser_version": PARSER_VERSION,
        "json_hash_mode": JSON_HASH_MODE,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "raw_canonical_json_sha256": canonical_json_sha256(raw_path),
        "mapping_audit": mapping_meta,
        "normalized_races_sha256": hashlib.sha256(share_path.read_bytes()).hexdigest(),
        "normalized_control_sha256": hashlib.sha256(control_path.read_bytes()).hexdigest(),
        "normalized_control_canonical_json_sha256": canonical_json_sha256(control_path),
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


def verify_market_store_integrity(*, as_of: str | None = None) -> dict[str, Any]:
    """Validate semantic JSON, exact Parquet bytes, and every event mapping."""
    manifest_path = MANIFESTS_DIR / "markets_kalshi.json"
    if not manifest_path.exists():
        return {"ok": False, "reason": "market manifest missing"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("parser_version") != PARSER_VERSION:
            raise ValueError("market parser version is stale")
        if manifest.get("json_hash_mode") != JSON_HASH_MODE:
            raise ValueError("market JSON integrity hash mode is missing or stale")
        if manifest.get("retrieval_date") != manifest.get("available_at"):
            raise ValueError("live market availability differs from retrieval date")
        if as_of and str(manifest.get("available_at") or "") > as_of:
            raise ValueError("market store is newer than requested snapshot")
        paths = {
            "raw": RAW_DIR / "external" / "kalshi_senate.json",
            "races": NORMALIZED_DIR / "markets.parquet",
            "control": NORMALIZED_DIR / "markets_control.json",
        }
        json_fingerprints = {
            "raw_canonical_json_sha256": paths["raw"],
            "normalized_control_canonical_json_sha256": paths["control"],
        }
        for field, path in json_fingerprints.items():
            if (not path.is_file()
                    or canonical_json_sha256(path) != manifest.get(field)):
                raise ValueError(f"market {field} is missing or changed")
        if (not paths["races"].is_file()
                or hashlib.sha256(paths["races"].read_bytes()).hexdigest()
                != manifest.get("normalized_races_sha256")):
            raise ValueError("market normalized_races_sha256 is missing or changed")
        audit_path = NORMALIZED_DIR / "markets_mapping_audit.json"
        audit_meta = manifest["mapping_audit"]
        if (not audit_path.is_file()
                or audit_meta.get("hash_mode") != JSON_HASH_MODE
                or canonical_json_sha256(audit_path)
                != audit_meta.get("canonical_json_sha256")):
            raise ValueError("market mapping audit is missing or changed")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        events = audit["events"]
        if audit.get("parser_version") != PARSER_VERSION:
            raise ValueError("market mapping audit parser is stale")
        expected = {f"{manifest['election_id']}-{state}" for state in SENATE_2026_STATES}
        found = [str(event.get("race_id")) for event in events]
        if set(found) != expected or len(found) != len(expected):
            raise ValueError("market mapping audit does not cover each event exactly once")
        if any(event.get("parser_version") != PARSER_VERSION for event in events):
            raise ValueError("market mapping audit contains a stale event parser")
        if any(event.get("fetch_status") == "fetch_failed" for event in events):
            raise ValueError("market event fetch failed; normalized store is incomplete")
        if any(not event.get("fetch_status") for event in events):
            raise ValueError("market event lacks fetch status")
        if any(not event.get("overlay_enabled") and not event.get("disable_reason") for event in events):
            raise ValueError("disabled market event lacks a reason")
        enabled = [event for event in events if event.get("overlay_enabled")]
        frame = pd.read_parquet(paths["races"])
        if len(frame) and (
            not frame["available_at"].astype(str).eq(manifest["available_at"]).all()
            or frame["retrieved_at"].astype(str).str[:10].gt(manifest["available_at"]).any()
        ):
            raise ValueError("market row was backdated before retrieval")
        if len(frame) != len(enabled) or set(frame.get("race_id", [])) != {
            str(event["race_id"]) for event in enabled
        }:
            raise ValueError("enabled market rows differ from mapping audit")
        if len(frame) and (not frame["parser_version"].eq(PARSER_VERSION).all()
                           or not frame["overlay_enabled"].all()):
            raise ValueError("normalized market rows include stale or disabled mapping")
        byte_hash_diagnostics = {
            "raw_sha256": hashlib.sha256(paths["raw"].read_bytes()).hexdigest()
            == manifest.get("raw_sha256"),
            "normalized_control_sha256": hashlib.sha256(
                paths["control"].read_bytes()
            ).hexdigest() == manifest.get("normalized_control_sha256"),
            "mapping_audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest()
            == audit_meta.get("sha256"),
        }
        return {"ok": True, "parser_version": PARSER_VERSION,
                "json_hash_mode": JSON_HASH_MODE,
                "n_enabled": len(enabled), "n_disabled": len(events) - len(enabled),
                "audit_sha256": audit_meta["canonical_json_sha256"],
                "byte_hash_diagnostics": byte_hash_diagnostics}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": str(exc)}


def load_race_markets(as_of: str | None = None) -> pd.DataFrame:
    path = NORMALIZED_DIR / "markets.parquet"
    if not path.exists() or not verify_market_store_integrity(as_of=as_of)["ok"]:
        return pd.DataFrame()
    df = pd.read_parquet(path)
    # A prior parser can carry a wrong candidate mapping. Never reuse it after
    # a refresh failure or when loading an older workspace artifact.
    if "parser_version" not in df.columns:
        return df.head(0)
    df = df[df["parser_version"] == PARSER_VERSION]
    if "overlay_enabled" not in df.columns or "mapping_audit" not in df.columns:
        return df.head(0)
    df = df[df["overlay_enabled"] == True]  # noqa: E712 - pandas BooleanArray filter
    if as_of and "available_at" in df.columns and len(df):
        df = df[pd.to_datetime(df["available_at"]).dt.date <= pd.Timestamp(as_of).date()]
    return df


def load_control_market(as_of: str | None = None) -> dict[str, Any]:
    path = NORMALIZED_DIR / "markets_control.json"
    if not path.exists() or not verify_market_store_integrity(as_of=as_of)["ok"]:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
