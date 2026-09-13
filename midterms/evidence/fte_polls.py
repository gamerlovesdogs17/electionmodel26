"""FiveThirtyEight / ABC News Senate polls as redistributable historical archive.

VoteHub's documented API has no /polls/archive (only live /polls for the current
cycle). For complete-cycle replay we ingest the public Senate poll table mirrored
at FiveThirtyEight Datasette (CC BY 4.0 — attribute FiveThirtyEight / ABC News).

Primary source:
  https://fivethirtyeight.datasettes.com/polls/senate_polls.csv
Docs:
  https://github.com/fivethirtyeight/data/blob/master/polls/README.md
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.candidates import canonicalize_pollster
from midterms.evidence.schema import align_poll_frame, empty_poll_row

PARSER_VERSION = "fte-senate-polls-v1"
ATTRIBUTION = (
    "Polling data from FiveThirtyEight / ABC News (CC BY 4.0). "
    "https://github.com/fivethirtyeight/data/tree/master/polls"
)
DATASETTE_CSV = "https://fivethirtyeight.datasettes.com/polls/senate_polls.csv?_size=max"
DATASETTE_SQL = "https://fivethirtyeight.datasettes.com/polls.csv?sql="

STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.8 (research; FTE CC-BY)"})
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def fetch_fte_senate_polls_csv(*, dest: Path | None = None) -> dict[str, Any]:
    """Download the Datasette mirror of FTE senate_polls and seal it."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = dest or (RAW_DIR / "external" / "fte_senate_polls.csv")
    blob = _get(DATASETTE_CSV)
    # Guard against HTML interstitial
    if blob.lstrip()[:1] == b"<":
        raise RuntimeError("FTE datasette returned HTML, not CSV — try again later")
    dest.write_bytes(blob)
    man = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "url": DATASETTE_CSV,
        "path": str(dest),
        "sha256": _sha256(blob),
        "bytes": len(blob),
        "license": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "parser_version": PARSER_VERSION,
        "note": (
            "Alternative to VoteHub historical archive (VoteHub documents no /polls/archive). "
            "Mirror may lag the latest cycle."
        ),
    }
    (MANIFESTS_DIR / "fte_senate_polls_fetch.json").write_text(json.dumps(man, indent=2))
    return man


def _state_abbr(state: object) -> str | None:
    if state is None or (isinstance(state, float) and pd.isna(state)):
        return None
    s = str(state).strip()
    if len(s) == 2:
        return s.upper()
    return STATE_NAME_TO_ABBR.get(s.lower())


