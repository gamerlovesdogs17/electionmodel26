"""OpenFEC / FEC bulk campaign-finance ingest → Dem fundraising share by Senate race."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from datetime import date, datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.tickets import TICKETS_2026

PARSER_VERSION = "fec-v1"
OPENFEC = "https://api.open.fec.gov/v1"
# FEC all-candidates summary (no API key; same underlying filings as OpenFEC).
WEBALL_URL = "https://www.fec.gov/files/bulk-downloads/{cycle}/weball{yy}.zip"
# Pipe fields: see https://www.fec.gov/campaign-finance-data/all-candidates-file-description/
WEBALL_COLS = [
    "candidate_id",
    "name",
    "incumbent_challenger_status",
    "party_code",
    "party",
    "receipts",
    "transfers_from_auth",
    "disbursements",
    "transfers_to_auth",
    "cash_on_hand_bop",
    "cash_on_hand_end_period",
    "candidate_contrib",
    "candidate_loans",
    "other_loans",
    "candidate_loan_repay",
    "other_loan_repay",
    "debts_owed_by",
    "indiv_contrib",
    "state",
    "district",
    "special_election_status",
    "primary_election_status",
    "runoff_election_status",
    "general_election_status",
    "general_election_pct",
    "other_pol_cmte_contrib",
    "party_contrib",
    "coverage_end_date",
    "indiv_refunds",
    "cmte_refunds",
]


def _fec_api_key() -> str:
    return os.environ.get("FEC_API_KEY") or "DEMO_KEY"


def _get(path: str, params: dict[str, Any], *, retries: int = 4) -> dict[str, Any]:
    q = dict(params)
    q["api_key"] = _fec_api_key()
    url = f"{OPENFEC}{path}?{urlencode(q, doseq=True)}"
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.2 (research)"})
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            last_exc = exc
            if int(getattr(exc, "code", 0) or 0) == 429 and attempt + 1 < retries:
                time.sleep(1.5 * (2**attempt))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(0.75 * (attempt + 1))
                continue
            raise
    raise last_exc or RuntimeError("OpenFEC request failed")


def _normalize_party(raw: str) -> str:
    p = (raw or "").strip().upper()
    if p.startswith("DEM") or p in {"D", "DEM"}:
        return "DEM"
    if p.startswith("REP") or p in {"R", "REP"}:
        return "REP"
    return p[:3]


def _coverage_iso(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10] if len(s) >= 10 else None


def fetch_senate_bulk_totals(cycle: int = 2026) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    FEC weball bulk download — first-party alternative when OpenFEC rate-limits.

    Same official filings as the API; no key required.
    """
    yy = f"{int(cycle) % 100:02d}"
    url = WEBALL_URL.format(cycle=int(cycle), yy=yy)
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.2 (research)"})
    try:
        with urlopen(req, timeout=90) as resp:
            blob = resp.read()
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), {"error": f"bulk:{exc}", "n": 0, "source": "fec_weball"}
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
            text = zf.read(name).decode("latin-1")
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), {"error": f"bulk_unzip:{exc}", "n": 0, "source": "fec_weball"}

    rows: list[dict[str, Any]] = []
    retrieved = datetime.now(timezone.utc).isoformat()
    for line in text.splitlines():
        if not line or not line.startswith("S"):
            continue
        parts = line.split("|")
        if len(parts) < 20:
            continue
        # Pad short rows so zipfile schema drift doesn't crash.
        while len(parts) < len(WEBALL_COLS):
            parts.append("")
        rec = dict(zip(WEBALL_COLS, parts[: len(WEBALL_COLS)]))
        state = (rec.get("state") or "").strip().upper()
        if len(state) != 2:
            cid = rec.get("candidate_id") or ""
            state = cid[2:4].upper() if len(cid) >= 4 else ""
        cov = _coverage_iso(str(rec.get("coverage_end_date") or ""))
        try:
            receipts = float(rec.get("receipts") or 0.0)
        except ValueError:
            receipts = 0.0
        try:
            disbursements = float(rec.get("disbursements") or 0.0)
        except ValueError:
            disbursements = 0.0
        try:
            cash = float(rec.get("cash_on_hand_end_period") or 0.0)
        except ValueError:
            cash = 0.0
        rows.append(
            {
                "cycle": cycle,
                "candidate_id": rec.get("candidate_id"),
                "name": rec.get("name"),
                "party": _normalize_party(str(rec.get("party") or "")),
                "state": state,
                "receipts": receipts,
                "disbursements": disbursements,
                "cash_on_hand_end_period": cash,
                "coverage_start_date": None,
                "coverage_end_date": cov,
                "last_file_date": cov,
                "amendment_indicator": None,
                "filing_id": rec.get("candidate_id"),
                "available_at": cov or datetime.now(timezone.utc).date().isoformat(),
                "retrieved_at": retrieved,
                "parser_version": PARSER_VERSION,
                "source": "fec_weball",
            }
        )
    return pd.DataFrame(rows), {"n": len(rows), "source": "fec_weball", "url": url, "bytes": len(blob)}


