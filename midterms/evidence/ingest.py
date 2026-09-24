"""External poll discovery and normalization (VoteHub + optional archives).

VoteHub Polling API is CC BY 4.0: https://votehub.com/polls/api/
Attribution: Polling data from VoteHub (https://votehub.com).
VoteHub Pollster Scorecards: https://votehub.com/polls/pollster-scorecards/
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import numpy as np

from midterms.config import DEMO_ELECTION_ID, MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR, REGIONS
from midterms.evidence.candidates import (
    candidate_party,
    canonicalize_pollster,
    normalize_candidate_key,
)
from midterms.evidence.ratings import build_rating_lookup, rating_for, write_normalized_ratings
from midterms.evidence.schema import POLL_COLUMNS, empty_poll_row

VOTEHUB_API = "https://api.votehub.com"
PARSER_VERSION = "votehub-ingest-v1"

STATE_NAME_TO_ABBR = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

# Documented preferred endpoints — may 403/redirect depending on network/ToS.
CANDIDATE_URLS = {
    "medsl_election_context_2018": "https://raw.githubusercontent.com/MEDSL/2018-elections-unoffical/master/election-context-2018.csv",
    "fte_pollster_ratings_combined": "https://raw.githubusercontent.com/fivethirtyeight/data/master/pollster-ratings/pollster-ratings-combined.csv",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_url(name: str, url: str, dest_dir: Path | None = None) -> dict:
    dest_dir = dest_dir or RAW_DIR / "external"
    dest_dir.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.1 (research; VoteHub CC-BY attribution)"})
    with urlopen(req, timeout=90) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")
    # Choose extension by content
    ext = ".json" if "json" in (content_type or "") or data[:1] in (b"{", b"[") else ".bin"
    if name.endswith((".json", ".csv", ".bin")):
        out = dest_dir / name
    else:
        out = dest_dir / f"{name}{ext}"
    out.write_bytes(data)
    meta = {
        "name": name,
        "url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sha256": _sha256_bytes(data),
        "bytes": len(data),
        "content_type": content_type,
        "path": str(out),
        "attribution": "VoteHub / upstream source — see license notes in README",
    }
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (MANIFESTS_DIR / f"fetch_{Path(name).stem}.json").write_text(json.dumps(meta, indent=2))
    return meta


def votehub_get(path: str, params: dict[str, Any] | None = None) -> Any:
    qs = f"?{urlencode(params)}" if params else ""
    url = f"{VOTEHUB_API}{path}{qs}"
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.1 (research; CC-BY attribution to VoteHub)"})
    with urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_votehub_polls(*, poll_type: str = "us-senator", subject: str | None = None) -> dict:
    params: dict[str, Any] = {"poll_type": poll_type}
    if subject:
        params["subject"] = subject
    payload = votehub_get("/polls", params)
    if isinstance(payload, list):
        payload = {"polls": payload}
    name = f"votehub_{poll_type.replace('-', '_')}"
    if subject:
        name += f"_{re.sub(r'[^a-z0-9]+', '_', subject.lower()).strip('_')}"
    dest = RAW_DIR / "external"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{name}.json"
    blob = json.dumps(payload, indent=2).encode()
    out.write_bytes(blob)
    meta = {
        "name": name,
        "url": f"{VOTEHUB_API}/polls",
        "params": params,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sha256": _sha256_bytes(blob),
        "bytes": len(blob),
        "n_polls": len(payload.get("polls", [])),
        "path": str(out),
        "license": "CC BY 4.0",
        "attribution": "Polling data from VoteHub (https://votehub.com)",
    }
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (MANIFESTS_DIR / f"fetch_{name}.json").write_text(json.dumps(meta, indent=2))
    return meta


def try_fetch_preferred() -> list[dict]:
    results = []
    for name, url in CANDIDATE_URLS.items():
        try:
            results.append(fetch_url(name, url))
        except Exception as exc:  # noqa: BLE001 — best-effort external fetch
            results.append({"name": name, "url": url, "error": str(exc)})
    for poll_type, subject in [
        ("us-senator", None),
        ("generic-ballot", "2026"),
    ]:
        try:
            results.append(fetch_votehub_polls(poll_type=poll_type, subject=subject))
        except Exception as exc:  # noqa: BLE001
            results.append({"name": f"votehub_{poll_type}", "error": str(exc)})
    try:
        pollsters = votehub_get("/pollsters")
        dest = RAW_DIR / "external" / "votehub_pollsters.json"
        dest.write_text(json.dumps(pollsters, indent=2))
        results.append({"name": "votehub_pollsters", "n": len(pollsters), "path": str(dest)})
    except Exception as exc:  # noqa: BLE001
        results.append({"name": "votehub_pollsters", "error": str(exc)})
    try:
        results.append({"name": "pollster_ratings", "path": str(write_normalized_ratings())})
    except Exception as exc:  # noqa: BLE001
        results.append({"name": "pollster_ratings", "error": str(exc)})
    return results


def _parse_subject_state(subject: str) -> tuple[str | None, bool]:
    """Return (state_abbr, is_primary). Primary subjects contain Democratic/Republican."""
    s = str(subject or "").strip()
    is_primary = bool(re.search(r"\b(Democratic|Republican)\b", s, re.I))
    # "2026 Texas" / "2026 North Carolina"
    m = re.match(r"^(?:20\d{2}\s+)?(.+?)(?:\s+(?:Democratic|Republican))?$", s, re.I)
    if not m:
        return None, is_primary
    name = m.group(1).strip().lower()
    return STATE_NAME_TO_ABBR.get(name), is_primary


def _two_party_from_answers(answers: list[dict]) -> dict[str, Any] | None:
    dem_pct = 0.0
    rep_pct = 0.0
    dem_name = None
    rep_name = None
    other = 0.0
    undecided = 0.0
    for a in answers or []:
        choice = str(a.get("choice") or "")
        pct = float(a.get("pct") or 0.0)
        key = normalize_candidate_key(choice)
        if key in {"undecided", "unsure", "not sure"}:
            undecided += pct
            continue
        party = candidate_party(choice)
        if party == "D":
            if pct >= dem_pct:
                dem_pct, dem_name = pct, choice
        elif party == "R":
            if pct >= rep_pct:
                rep_pct, rep_name = pct, choice
        else:
            other += pct
    if dem_pct <= 0 or rep_pct <= 0:
        return None
    total = dem_pct + rep_pct
    dem_tw = 100.0 * dem_pct / total
    rep_tw = 100.0 * rep_pct / total
    return {
        "dem_share": round(dem_tw, 3),
        "rep_share": round(rep_tw, 3),
        "two_party_margin": round(dem_tw - rep_tw, 3),
        "undecided": round(undecided, 3),
        "other_share": round(other, 3),
        "dem_candidate": dem_name,
        "rep_candidate": rep_name,
    }


def normalize_votehub_senate_polls(
    payload: dict | list | None = None,
    *,
    path: Path | None = None,
    election_id: str = DEMO_ELECTION_ID,
) -> pd.DataFrame:
    """Convert VoteHub us-senator polls into blueprint poll records."""
    if payload is None:
        path = path or (RAW_DIR / "external" / "votehub_senate_polls.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
    polls = payload.get("polls", payload) if isinstance(payload, dict) else payload
    ratings = build_rating_lookup()
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    skipped = {"primary": 0, "no_state": 0, "no_twoway": 0}

    for p in polls or []:
        subject = str(p.get("subject") or "")
        state, is_primary = _parse_subject_state(subject)
        if is_primary:
            skipped["primary"] += 1
            continue
        if not state:
            skipped["no_state"] += 1
            continue
        tw = _two_party_from_answers(p.get("answers") or [])
        if not tw:
            skipped["no_twoway"] += 1
            continue

        pollster_raw = str(p.get("pollster") or "unknown")
        pollster = canonicalize_pollster(pollster_raw)
        rating = rating_for(pollster, ratings)
        field_start = p.get("start_date")
        field_end = p.get("end_date") or field_start
        published = p.get("created_at") or field_end
        available_at = published
        sponsors = p.get("sponsors") or []
        sponsor_id = ",".join(sponsors) if sponsors else "none"
        pop = str(p.get("population") or "").upper() or "LV"
        if pop.lower() in {"a", "adult", "adults"}:
            pop = "A"
        elif pop.lower() in {"rv", "registered"}:
            pop = "RV"
        elif pop.lower() in {"lv", "likely"}:
            pop = "LV"

        sample_size = int(float(p.get("sample_size") or 0) or 0)
        raw_hash = _sha256_bytes(json.dumps(p, sort_keys=True).encode())
        poll_id = f"vh-{p.get('id')}"
        row = empty_poll_row(
            poll_id=poll_id,
            study_id=f"vh-study-{p.get('id')}",
            release_version=1,
            pollster_id=pollster,
            sponsor_id=sponsor_id,
            source_url=p.get("url") or f"{VOTEHUB_API}/polls/{p.get('id')}",
            raw_hash=raw_hash,
            field_start=field_start,
            field_end=field_end,
            published_at=published,
            corrected_at=None,
            retrieved_at=now,
            valid_from=published,
            valid_to=None,
            available_at=available_at,
            event_time=field_end,
            election_id=election_id,
            office="US_SENATE",
            state=state,
            race_id=f"{election_id}-{state}",
            population=pop,
            sample_size=sample_size if sample_size > 0 else None,
            mode=None,
            dem_share=tw["dem_share"],
            rep_share=tw["rep_share"],
            undecided=tw["undecided"],
            other_share=tw["other_share"],
            two_party_margin=tw["two_party_margin"],
            partisan=bool(p.get("partisan")) if p.get("partisan") is not None else bool(p.get("internal")),
            exclusion_status="include",
            exclusion_reason=None,
            parser_version=PARSER_VERSION,
            normalized_at=now,
            supersedes=None,
        )
        # Rating fields carried alongside for model priors (not in core POLL_COLUMNS contract)
        row["quality_weight"] = rating.quality_weight
        row["house_effect_prior"] = rating.house_effect_dem_pp
        row["extra_sd_prior"] = rating.extra_sd_prior
        row["pollster_grade"] = rating.grade
        row["rating_source"] = rating.source
        row["dem_candidate"] = tw["dem_candidate"]
        row["rep_candidate"] = tw["rep_candidate"]
        row["region"] = REGIONS.get(state)
        rows.append(row)

    df = pd.DataFrame(rows)
    meta = {
        "parser_version": PARSER_VERSION,
        "n_normalized": int(len(df)),
        "skipped": skipped,
        "unknown_candidates": sorted(
            {
                normalize_candidate_key(a.get("choice", ""))
                for p in (polls or [])
                for a in (p.get("answers") or [])
                if candidate_party(str(a.get("choice") or "")) is None
                and normalize_candidate_key(str(a.get("choice") or ""))
                not in {"undecided", "unsure", "not sure", ""}
            }
        ),
        "attribution": "Polling data from VoteHub (https://votehub.com), CC BY 4.0",
        "scorecards": "https://votehub.com/polls/pollster-scorecards/",
    }
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (MANIFESTS_DIR / "normalize_votehub_senate.json").write_text(json.dumps(meta, indent=2))
    return df


def generic_ballot_latest(
    path: Path | None = None,
    *,
    as_of: str | None = None,
) -> float | None:
    """Backward-compatible alias: trailing-window aggregate, not a single poll."""
    agg = generic_ballot_aggregate(path=path, as_of=as_of)
    return None if agg is None else float(agg["margin"])


def generic_ballot_aggregate(
    path: Path | None = None,
    *,
    as_of: str | None = None,
    window_days: int = 21,
    winsor_abs: float = 8.0,
) -> dict[str, Any] | None:
    """
    VoteHub generic-ballot Dem−Rep margin (pp), ENOP-ish trailing average.

    Uses polls in the last `window_days` on/before as_of (else all eligible),
    Winsorizes outlier margins, and weights by recency × √N. Avoids letting a
    single YouGov/etc. print dominate fundamentals.
    """
    path = path or (RAW_DIR / "external" / "votehub_generic_ballot_2026.json")
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    polls = payload if isinstance(payload, list) else payload.get("polls", [])
    cutoff = as_of
    rows: list[dict[str, Any]] = []
    for p in polls:
        answers = {normalize_candidate_key(a["choice"]): float(a["pct"]) for a in p.get("answers") or []}
        dem = (
            answers.get("democrats")
            or answers.get("democrat")
            or answers.get("democratic")
            or answers.get("dem")
            or answers.get("d")
        )
        rep = (
            answers.get("republicans")
            or answers.get("republican")
            or answers.get("rep")
            or answers.get("r")
        )
        if dem is None or rep is None:
            for k, v in answers.items():
                if dem is None and ("democ" in k or k == "dem"):
                    dem = v
                if rep is None and ("repub" in k or k == "rep"):
                    rep = v
        if dem is None or rep is None:
            continue
        end = str(p.get("end_date") or p.get("created_at") or "")[:10]
        published = str(p.get("created_at") or end)[:10]
        if cutoff and published > cutoff[:10]:
            continue
        if not end:
            continue
        total = dem + rep
        raw = 100.0 * (dem - rep) / total if total else dem - rep
        # Headline D−R (not two-party renorm) — closer to public GB reporting
        headline = float(dem) - float(rep)
        sample = p.get("sample_size")
        try:
            n = float(sample) if sample is not None else 1000.0
        except (TypeError, ValueError):
            n = 1000.0
        if not np.isfinite(n) or n <= 0:
            n = 1000.0
        rows.append(
            {
                "end": end,
                "margin_renorm": float(raw),
                "margin_headline": float(headline),
                "n": n,
                "pollster": str(p.get("pollster") or p.get("pollster_id") or ""),
            }
        )
    if not rows:
        return None

    import math
    from datetime import date, timedelta

    ref = date.fromisoformat((cutoff or max(r["end"] for r in rows))[:10])
    windowed = [
        r
        for r in rows
        if (ref - date.fromisoformat(r["end"])).days <= window_days
        and (ref - date.fromisoformat(r["end"])).days >= 0
    ]
    if not windowed:
        # Fall back to most recent 5 polls overall
        windowed = sorted(rows, key=lambda r: r["end"], reverse=True)[:5]

    margins = []
    weights = []
    for r in windowed:
        m = float(np.clip(r["margin_headline"], -winsor_abs, winsor_abs))
        age = max((ref - date.fromisoformat(r["end"])).days, 0)
        recency = math.exp(-math.log(2.0) * age / max(window_days / 2.0, 1.0))
        w = recency * math.sqrt(max(r["n"], 100.0) / 1000.0)
        margins.append(m)
        weights.append(w)
    w_arr = np.asarray(weights, dtype=float)
    m_arr = np.asarray(margins, dtype=float)
    if float(w_arr.sum()) <= 0:
        margin = float(np.median(m_arr))
    else:
        margin = float(np.average(m_arr, weights=w_arr))
    return {
        "margin": margin,
        "n_polls": len(windowed),
        "window_days": window_days,
        "winsor_abs": winsor_abs,
        "as_of": cutoff,
        "ref_date": ref.isoformat(),
        "raw_median": float(np.median(m_arr)),
        "raw_mean": float(np.mean(m_arr)),
        "method": "trailing_weighted_headline",
        "note": "Headline D-R pp (not two-party renorm); Winsorized; recency*sqrt(N) weights",
    }


def merge_live_polls_into_warehouse(
    *,
    election_id: str = DEMO_ELECTION_ID,
    replace_synthetic_for_election: bool = True,
    payload_path: Path | None = None,
) -> dict[str, Any]:
    """Normalize VoteHub polls and merge into normalized polls.parquet."""
    write_normalized_ratings()
    live = normalize_votehub_senate_polls(path=payload_path, election_id=election_id)
    if live.empty:
        return {"n_live": 0, "warning": "no VoteHub polls normalized"}

    # Ensure core columns exist for warehouse
    for col in POLL_COLUMNS:
        if col not in live.columns:
            live[col] = None
    core = live[POLL_COLUMNS].copy()

    polls_path = NORMALIZED_DIR / "polls.parquet"
    if polls_path.exists():
        existing = pd.read_parquet(polls_path)
    else:
        existing = pd.DataFrame(columns=POLL_COLUMNS)

    if replace_synthetic_for_election and len(existing):
        existing = existing[
            ~(
                (existing["election_id"] == election_id)
                & (existing["parser_version"].astype(str).str.startswith("fixtures"))
            )
        ]
        # also drop prior live rows for same election to avoid dupes
        existing = existing[existing["election_id"] != election_id]

    merged = pd.concat([existing, core], ignore_index=True)
    # Deduplicate by poll_id keeping latest normalized_at
    if "normalized_at" in merged.columns:
        merged = merged.sort_values("normalized_at")
    merged = merged.drop_duplicates(subset=["poll_id"], keep="last")
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(polls_path, index=False)
    # Keep extended live table for diagnostics / model priors
    live.to_parquet(NORMALIZED_DIR / "polls_live_votehub.parquet", index=False)
    raw_csv = RAW_DIR / "polls_live_votehub.csv"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    live.to_csv(raw_csv, index=False)

    gb = generic_ballot_latest()
    summary = {
        "n_live": int(len(live)),
        "n_warehouse_polls": int(len(merged)),
        "election_id": election_id,
        "states": sorted(live["state"].dropna().unique().tolist()),
        "generic_ballot_dem_margin": gb,
        "paths": {
            "polls": str(polls_path),
            "live": str(NORMALIZED_DIR / "polls_live_votehub.parquet"),
            "ratings": str(NORMALIZED_DIR / "pollster_ratings.parquet"),
        },
        "attribution": "Polling data from VoteHub (https://votehub.com), CC BY 4.0",
    }
    (MANIFESTS_DIR / "merge_live_polls.json").write_text(json.dumps(summary, indent=2))
    return summary