def _parse_day(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def normalize_fte_senate_polls(
    df: pd.DataFrame | None = None,
    *,
    path: Path | None = None,
    cycles: list[int] | None = None,
) -> pd.DataFrame:
    """
    Collapse candidate-level FTE rows into two-party margin poll records.
    Keeps general-stage polls only.
    """
    if df is None:
        path = path or (RAW_DIR / "external" / "fte_senate_polls.csv")
        if not path.exists():
            fetch_fte_senate_polls_csv(dest=path)
        df = pd.read_csv(path)

    if cycles:
        df = df[df["cycle"].astype(int).isin(cycles)]

    if "stage" in df.columns:
        df = df[df["stage"].astype(str).str.lower().eq("general")]

    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    # Group by poll question
    group_cols = [c for c in ("poll_id", "question_id", "cycle", "state", "pollster", "end_date", "start_date", "created_at", "sample_size", "population", "methodology", "url", "sponsors") if c in df.columns]
    for key, g in df.groupby(group_cols, dropna=False):
        meta = dict(zip(group_cols, key if isinstance(key, tuple) else (key,)))
        st = _state_abbr(meta.get("state"))
        if not st:
            continue
        try:
            cycle = int(meta.get("cycle"))
        except (TypeError, ValueError):
            continue
        election_id = f"senate-{cycle}"
        dem = g[g["candidate_party"].astype(str).str.upper().str.startswith("DEM")]
        rep = g[g["candidate_party"].astype(str).str.upper().str.startswith("REP")]
        if dem.empty or rep.empty:
            continue
        dem_pct = float(dem["pct"].max())
        rep_pct = float(rep["pct"].max())
        tot = dem_pct + rep_pct
        if tot <= 0:
            continue
        dem_tw = 100.0 * dem_pct / tot
        rep_tw = 100.0 * rep_pct / tot
        margin = dem_tw - rep_tw
        field_end = _parse_day(meta.get("end_date"))
        field_start = _parse_day(meta.get("start_date")) or field_end
        published = _parse_day(meta.get("created_at")) or field_end
        if not published or not field_end:
            continue
        pollster = canonicalize_pollster(str(meta.get("pollster") or "unknown"))
        sample = meta.get("sample_size")
        try:
            sample_size = int(float(sample)) if sample == sample and sample is not None else None
        except (TypeError, ValueError):
            sample_size = None
        pop = str(meta.get("population") or "lv").upper()
        if pop in {"A", "ADULT", "ADULTS"}:
            pop = "A"
        elif pop in {"RV", "REGISTERED"}:
            pop = "RV"
        else:
            pop = "LV"
        poll_id = f"fte-{meta.get('poll_id')}-{meta.get('question_id')}"
        payload = f"{poll_id}|{margin}|{sample_size}".encode()
        rows.append(
            empty_poll_row(
                poll_id=poll_id,
                study_id=f"fte-study-{meta.get('poll_id')}",
                release_version=1,
                pollster_id=pollster,
                sponsor_id=str(meta.get("sponsors") or "none") or "none",
                source_url=str(meta.get("url") or "https://projects.fivethirtyeight.com/polls/"),
                raw_hash=_sha256(payload),
                field_start=field_start,
                field_end=field_end,
                published_at=published,
                retrieved_at=now,
                valid_from=published,
                available_at=published,
                event_time=field_end,
                election_id=election_id,
                office="US_SENATE",
                state=st,
                race_id=f"{election_id}-{st}",
                population=pop,
                sample_size=sample_size,
                mode=str(meta.get("methodology") or "") or None,
                dem_share=round(dem_tw, 3),
                rep_share=round(rep_tw, 3),
                undecided=0.0,
                other_share=0.0,
                two_party_margin=round(margin, 3),
                partisan=False,
                exclusion_status="include",
                parser_version=PARSER_VERSION,
                normalized_at=now,
                geography_version_id="state-usps-v1",
                candidate_set_version="fte-ticket",
                question_id=str(meta.get("question_id")),
            )
        )

    return align_poll_frame(pd.DataFrame(rows)) if rows else align_poll_frame(pd.DataFrame())


def ingest_fte_historical_into_warehouse(
    *,
    cycles: list[int] | None = None,
    replace_synthetic: bool = True,
) -> dict[str, Any]:
    """
    Fetch FTE senate polls and merge into polls.parquet.
    Prefer FTE rows over synthetic fixtures for matching historical election_ids.
    Does not remove VoteHub 2026 live polls.
    """
    fetch_meta = fetch_fte_senate_polls_csv()
    fte = normalize_fte_senate_polls(cycles=cycles)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    out_fte = NORMALIZED_DIR / "polls_fte_historical.parquet"
    fte.to_parquet(out_fte, index=False)

    polls_path = NORMALIZED_DIR / "polls.parquet"
    existing = pd.read_parquet(polls_path) if polls_path.exists() else pd.DataFrame()

    if fte.empty:
        man = {
            "ok": False,
            "error": "no FTE polls normalized",
            "fetch": fetch_meta,
            "parser_version": PARSER_VERSION,
        }
        (MANIFESTS_DIR / "fte_historical_ingest.json").write_text(json.dumps(man, indent=2))
        return man

    fte_elections = set(fte["election_id"].astype(str))
    # Never clobber live 2026 VoteHub with FTE (mirror may be incomplete)
    fte_elections = {e for e in fte_elections if e != "senate-2026"}
    fte = fte[fte["election_id"].astype(str).isin(fte_elections)]

    if replace_synthetic and len(existing):
        keep = existing[~existing["election_id"].astype(str).isin(fte_elections)]
        merged = pd.concat([keep, fte], ignore_index=True)
    else:
        merged = pd.concat([existing, fte], ignore_index=True) if len(existing) else fte

    if "poll_id" in merged.columns:
        merged = merged.drop_duplicates(subset=["poll_id"], keep="last")
    merged = align_poll_frame(merged)
    merged.to_parquet(polls_path, index=False)

    # Refresh sealed historical archive used by warehouse merge helpers
    from midterms.evidence.historical_polls import freeze_historical_polls_from_warehouse

    try:
        freeze = freeze_historical_polls_from_warehouse(merged)
    except OSError as exc:
        freeze = {"ok": False, "error": str(exc)}

    man = {
        "ok": True,
        "fetched_at": fetch_meta.get("fetched_at"),
        "n_fte_polls": int(len(fte)),
        "elections": sorted(fte_elections),
        "n_warehouse_polls": int(len(merged)),
        "paths": {"fte_parquet": str(out_fte), "warehouse": str(polls_path)},
        "freeze": freeze,
        "license": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "parser_version": PARSER_VERSION,
        "note": "VoteHub remains the live 2026 source; FTE Datasette covers historical cycles.",
    }
    (MANIFESTS_DIR / "fte_historical_ingest.json").write_text(json.dumps(man, indent=2, default=str))
    return man
