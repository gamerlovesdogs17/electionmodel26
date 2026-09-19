"""Redistributable historical Senate poll archive (research fixtures + freeze).

Blueprint §3 / §10: historical as-of replay needs sealed poll vintages.
When live redistributable archives are unavailable, we freeze synthetic
cycle polls into an immutable hashed archive so backtests are reproducible.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.schema import POLL_COLUMNS, empty_poll_row

PARSER_VERSION = "historical-polls-v1"


def _ensure_optional_poll_cols(df: pd.DataFrame) -> pd.DataFrame:
    defaults = {
        "geography_version_id": "state-usps-v1",
        "candidate_set_version": "ticket-v1",
        "question_id": "generic_two_way",
        "frame": None,
        "recruitment": None,
        "language": "en",
        "design_effect": 1.0,
        "leaners_included": True,
        "multiway": False,
        "questionnaire_hash": None,
    }
    out = df.copy()
    for k, v in defaults.items():
        if k not in out.columns:
            out[k] = v
    return out


def freeze_historical_polls_from_warehouse(polls: pd.DataFrame | None = None) -> dict[str, Any]:
    """Write immutable historical poll archive from current warehouse polls (non-2026)."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    if polls is None:
        path = NORMALIZED_DIR / "polls.parquet"
        if not path.exists():
            from midterms.evidence.fixtures import build_fixtures

            build_fixtures()
        polls = pd.read_parquet(NORMALIZED_DIR / "polls.parquet")

    hist = polls[polls["election_id"].astype(str) != "senate-2026"].copy()
    hist = _ensure_optional_poll_cols(hist)
    # Keep schema columns that exist
    cols = [c for c in POLL_COLUMNS if c in hist.columns] + [
        c
        for c in (
            "geography_version_id",
            "candidate_set_version",
            "question_id",
            "frame",
            "recruitment",
            "language",
            "design_effect",
            "leaners_included",
            "multiway",
            "questionnaire_hash",
        )
        if c in hist.columns
    ]
    hist = hist[list(dict.fromkeys(cols))]

    src = hist["source_url"].astype(str) if "source_url" in hist.columns else pd.Series([""] * len(hist))
    n_synthetic = int(src.str.contains("synthetic", case=False, na=False).sum())
    n_fte = int(src.str.contains("fivethirtyeight|datasette|fte", case=False, na=False).sum())
    primary = "fte" if n_fte > 0 and n_synthetic == 0 else ("mixed" if n_fte and n_synthetic else "synthetic")

    raw_path = RAW_DIR / "external" / "senate_historical_polls.json"
    records = hist.to_dict(orient="records")
    payload = {
        "parser_version": PARSER_VERSION,
        "license": (
            "CC BY 4.0 (FiveThirtyEight / ABC News)"
            if primary == "fte"
            else "synthetic-research-fixture (redistributable for CI/replay)"
        ),
        "primary_source": primary,
        "n_synthetic": n_synthetic,
        "n_fte_like": n_fte,
        "note": (
            "Sealed historical poll vintages for complete-cycle as-of replay. "
            "Prefer FTE CC BY ingest (`ingest-fte-polls`); VoteHub has no /polls/archive."
        ),
        "n": len(records),
        "elections": sorted(hist["election_id"].unique()) if len(hist) else [],
        "rows": records,
    }
    text = json.dumps(payload, default=str)
    digest = hashlib.sha256(text.encode()).hexdigest()
    try:
        raw_path.write_text(text, encoding="utf-8")
    except OSError:
        # OneDrive / Windows lock fallback
        alt = RAW_DIR / "external" / f"senate_historical_polls_{digest[:8]}.json"
        alt.write_text(text, encoding="utf-8")
        raw_path = alt
    out = NORMALIZED_DIR / "polls_historical.parquet"
    hist.to_parquet(out, index=False)
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": int(len(hist)),
        "elections": payload["elections"],
        "sha256": digest,
        "parser_version": PARSER_VERSION,
        "primary_source": primary,
        "n_synthetic": n_synthetic,
        "paths": {"raw": str(raw_path), "normalized": str(out)},
    }
    (MANIFESTS_DIR / "historical_polls.json").write_text(json.dumps(man, indent=2))
    return man


def merge_historical_polls(polls: pd.DataFrame) -> pd.DataFrame:
    """Prefer sealed FTE (then historical archive) for non-2026 elections."""
    from midterms.evidence.schema import align_poll_frame

    live = (
        polls[polls["election_id"].astype(str) == "senate-2026"]
        if polls is not None and len(polls)
        else pd.DataFrame()
    )
    other = (
        polls[polls["election_id"].astype(str) != "senate-2026"]
        if polls is not None and len(polls)
        else pd.DataFrame()
    )

    preferred: pd.DataFrame | None = None
    fte_path = NORMALIZED_DIR / "polls_fte_historical.parquet"
    hist_path = NORMALIZED_DIR / "polls_historical.parquet"
    for path in (fte_path, hist_path):
        if not path.exists():
            continue
        cand = pd.read_parquet(path)
        if cand.empty:
            continue
        # Never let a fully-synthetic archive beat a non-synthetic FTE snapshot.
        src = cand["source_url"].astype(str) if "source_url" in cand.columns else pd.Series([])
        n_synth = int(src.str.contains("synthetic", case=False, na=False).sum()) if len(src) else 0
        if path == hist_path and preferred is not None and n_synth == len(cand):
            continue
        if preferred is None or (path == fte_path and n_synth == 0):
            preferred = cand
            if path == fte_path and n_synth == 0:
                break

    if preferred is None or preferred.empty:
        return polls if polls is not None else preferred

    arch_elections = set(preferred["election_id"].astype(str))
    keep_other = other[~other["election_id"].astype(str).isin(arch_elections)] if len(other) else other
    merged = pd.concat([live, keep_other, preferred], ignore_index=True)
    return align_poll_frame(merged)


def inject_correction_versions(polls: pd.DataFrame, rng_seed: int = 42) -> pd.DataFrame:
    """
    Create bitemporal correction rows for ~5% of historical polls (release_version bumps).
    Enables valid_from/valid_to correction tests.
    """
    import numpy as np

    if polls.empty:
        return polls
    rng = np.random.default_rng(rng_seed)
    rows = [polls]
    hist = polls[polls["election_id"].astype(str) != "senate-2026"]
    if hist.empty:
        return polls
    idxs = hist.index.to_numpy()
    pick = rng.choice(idxs, size=max(1, len(idxs) // 20), replace=False)
    corr = []
    for i in pick:
        row = hist.loc[i].to_dict()
        old = row.copy()
        # Close old version
        avail = str(row.get("available_at") or row.get("published_at"))
        old["valid_to"] = avail
        # New corrected version slightly shifts margin
        row["release_version"] = int(row.get("release_version") or 1) + 1
        row["corrected_at"] = avail
        row["valid_from"] = avail
        row["valid_to"] = None
        row["supersedes"] = row.get("poll_id")
        row["poll_id"] = f"{row['poll_id']}-r{row['release_version']}"
        margin = float(row.get("two_party_margin") or 0.0) + float(rng.normal(0, 0.4))
        row["two_party_margin"] = round(margin, 3)
        dem = 50 + margin / 2
        row["dem_share"] = round(dem, 2)
        row["rep_share"] = round(100 - dem, 2)
        corr.append(row)
    if not corr:
        return polls
    return pd.concat([polls, pd.DataFrame(corr)], ignore_index=True)