def fetch_senate_candidate_totals(cycle: int = 2026) -> tuple[pd.DataFrame, dict[str, Any]]:
    """OpenFEC totals first; on failure/empty, FEC weball bulk (no API key)."""
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
                        "coverage_start_date": r.get("coverage_start_date"),
                        "coverage_end_date": r.get("coverage_end_date"),
                        "last_file_date": r.get("last_file_date") or r.get("candidate_inactive_date"),
                        "amendment_indicator": r.get("amendment_indicator")
                        or r.get("candidate_election_year"),
                        "filing_id": r.get("candidate_id"),
                        "available_at": r.get("coverage_end_date")
                        or r.get("last_file_date")
                        or datetime.now(timezone.utc).date().isoformat(),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "parser_version": PARSER_VERSION,
                        "source": "openfec",
                    }
                )
            pagination = payload.get("pagination") or {}
            if page >= int(pagination.get("pages") or 1):
                break
            page += 1
            time.sleep(0.35)  # stay under DEMO_KEY / shared-key rate limits
    except Exception as exc:  # noqa: BLE001
        # Partial DEMO_KEY pages are worse than the full weball dump.
        bulk, bmeta = fetch_senate_bulk_totals(cycle)
        if len(bulk):
            bmeta = {**bmeta, "openfec_error": str(exc), "openfec_partial_n": len(rows)}
            return bulk, bmeta
        if rows:
            return pd.DataFrame(rows), {"error": str(exc), "n": len(rows), "source": "openfec"}
        return pd.DataFrame(), {**bmeta, "openfec_error": str(exc)}
    if rows:
        return pd.DataFrame(rows), {"pages": page, "n": len(rows), "source": "openfec"}
    bulk, bmeta = fetch_senate_bulk_totals(cycle)
    bmeta = {**bmeta, "openfec_error": "empty_results"}
    return bulk, bmeta


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


def curated_fundraising_shares(election_id: str = "senate-2026") -> pd.DataFrame:
    """
    Non-publication lean-anchored estimates when both OpenFEC and FEC weball fail.

    Explicit curated tier — blocked for PUBLICATION_ELIGIBLE runs.
    """
    from midterms.evidence.official_ballot import BASE_LEANS

    year = election_id.split("-")[-1]
    rows = []
    states = list(TICKETS_2026.keys()) if str(year) == "2026" else list(BASE_LEANS.keys())
    for st in states:
        lean = float(BASE_LEANS.get(st, 0.0))
        share = 0.5 + max(-0.25, min(0.25, lean / 80.0))
        rows.append(
            {
                "election_id": election_id,
                "state": st,
                "race_id": f"senate-{year}-{st}",
                "fundraising_share": round(float(share), 3),
                "dem_receipts": round(2_000_000 * share, 2),
                "rep_receipts": round(2_000_000 * (1 - share), 2),
                "source": "curated_fec_browse_estimate",
                "source_url": "https://www.fec.gov/data/browse-data/?tab=candidates",
                "available_at": f"{year}-09-01",
                "parser_version": PARSER_VERSION,
                "tier": "curated",
            }
        )
    return pd.DataFrame(rows)


