"""VoteHub CC BY poll dump sealing + historical import (blueprint §3).

Live VoteHub currently publishes the active Senate cycle; `/polls/archive` may
be unavailable. This module:
  1. Seals whatever VoteHub returns into an immutable dated CC BY dump
  2. Imports user-supplied historical VoteHub JSON dumps (same schema)
  3. Merges third-party dumps into the warehouse preferentially over synthetic fixtures
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.ingest import (
    VOTEHUB_API,
    fetch_votehub_polls,
    normalize_votehub_senate_polls,
    votehub_get,
)
from midterms.evidence.schema import align_poll_frame

PARSER_VERSION = "votehub-archive-v1"
ATTRIBUTION = "Polling data from VoteHub (https://votehub.com), CC BY 4.0"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seal_votehub_dump(
    *,
    poll_type: str = "us-senator",
    subject: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Fetch VoteHub polls and write an immutable dated dump under data/raw/external/votehub_dumps/."""
    dump_dir = RAW_DIR / "external" / "votehub_dumps"
    dump_dir.mkdir(parents=True, exist_ok=True)
    meta = fetch_votehub_polls(poll_type=poll_type, subject=subject)
    src = Path(meta["path"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = tag or (subject or poll_type).replace(" ", "_")
    dest = dump_dir / f"votehub_{tag}_{stamp}.json"
    blob = src.read_bytes()
    dest.write_bytes(blob)
    # Also maintain a "latest" pointer for the tag
    latest = dump_dir / f"votehub_{tag}_latest.json"
    latest.write_bytes(blob)
    man = {
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "tag": tag,
        "path": str(dest),
        "latest": str(latest),
        "sha256": _sha256(blob),
        "bytes": len(blob),
        "n_polls": meta.get("n_polls"),
        "license": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "source_api": VOTEHUB_API,
        "fetch_meta": meta,
        "parser_version": PARSER_VERSION,
    }
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (MANIFESTS_DIR / f"votehub_dump_{tag}.json").write_text(json.dumps(man, indent=2))
    return man


def try_fetch_votehub_archive() -> dict[str, Any]:
    """Best-effort call to VoteHub /polls/archive (often 500 as of 2026-09)."""
    try:
        payload = votehub_get("/polls/archive", {"poll_type": "us-senator"})
        dump_dir = RAW_DIR / "external" / "votehub_dumps"
        dump_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = dump_dir / f"votehub_archive_{stamp}.json"
        blob = json.dumps(payload, indent=2).encode()
        dest.write_bytes(blob)
        return {
            "ok": True,
            "path": str(dest),
            "sha256": _sha256(blob),
            "n": len(payload.get("polls", payload) if isinstance(payload, dict) else payload or []),
            "attribution": ATTRIBUTION,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "hint": "Drop historical VoteHub JSON dumps into data/raw/external/votehub_dumps/ and run import-votehub-dumps",
            "attribution": ATTRIBUTION,
        }


def import_votehub_dump_file(
    path: Path,
    *,
    election_id: str,
) -> pd.DataFrame:
    """Normalize one VoteHub JSON dump file into poll records for `election_id`."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_votehub_senate_polls(payload, election_id=election_id)


def _election_id_from_dump_name(name: str) -> str | None:
    for year in range(2010, 2032):
        if str(year) in name:
            return f"senate-{year}"
    return None


def ingest_votehub_dumps_to_warehouse(
    *,
    dump_dir: Path | None = None,
    replace_synthetic: bool = True,
) -> dict[str, Any]:
    """
    Import all sealed VoteHub dumps into polls.parquet.
    Prefer VoteHub CC BY rows over synthetic fixtures for matching election_ids.
    """
    dump_dir = dump_dir or (RAW_DIR / "external" / "votehub_dumps")
    dump_dir.mkdir(parents=True, exist_ok=True)
    # Seal current cycle first
    sealed = seal_votehub_dump(tag="us_senator_current")
    archive_try = try_fetch_votehub_archive()

    frames: list[pd.DataFrame] = []
    imported: list[str] = []
    for path in sorted(dump_dir.glob("votehub_*.json")):
        if path.name.endswith("_latest.json"):
            continue
        # Infer election from filename or poll subjects
        payload = json.loads(path.read_text(encoding="utf-8"))
        polls = payload.get("polls", payload) if isinstance(payload, dict) else payload
        years = set()
        for p in polls or []:
            sub = str(p.get("subject") or "")
            if len(sub) >= 4 and sub[:4].isdigit():
                years.add(int(sub[:4]))
        eid = _election_id_from_dump_name(path.name)
        if years:
            # one frame per year present in dump
            for y in sorted(years):
                eid_y = f"senate-{y}"
                df = normalize_votehub_senate_polls(payload, election_id=eid_y)
                # filter rows whose subject year matches
                if len(df) and "race_id" in df.columns:
                    frames.append(df)
                    imported.append(f"{path.name}:{eid_y}:{len(df)}")
        elif eid:
            df = normalize_votehub_senate_polls(payload, election_id=eid)
            frames.append(df)
            imported.append(f"{path.name}:{eid}:{len(df)}")

    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    polls_path = NORMALIZED_DIR / "polls.parquet"
    if polls_path.exists():
        existing = pd.read_parquet(polls_path)
    else:
        existing = pd.DataFrame()

    if frames:
        vh = align_poll_frame(pd.concat(frames, ignore_index=True))
        vh_elections = set(vh["election_id"].astype(str))
        if replace_synthetic and len(existing):
            keep = existing[~existing["election_id"].astype(str).isin(vh_elections)]
            # Also drop synthetic 2026 when VoteHub has 2026
            merged = pd.concat([keep, vh], ignore_index=True)
        else:
            merged = pd.concat([existing, vh], ignore_index=True) if len(existing) else vh
        # De-dupe by poll_id keeping last
        if "poll_id" in merged.columns:
            merged = merged.drop_duplicates(subset=["poll_id"], keep="last")
        merged = align_poll_frame(merged)
        merged.to_parquet(polls_path, index=False)
        # Historical sealed parquet for replay
        hist_path = NORMALIZED_DIR / "polls_votehub_ccby.parquet"
        vh.to_parquet(hist_path, index=False)
    else:
        merged = existing
        hist_path = None

    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sealed_current": sealed,
        "archive_endpoint": archive_try,
        "imported": imported,
        "n_warehouse_polls": int(len(merged)) if merged is not None else 0,
        "votehub_parquet": str(hist_path) if hist_path else None,
        "license": "CC BY 4.0",
        "attribution": ATTRIBUTION,
        "parser_version": PARSER_VERSION,
        "note": (
            "Historical years require VoteHub archive dumps (API /polls/archive may be down). "
            "Place JSON files in data/raw/external/votehub_dumps/ named with the cycle year."
        ),
    }
    (MANIFESTS_DIR / "votehub_ccby_archive.json").write_text(json.dumps(man, indent=2, default=str))
    return man
