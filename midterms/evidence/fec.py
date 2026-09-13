"""OpenFEC campaign-finance ingest → Dem fundraising share by Senate race."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.tickets import TICKETS_2026

PARSER_VERSION = "fec-v1"
OPENFEC = "https://api.open.fec.gov/v1"


def _fec_api_key() -> str:
    return os.environ.get("FEC_API_KEY") or "DEMO_KEY"


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    q = dict(params)
    q["api_key"] = _fec_api_key()
    url = f"{OPENFEC}{path}?{urlencode(q, doseq=True)}"
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.2 (research)"})
    with urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_senate_candidate_totals(cycle: int = 2026) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Best-effort OpenFEC totals for Senate candidates in `cycle`."""
    rows: list[dict[str, Any]] = []
    page = 1
    try:
        while page <= 20:
            payload = _get(
                "/candidates/totals/",
                {
                    "cycle": cycle,
                    "office": "S",
                    "is_active_candidate": "true",
                    "sort": "-receipts",
                    "per_page": 100,
                    "page": page,
                },
            )
            results = payload.get("results") or []
            if not results:
                break
            for r in results:
                rows.append(
                    {
                        "cycle": cycle,
                        "candidate_id": r.get("candidate_id"),
                        "name": r.get("name"),
                        "party": (r.get("party") or "")[:3],
                        "state": r.get("state"),
                        "receipts": float(r.get("receipts") or 0.0),
                        "disbursements": float(r.get("disbursements") or 0.0),
                        "cash_on_hand_end_period": float(r.get("cash_on_hand_end_period") or 0.0),
                        "coverage_end_date": r.get("coverage_end_date"),
                        "available_at": r.get("coverage_end_date")
                        or datetime.now(timezone.utc).date().isoformat(),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "parser_version": PARSER_VERSION,
                    }
                )
            pagination = payload.get("pagination") or {}
            if page >= int(pagination.get("pages") or 1):
                break
            page += 1
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(rows), {"error": str(exc), "n": len(rows)}
    return pd.DataFrame(rows), {"pages": page, "n": len(rows)}


def fixture_fundraising_shares(election_id: str = "senate-2026") -> pd.DataFrame:
    """Deterministic shares when OpenFEC is unavailable."""
    rows = []
    for st, _ticket in TICKETS_2026.items():
        h = int(hashlib.sha256(f"{election_id}|{st}".encode()).hexdigest()[:8], 16)
        share = 0.25 + (h % 5000) / 10000.0
        rows.append(
            {
                "election_id": election_id,
                "state": st,
                "race_id": f"senate-2026-{st}" if election_id == "senate-2026" else f"{election_id}-{st}",
                "fundraising_share": round(share, 3),
                "dem_receipts": round(1_000_000 * share, 2),
                "rep_receipts": round(1_000_000 * (1 - share), 2),
                "source": "fixture_hash",
                "available_at": "2026-09-01",
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def shares_from_totals(totals: pd.DataFrame, election_id: str, cycle: int) -> pd.DataFrame:
    if totals.empty:
        return fixture_fundraising_shares(election_id)
    rows = []
    for state, g in totals.groupby("state"):
        dem = g[g["party"].astype(str).str.upper().str.startswith("DEM")]
        rep = g[g["party"].astype(str).str.upper().str.startswith("REP")]
        dem_rec = float(dem["receipts"].max()) if len(dem) else 0.0
        rep_rec = float(rep["receipts"].max()) if len(rep) else 0.0
        total = dem_rec + rep_rec
        share = dem_rec / total if total > 0 else 0.5
        rows.append(
            {
                "election_id": election_id,
                "state": state,
                "race_id": f"senate-{cycle}-{state}",
                "fundraising_share": round(float(share), 3),
                "dem_receipts": dem_rec,
                "rep_receipts": rep_rec,
                "source": "openfec",
                "available_at": str(g["available_at"].max()),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def write_finance_store(election_id: str = "senate-2026", cycle: int = 2026) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    totals, meta = fetch_senate_candidate_totals(cycle)
    shares = shares_from_totals(totals, election_id, cycle)
    raw_path = RAW_DIR / f"fec_totals_{cycle}.csv"
    share_path = NORMALIZED_DIR / "fundraising_shares.parquet"
    if len(totals):
        totals.to_csv(raw_path, index=False)
    else:
        pd.DataFrame().to_csv(raw_path, index=False)
    shares.to_parquet(share_path, index=False)
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "cycle": cycle,
        "n_shares": int(len(shares)),
        "source_mix": shares["source"].value_counts().to_dict() if len(shares) else {},
        "fetch_meta": meta,
        "parser_version": PARSER_VERSION,
    }
    man_path = MANIFESTS_DIR / "fundraising_shares.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {"shares": str(share_path), "raw": str(raw_path), "manifest": str(man_path), **man}


def load_fundraising_shares() -> pd.DataFrame:
    path = NORMALIZED_DIR / "fundraising_shares.parquet"
    if not path.exists():
        write_finance_store()
    return pd.read_parquet(path)


def attach_fundraising_to_races(races: pd.DataFrame) -> pd.DataFrame:
    shares = load_fundraising_shares()
    out = races.copy()
    if "fundraising_share" not in out.columns:
        out["fundraising_share"] = 0.5
    by_state = shares.set_index("state")["fundraising_share"].to_dict()
    by_race = shares.set_index("race_id")["fundraising_share"].to_dict()
    vals = []
    for _, r in out.iterrows():
        if r["race_id"] in by_race:
            vals.append(float(by_race[r["race_id"]]))
        elif r["state"] in by_state:
            vals.append(float(by_state[r["state"]]))
        else:
            cur = r.get("fundraising_share")
            vals.append(float(cur) if pd.notna(cur) else 0.5)
    out["fundraising_share"] = vals
    return out