def shares_from_totals(
    totals: pd.DataFrame,
    election_id: str,
    cycle: int,
    *,
    as_of: str | None = None,
) -> pd.DataFrame:
    """
    Build Dem fundraising shares with amendment / coverage discipline.

    When multiple totals rows exist for a candidate, keep the latest
    coverage_end_date still known by `as_of` (blueprint finance amendment chain).
    """
    if totals.empty:
        return curated_fundraising_shares(election_id)
    work = totals.copy()
    if as_of and "available_at" in work.columns:
        work = work[pd.to_datetime(work["available_at"]).dt.date <= date.fromisoformat(str(as_of)[:10])]
    if work.empty:
        return curated_fundraising_shares(election_id)
    # Prefer latest coverage window per candidate (amendment / restatement chain)
    chain_by_cand: dict[str, list[str]] = {}
    if "candidate_id" in work.columns and "coverage_end_date" in work.columns:
        for cid, cg in work.groupby("candidate_id"):
            dates = sorted(
                {str(x)[:10] for x in cg["coverage_end_date"].dropna().tolist() if str(x)}
            )
            chain_by_cand[str(cid)] = dates
        work = work.sort_values(
            [c for c in ("coverage_end_date", "last_file_date", "available_at") if c in work.columns]
        )
        work = work.groupby("candidate_id", as_index=False).tail(1)
    rows = []
    for state, g in work.groupby("state"):
        dem = g[g["party"].astype(str).str.upper().str.startswith("DEM")]
        rep = g[g["party"].astype(str).str.upper().str.startswith("REP")]
        dem_rec = float(dem["receipts"].max()) if len(dem) else 0.0
        rep_rec = float(rep["receipts"].max()) if len(rep) else 0.0
        dem_cash = float(dem["cash_on_hand_end_period"].max()) if len(dem) and "cash_on_hand_end_period" in dem else 0.0
        rep_cash = float(rep["cash_on_hand_end_period"].max()) if len(rep) and "cash_on_hand_end_period" in rep else 0.0
        dem_disb = float(dem["disbursements"].max()) if len(dem) and "disbursements" in dem else 0.0
        rep_disb = float(rep["disbursements"].max()) if len(rep) and "disbursements" in rep else 0.0
        total = dem_rec + rep_rec
        share = dem_rec / total if total > 0 else 0.5
        cash_tot = dem_cash + rep_cash
        cash_share = dem_cash / cash_tot if cash_tot > 0 else 0.5
        chains = []
        for cid in g.get("candidate_id", pd.Series(dtype=str)).dropna().astype(str).tolist():
            chains.extend(chain_by_cand.get(cid, []))
        cov_dates = sorted(set(chains))
        src_vals = (
            set(g["source"].dropna().astype(str))
            if "source" in g.columns
            else set()
        )
        if "openfec" in src_vals:
            src = "openfec"
        elif "fec_weball" in src_vals:
            src = "fec_weball"
        else:
            src = "openfec"
        rows.append(
            {
                "election_id": election_id,
                "state": state,
                "race_id": f"senate-{cycle}-{state}",
                "fundraising_share": round(float(share), 3),
                "cash_share": round(float(cash_share), 3),
                "dem_receipts": dem_rec,
                "rep_receipts": rep_rec,
                "dem_cash_on_hand": dem_cash,
                "rep_cash_on_hand": rep_cash,
                "dem_disbursements": dem_disb,
                "rep_disbursements": rep_disb,
                "matched_window_id": f"cycle-{cycle}-coverage-end-asof",
                "amendment_chain": ">".join(cov_dates) if cov_dates else "latest_totals_row",
                "n_filings_in_chain": int(len(cov_dates)),
                "source": src,
                "available_at": str(g["available_at"].max()),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def _finance_tier_and_url(shares: pd.DataFrame, meta: dict[str, Any]) -> tuple[str, str]:
    sources = set(shares["source"].astype(str)) if len(shares) else set()
    if "openfec" in sources:
        return "aggregator", "https://api.open.fec.gov/v1/candidates/totals/"
    if "fec_weball" in sources:
        url = str(meta.get("url") or "https://www.fec.gov/files/bulk-downloads/")
        return "first_party", url
    return "curated", "https://www.fec.gov/data/browse-data/?tab=candidates"


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
    tier, source_url = _finance_tier_and_url(shares, meta if isinstance(meta, dict) else {})
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "cycle": cycle,
        "n_shares": int(len(shares)),
        "source_mix": shares["source"].value_counts().to_dict() if len(shares) else {},
        "fetch_meta": meta,
        "parser_version": PARSER_VERSION,
        "source_url": source_url,
        "tier": tier,
    }
    man_path = MANIFESTS_DIR / "fundraising_shares.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {"shares": str(share_path), "raw": str(raw_path), "manifest": str(man_path), **man}


def load_fundraising_shares() -> pd.DataFrame:
    path = NORMALIZED_DIR / "fundraising_shares.parquet"
    if not path.exists():
        write_finance_store()
    return pd.read_parquet(path)


def attach_fundraising_to_races(
    races: pd.DataFrame, *, as_of: str | date | None = None
) -> pd.DataFrame:
    shares = load_fundraising_shares()
    out = races.copy()
    if "fundraising_share" not in out.columns:
        out["fundraising_share"] = 0.5
    if as_of is not None and "available_at" in shares.columns:
        as_of_d = date.fromisoformat(str(as_of)[:10]) if not isinstance(as_of, date) else as_of
        shares = shares[
            pd.to_datetime(shares["available_at"]).dt.date <= as_of_d
        ]
    by_state = shares.set_index("state")["fundraising_share"].to_dict() if len(shares) else {}
    by_race = shares.set_index("race_id")["fundraising_share"].to_dict() if len(shares) else {}
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
