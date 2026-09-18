"""FiveThirtyEight / ABC News Senate polls as redistributable historical archive.

VoteHub's documented API has no /polls/archive (only live /polls for the current
cycle). For complete-cycle replay we ingest FTE's public Senate poll tables
(CC BY 4.0 — attribute FiveThirtyEight / ABC News).

Primary sources (tried in order):
  1. Sealed local CSVs under data/raw/external/
  2. Wayback Machine copies of projects.fivethirtyeight.com/polls-page/data/
  3. GitHub mirror (simonw/fivethirtyeight-polls) — often incomplete for late cycles
  4. Datasette pagination (may 503 / 1000-row cap)

Docs: https://github.com/fivethirtyeight/data/blob/master/polls/README.md
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.candidates import canonicalize_pollster
from midterms.evidence.schema import align_poll_frame, empty_poll_row

PARSER_VERSION = "fte-senate-polls-v2"
ATTRIBUTION = (
    "Polling data from FiveThirtyEight / ABC News (CC BY 4.0). "
    "https://github.com/fivethirtyeight/data/tree/master/polls"
)
FTE_HISTORICAL_URL = (
    "https://projects.fivethirtyeight.com/polls-page/data/senate_polls_historical.csv"
)
FTE_CURRENT_URL = "https://projects.fivethirtyeight.com/polls-page/data/senate_polls.csv"
GITHUB_MIRROR_CSV = (
    "https://raw.githubusercontent.com/simonw/fivethirtyeight-polls/main/senate_polls.csv"
)
DATASETTE_JSON = "https://fivethirtyeight.datasettes.com/polls/senate_polls.json"

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


def _get(url: str, *, timeout: int = 180) -> bytes:
    req = Request(
        url,
        headers={"User-Agent": "midterms-senate-model/0.9 (research; FTE CC-BY)"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _is_csv_blob(blob: bytes) -> bool:
    head = blob.lstrip()[:80].lower()
    if not head or head[:1] == b"<":
        return False
    text = head.decode("utf-8", errors="ignore")
    return "poll_id" in text or "question_id" in text or "cycle" in text


def _wayback_latest_csv(original_url: str) -> tuple[bytes, str] | None:
    """Return (csv_bytes, wayback_url) for the largest 200 text/csv snapshot."""
    cdx = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={quote(original_url, safe='')}&output=json&filter=statuscode:200"
        "&filter=mimetype:text/csv&limit=20"
    )
    try:
        raw = _get(cdx, timeout=60)
        rows = json.loads(raw)
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None
    if not rows or len(rows) < 2:
        return None
    best = max(rows[1:], key=lambda r: int(r[6]) if len(r) > 6 and str(r[6]).isdigit() else 0)
    ts = best[1]
    wb = f"https://web.archive.org/web/{ts}id_/{original_url}"
    try:
        blob = _get(wb, timeout=180)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if not _is_csv_blob(blob):
        return None
    return blob, wb


def _fetch_datasette_paginated() -> bytes | None:
    rows: list[dict[str, Any]] = []
    next_token: str | None = None
    for _ in range(30):
        url = f"{DATASETTE_JSON}?_size=1000"
        if next_token:
            url += f"&_next={quote(str(next_token), safe='')}"
        try:
            data = json.loads(_get(url, timeout=120))
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError, OSError):
            return None
        cols = data.get("columns") or []
        batch = data.get("rows") or []
        if not batch:
            break
        if cols and isinstance(batch[0], list):
            rows.extend(dict(zip(cols, r)) for r in batch)
        elif isinstance(batch[0], dict):
            rows.extend(batch)
        else:
            return None
        next_token = data.get("next")
        if not next_token:
            break
        time.sleep(0.2)
    if not rows:
        return None
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8")


def fetch_fte_senate_polls_csv(*, dest: Path | None = None, force: bool = False) -> dict[str, Any]:
    """
    Seal a complete FTE Senate poll CSV (historical + current when available).

    Prefers already-sealed local files when large enough (>= 2000 rows) unless force=True.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = dest or (RAW_DIR / "external" / "fte_senate_polls.csv")
    hist_path = RAW_DIR / "external" / "fte_senate_polls_historical.csv"
    cur_path = RAW_DIR / "external" / "fte_senate_polls_current.csv"

    sources_tried: list[str] = []
    blob: bytes | None = None
    source_url = ""

    if not force and dest.exists():
        existing = dest.read_bytes()
        if _is_csv_blob(existing):
            try:
                n = len(pd.read_csv(io.BytesIO(existing)))
            except Exception:  # noqa: BLE001
                n = 0
            if n >= 2000:
                man = {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "url": "local:fte_senate_polls.csv",
                    "path": str(dest),
                    "sha256": _sha256(existing),
                    "bytes": len(existing),
                    "n_rows": n,
                    "license": "CC BY 4.0",
                    "attribution": ATTRIBUTION,
                    "parser_version": PARSER_VERSION,
                    "note": "Reused sealed local CSV (>=2000 rows).",
                    "sources_tried": ["local"],
                }
                (MANIFESTS_DIR / "fte_senate_polls_fetch.json").write_text(json.dumps(man, indent=2))
                return man

    parts: list[pd.DataFrame] = []
    for p in (hist_path, cur_path):
        if p.exists() and _is_csv_blob(p.read_bytes()):
            parts.append(pd.read_csv(p))
            sources_tried.append(f"local:{p.name}")
    if parts:
        df = pd.concat(parts, ignore_index=True).drop_duplicates()
        if len(df) >= 2000 or force:
            blob = df.to_csv(index=False).encode("utf-8")
            source_url = "local:historical+current"

    if blob is None:
        sources_tried.append("wayback")
        frames: list[pd.DataFrame] = []
        urls: list[str] = []
        for original in (FTE_HISTORICAL_URL, FTE_CURRENT_URL):
            hit = _wayback_latest_csv(original)
            if not hit:
                continue
            b, wb = hit
            frames.append(pd.read_csv(io.BytesIO(b)))
            urls.append(wb)
            if "historical" in original:
                hist_path.write_bytes(b)
            else:
                cur_path.write_bytes(b)
        if frames:
            df = pd.concat(frames, ignore_index=True).drop_duplicates()
            blob = df.to_csv(index=False).encode("utf-8")
            source_url = "|".join(urls)

    if blob is None:
        sources_tried.append("github_mirror")
        try:
            b = _get(GITHUB_MIRROR_CSV)
            if _is_csv_blob(b):
                blob = b
                source_url = GITHUB_MIRROR_CSV
        except (HTTPError, URLError, TimeoutError, OSError):
            pass

    if blob is None:
        sources_tried.append("datasette_paginated")
        blob = _fetch_datasette_paginated()
        source_url = DATASETTE_JSON

    if blob is None or not _is_csv_blob(blob):
        raise RuntimeError(
            "Unable to fetch FTE Senate polls CSV "
            f"(tried {sources_tried}). Place a sealed CSV at {dest}."
        )

    dest.write_bytes(blob)
    try:
        n_rows = int(len(pd.read_csv(io.BytesIO(blob))))
    except Exception:  # noqa: BLE001
        n_rows = -1
    man = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "url": source_url,
        "path": str(dest),
        "sha256": _sha256(blob),
        "bytes": len(blob),
        "n_rows": n_rows,
        "license": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "parser_version": PARSER_VERSION,
        "sources_tried": sources_tried,
        "note": (
            "VoteHub has no /polls/archive. Prefer Wayback / sealed FTE polls-page CSVs "
            "over the incomplete Datasette/GitHub mirrors."
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


def _party_series(g: pd.DataFrame) -> pd.Series:
    if "candidate_party" in g.columns:
        return g["candidate_party"].astype(str).str.upper()
    if "party" in g.columns:
        return g["party"].astype(str).str.upper()
    return pd.Series([""] * len(g), index=g.index)


def _seat_class_token(seat_name: object) -> str | None:
    if seat_name is None or (isinstance(seat_name, float) and pd.isna(seat_name)):
        return None
    s = str(seat_name).strip().upper().replace("CLASS", "").strip()
    if s in {"I", "II", "III"}:
        return s
    return None


def map_fte_to_official_race_id(
    *,
    cycle: int,
    state: str,
    seat_name: object = None,
) -> tuple[str | None, str | None]:
    """Map an FTE poll question to an official race_id (race_id, contest_kind)."""
    from midterms.evidence.official_ballot import CYCLE_META, contested_contests

    meta = CYCLE_META.get(cycle)
    if meta is None and cycle != 2026:
        return None, None
    contests = contested_contests(cycle)
    by_state = [c for c in contests if c["state"] == state]
    if not by_state:
        return None, None
    seat = _seat_class_token(seat_name)
    up = (meta or {}).get("seat_class_up")
    specials = [
        c
        for c in by_state
        if c.get("kind") in {"special", "unexpired"} or c.get("term_type") == "unexpired"
    ]
    regulars = [c for c in by_state if c.get("kind") == "regular" and c.get("term_type") != "unexpired"]
    # Prefer the class that is up this cycle for regulars; only route to special/unexpired
    # when FTE seat_name points at a different class (GA 2020 Class III, NE 2024 Class II).
    if seat and up and seat != up and specials:
        for sp in specials:
            if str(sp.get("seat_class")) == seat:
                return sp["race_id"], "special"
        return specials[0]["race_id"], "special"
    if regulars:
        return regulars[0]["race_id"], "regular"
    if specials:
        return specials[0]["race_id"], "special"
    return None, None


def companion_unexpired_race_ids(
    cycle: int,
    state: str,
    primary_race_id: str,
    *,
    dem_name: str | None = None,
    rep_name: str | None = None,
) -> list[str]:
    """Same-state unexpired ballots that share the polled matchup (not distinct races)."""
    from midterms.evidence.official_ballot import contested_contests
    from midterms.evidence.official_ledger import nominees_from_ledger

    try:
        noms = nominees_from_ledger(cycle)
    except Exception:  # noqa: BLE001
        noms = {}
    out: list[str] = []
    for c in contested_contests(cycle):
        if c.get("state") != state:
            continue
        rid = str(c.get("race_id") or "")
        if rid == primary_race_id:
            continue
        if not (
            c.get("term_type") == "unexpired"
            or c.get("kind") == "unexpired"
            or rid.endswith("-unexpired")
        ):
            continue
        nom = noms.get(rid) or {}
        # Only alias when the unexpired ballot is the same D/R pairing (CA dual-ballot).
        if dem_name and nom.get("dem_candidate_name"):
            if str(nom["dem_candidate_name"]).lower() not in str(dem_name).lower() and str(
                dem_name
            ).lower() not in str(nom["dem_candidate_name"]).lower():
                continue
        if rep_name and nom.get("rep_candidate_name"):
            if str(nom["rep_candidate_name"]).lower() not in str(rep_name).lower() and str(
                rep_name
            ).lower() not in str(nom["rep_candidate_name"]).lower():
                continue
        out.append(rid)
    return out


def normalize_fte_senate_polls(
    df: pd.DataFrame | None = None,
    *,
    path: Path | None = None,
    cycles: list[int] | None = None,
    include_hypothetical: bool = False,
    stages: tuple[str, ...] = ("general", "runoff"),
) -> pd.DataFrame:
    """
    Collapse candidate-level FTE rows into two-party margin poll records.

    Keeps candidate identity, maps to official race_ids, and drops hypothetical
    matchups by default (audit P0.3).
    """
    if df is None:
        path = path or (RAW_DIR / "external" / "fte_senate_polls.csv")
        if not path.exists():
            fetch_fte_senate_polls_csv(dest=path)
        df = pd.read_csv(path)

    if cycles:
        df = df[df["cycle"].astype(int).isin(cycles)]

    if "stage" in df.columns:
        stage_ok = df["stage"].astype(str).str.lower().isin({s.lower() for s in stages})
        df = df[stage_ok]

    if "hypothetical" in df.columns and not include_hypothetical:
        hyp = df["hypothetical"].fillna(False)
        if hyp.dtype != bool:
            hyp = hyp.astype(str).str.lower().isin({"1", "true", "yes", "t"})
        df = df[~hyp]

    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    group_cols = [
        c
        for c in (
            "poll_id",
            "question_id",
            "cycle",
            "state",
            "pollster",
            "end_date",
            "start_date",
            "created_at",
            "sample_size",
            "population",
            "methodology",
            "url",
            "sponsors",
            "seat_name",
            "seat_number",
            "stage",
            "hypothetical",
            "notes",
        )
        if c in df.columns
    ]
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
        race_id, contest_kind = map_fte_to_official_race_id(
            cycle=cycle, state=st, seat_name=meta.get("seat_name")
        )
        if not race_id:
            continue

        parties = _party_series(g)
        dem = g[parties.str.startswith("DEM")]
        rep = g[parties.str.startswith("REP")]
        if dem.empty or rep.empty:
            continue
        dem_row = dem.loc[dem["pct"].astype(float).idxmax()]
        rep_row = rep.loc[rep["pct"].astype(float).idxmax()]
        dem_pct = float(dem_row["pct"])
        rep_pct = float(rep_row["pct"])
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
        dem_id = str(dem_row.get("candidate_id") or "")
        rep_id = str(rep_row.get("candidate_id") or "")
        dem_name = str(dem_row.get("candidate_name") or dem_row.get("answer") or "")
        rep_name = str(rep_row.get("candidate_name") or rep_row.get("answer") or "")
        matchup_id = f"{dem_id}|{rep_id}" if dem_id and rep_id else f"{dem_name}|{rep_name}"
        hyp_raw = meta.get("hypothetical")
        if isinstance(hyp_raw, bool):
            hypothetical = hyp_raw
        else:
            hypothetical = str(hyp_raw).lower() in {"1", "true", "yes", "t"}
        poll_id = f"fte-{meta.get('poll_id')}-{meta.get('question_id')}"
        payload = f"{poll_id}|{margin}|{sample_size}|{matchup_id}".encode()
        base = empty_poll_row(
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
            race_id=race_id,
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
            candidate_set_version=f"fte:{matchup_id}",
            question_id=str(meta.get("question_id")),
            dem_candidate_id=dem_id or None,
            dem_candidate_name=dem_name or None,
            rep_candidate_id=rep_id or None,
            rep_candidate_name=rep_name or None,
            matchup_id=matchup_id,
            hypothetical=hypothetical,
            contest_kind=contest_kind,
            seat_name=str(meta.get("seat_name") or "") or None,
            election_stage=str(meta.get("stage") or "general").lower(),
        )
        rows.append(base)
        # Dual-ballot same-day unexpired seats (CA/NE 2024, CA 2022): FTE rarely
        # distinguishes; clone the regular-row poll onto the companion race_id.
        if contest_kind == "regular":
            for companion in companion_unexpired_race_ids(
                cycle, st, race_id, dem_name=dem_name, rep_name=rep_name
            ):
                clone = dict(base)
                clone["poll_id"] = f"{poll_id}-{companion.split('-')[-1]}"
                clone["race_id"] = companion
                clone["contest_kind"] = "unexpired"
                rows.append(clone)

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

    from midterms.evidence.historical_polls import freeze_historical_polls_from_warehouse

    try:
        freeze = freeze_historical_polls_from_warehouse(merged)
    except OSError as exc:
        freeze = {"ok": False, "error": str(exc)}

    try:
        from midterms.validation.poll_coverage import write_poll_coverage_report

        coverage = write_poll_coverage_report()
    except Exception as exc:  # noqa: BLE001
        coverage = {"ok": False, "error": str(exc)}

    man = {
        "ok": True,
        "fetched_at": fetch_meta.get("fetched_at"),
        "n_fte_polls": int(len(fte)),
        "elections": sorted(fte_elections),
        "n_warehouse_polls": int(len(merged)),
        "paths": {"fte_parquet": str(out_fte), "warehouse": str(polls_path)},
        "freeze": freeze,
        "poll_coverage": {
            "ok": coverage.get("ok"),
            "failures": coverage.get("failures"),
            "path": coverage.get("path"),
        },
        "license": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "parser_version": PARSER_VERSION,
        "note": "VoteHub remains the live 2026 source; FTE polls-page / Wayback covers historical cycles.",
        "synthetic": False,
        "cycle_manifests": write_cycle_poll_manifests(fte),
    }
    (MANIFESTS_DIR / "fte_historical_ingest.json").write_text(json.dumps(man, indent=2, default=str))
    return man


def write_cycle_poll_manifests(polls: pd.DataFrame) -> dict[str, Any]:
    """Per-cycle sealed poll coverage for validation fail-closed checks."""
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {}
    if polls is None or polls.empty:
        return out
    for eid, g in polls.groupby(polls["election_id"].astype(str)):
        if str(eid) == "senate-2026":
            continue
        src = g["source_url"].astype(str) if "source_url" in g.columns else pd.Series([""] * len(g))
        synthetic_n = int(src.str.contains("synthetic", case=False, na=False).sum())
        n_identity = (
            int(g["dem_candidate_name"].notna().sum()) if "dem_candidate_name" in g.columns else 0
        )
        block = {
            "election_id": str(eid),
            "n_polls": int(len(g)),
            "n_races": int(g["race_id"].nunique()) if "race_id" in g.columns else 0,
            "n_with_candidate_identity": n_identity,
            "n_synthetic_marked": synthetic_n,
            "primary_source": "fte" if synthetic_n == 0 else "mixed",
            "field_end_min": str(pd.to_datetime(g["field_end"], errors="coerce").min().date())
            if "field_end" in g.columns and len(g)
            else None,
            "field_end_max": str(pd.to_datetime(g["field_end"], errors="coerce").max().date())
            if "field_end" in g.columns and len(g)
            else None,
            "sha256_poll_ids": _sha256(",".join(sorted(g["poll_id"].astype(str))).encode())
            if "poll_id" in g.columns
            else None,
        }
        out[str(eid)] = block
        (MANIFESTS_DIR / f"polls_{eid}.json").write_text(json.dumps(block, indent=2))
    (MANIFESTS_DIR / "historical_poll_cycles.json").write_text(json.dumps(out, indent=2))
    return out


def assert_real_historical_polls(
    election_id: str,
    *,
    allow_synthetic: bool = False,
    min_polls: int = 5,
    require_coverage: bool = True,
) -> dict[str, Any]:
    """
    Fail closed when a holdout cycle would replay on synthetic fixtures
    or (by default) when official-contest / nominee coverage fails (P0.3).
    """
    from midterms.config import CYCLES

    year = int(str(election_id).split("-")[-1]) if "-" in str(election_id) else None
    man_path = MANIFESTS_DIR / f"polls_{election_id}.json"
    fte_path = NORMALIZED_DIR / "polls_fte_historical.parquet"
    hist_path = NORMALIZED_DIR / "polls_historical.parquet"
    polls_path = NORMALIZED_DIR / "polls.parquet"

    n = 0
    synthetic = True
    source = "missing"
    if man_path.exists():
        block = json.loads(man_path.read_text())
        n = int(block.get("n_polls") or 0)
        synthetic = int(block.get("n_synthetic_marked") or 0) > 0 or block.get("primary_source") != "fte"
        source = "cycle_manifest"
        if block.get("primary_source") == "fte" and n >= min_polls:
            synthetic = False
    elif fte_path.exists():
        df = pd.read_parquet(fte_path)
        g = df[df["election_id"].astype(str) == election_id]
        n = int(len(g))
        source = "fte_parquet"
        synthetic = n < min_polls
    elif hist_path.exists() or polls_path.exists():
        path = hist_path if hist_path.exists() else polls_path
        df = pd.read_parquet(path)
        g = df[df["election_id"].astype(str) == election_id]
        n = int(len(g))
        source = path.name
        if n < min_polls:
            synthetic = True
        elif "source_url" in g.columns:
            urls = g["source_url"].astype(str)
            if urls.str.contains("fivethirtyeight|datasette|fte-|projects.fivethirtyeight", case=False, na=False).any():
                synthetic = False
                source = "fte_like_urls"
            else:
                synthetic = bool(urls.str.contains("synthetic", case=False, na=False).mean() > 0.5)
        else:
            synthetic = True

    info: dict[str, Any] = {
        "election_id": election_id,
        "year": year,
        "n_polls": n,
        "synthetic": synthetic,
        "source": source,
        "allow_synthetic": allow_synthetic,
        "in_cycles": year in CYCLES if year else False,
    }
    if synthetic and not allow_synthetic and year is not None and year >= 2020:
        raise ValueError(
            f"Historical polls for {election_id} appear synthetic or thin (n={n}, source={source}). "
            "Ingest FTE CC BY archive (`midterms ingest-fte-polls`) or pass allow_synthetic=True for CI."
        )
    if synthetic and not allow_synthetic and n < min_polls:
        raise ValueError(
            f"Historical polls for {election_id} are missing or synthetic "
            f"(n={n}, source={source}). Run `midterms ingest-fte-polls` or pass allow_synthetic=True."
        )
    if synthetic:
        info["warning"] = "synthetic_or_pre_fte_era"

    if require_coverage and not allow_synthetic and year is not None and year >= 2018:
        from midterms.validation.poll_coverage import assert_poll_coverage

        info["coverage"] = assert_poll_coverage(election_id, allow_thin=False)
    return info
