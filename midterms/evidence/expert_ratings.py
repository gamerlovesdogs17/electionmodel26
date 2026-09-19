"""Timestamped expert Senate race ratings (ablatable overlay layer)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.model.overlays import RATING_MARGIN, rating_from_probability

PARSER_VERSION = "expert-ratings-v1"

# Research snapshot: public-facing consensus-style categories for Class II 2026.
# Not affiliated with Cook/IE/Sabato; maintained as an auditable, dated fixture
# that can be replaced by an external CSV via fetch/write.
DEFAULT_RATINGS_2026: list[dict[str, Any]] = [
    # Solid / Likely D
    {"state": "CA", "rating": "Solid D"},  # not Class II — ignored if absent
    {"state": "CO", "rating": "Likely D"},
    {"state": "DE", "rating": "Solid D"},
    {"state": "IL", "rating": "Solid D"},
    {"state": "MA", "rating": "Solid D"},
    {"state": "NJ", "rating": "Solid D"},
    {"state": "NM", "rating": "Likely D"},
    {"state": "OR", "rating": "Solid D"},
    {"state": "RI", "rating": "Solid D"},
    {"state": "VA", "rating": "Likely D"},
    {"state": "GA", "rating": "Lean D"},
    {"state": "MI", "rating": "Lean D"},
    {"state": "MN", "rating": "Lean D"},
    {"state": "NH", "rating": "Tossup"},
    {"state": "NC", "rating": "Lean D"},
    # Competitive / R-leaning Class II
    {"state": "ME", "rating": "Lean R"},
    {"state": "TX", "rating": "Tossup"},
    {"state": "AK", "rating": "Likely R"},
    {"state": "IA", "rating": "Likely R"},
    {"state": "OH", "rating": "Likely R"},
    {"state": "FL", "rating": "Likely R"},
    # Solid / Likely R
    {"state": "AL", "rating": "Solid R"},
    {"state": "AR", "rating": "Solid R"},
    {"state": "ID", "rating": "Solid R"},
    {"state": "KS", "rating": "Solid R"},
    {"state": "KY", "rating": "Solid R"},
    {"state": "LA", "rating": "Solid R"},
    {"state": "MS", "rating": "Solid R"},
    {"state": "MT", "rating": "Likely R"},
    {"state": "NE", "rating": "Solid R"},
    {"state": "OK", "rating": "Solid R"},
    {"state": "SC", "rating": "Solid R"},
    {"state": "SD", "rating": "Solid R"},
    {"state": "TN", "rating": "Solid R"},
    {"state": "WV", "rating": "Solid R"},
    {"state": "WY", "rating": "Solid R"},
]


def _stamp_rows(
    rows: list[dict[str, Any]],
    *,
    election_id: str,
    available_at: str,
    source: str,
) -> pd.DataFrame:
    out = []
    for r in rows:
        st = str(r["state"]).upper()
        rating = str(r["rating"])
        out.append(
            {
                "election_id": election_id,
                "state": st,
                "race_id": f"{election_id}-{st}",
                "rating": rating,
                "implied_margin": float(RATING_MARGIN.get(rating, 0.0)),
                "source": source,
                "available_at": available_at,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(out)


def write_expert_ratings_store(
    election_id: str = "senate-2026",
    *,
    available_at: str = "2026-09-01",
    csv_path: Path | None = None,
    rows: list[dict[str, Any]] | None = None,
    source_label: str | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Persist timestamped expert ratings (rows / CSV / curated defaults)."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    norm_path = NORMALIZED_DIR / "expert_ratings.parquet"
    if not overwrite and norm_path.exists():
        existing = pd.read_parquet(norm_path)
        return {
            "ok": True,
            "skipped": True,
            "n": int(len(existing)),
            "source": "existing",
            "path": str(norm_path),
            "available_at": available_at,
            "election_id": election_id,
            "parser_version": PARSER_VERSION,
        }

    source = source_label or "curated_research_snapshot"
    if csv_path and Path(csv_path).exists():
        df_in = pd.read_csv(csv_path)
        rows = df_in.to_dict(orient="records")
        source = source_label or f"csv:{Path(csv_path).name}"
        # Per-row source (e.g. licensed:cook) wins when present
        out_rows = []
        for r in rows:
            row_source = str(r.get("source") or source)
            st = str(r["state"]).upper()
            rating = str(r["rating"])
            avail = str(r.get("available_at") or available_at)[:10]
            eid = str(r.get("election_id") or election_id)
            out_rows.append(
                {
                    "election_id": eid,
                    "state": st,
                    "race_id": f"{eid}-{st}",
                    "rating": rating,
                    "implied_margin": float(RATING_MARGIN.get(rating, 0.0)),
                    "source": row_source,
                    "available_at": avail,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "parser_version": PARSER_VERSION,
                }
            )
        df = pd.DataFrame(out_rows)
        source = (
            "licensed"
            if any(str(s).startswith("licensed") for s in df["source"])
            else source
        )
    elif rows:
        source = source_label or str(rows[0].get("source") or "rows")
        df = _stamp_rows(
            rows,
            election_id=election_id,
            available_at=available_at,
            source=source,
        )
        # Preserve per-row source when provided
        if any("source" in r for r in rows):
            by_state = {str(r["state"]).upper(): str(r.get("source") or source) for r in rows}
            df["source"] = df["state"].map(lambda s: by_state.get(str(s), source))
    else:
        rows = DEFAULT_RATINGS_2026
        df = _stamp_rows(rows, election_id=election_id, available_at=available_at, source=source)

    raw_path = RAW_DIR / "external" / "expert_ratings_senate.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    # Public raw file — strip licensed:* rows from git-tracked JSON
    public_df = df[~df["source"].astype(str).str.startswith("licensed")]
    if public_df.empty:
        public_df = df.head(0)
    raw_path.write_text(public_df.to_json(orient="records", indent=2))
    df.to_parquet(norm_path, index=False)
    note = (
        "Wikipedia multi-rater Cook/IE/Sabato consensus (CC BY-SA page; extended WH/RCP/DDHQ stored)."
        if str(source).startswith("wikipedia")
        else (
            "Curated research ratings for ablation overlays — replace via Wikipedia / CSV / licensed. "
            "Not Cook/IE/Sabato redistribution."
        )
    )
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "available_at": available_at,
        "n": int(len(df)),
        "source": source,
        "parser_version": PARSER_VERSION,
        "tier": "aggregator" if str(source).startswith("licensed") else "curated",
        "note": note,
    }
    man_path = MANIFESTS_DIR / "expert_ratings.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {**man, "path": str(norm_path), "raw": str(raw_path)}


def ensure_expert_ratings_store(
    election_id: str = "senate-2026",
    *,
    available_at: str = "2026-09-01",
    prefer_wikipedia: bool = True,
    max_age_hours: float = 168.0,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Ensure an expert-ratings store exists and is reasonably fresh.

    Refreshes from Wikipedia when missing, forced, older than `max_age_hours`,
    still on the curated snapshot while Wikipedia is preferred, or when the
    forecast as-of is after the store's rating available_at.
    """
    path = NORMALIZED_DIR / "expert_ratings.parquet"
    if path.exists() and not force_refresh and _expert_store_is_fresh(
        path, available_at=available_at, prefer_wikipedia=prefer_wikipedia, max_age_hours=max_age_hours
    ):
        return write_expert_ratings_store(
            election_id=election_id,
            available_at=available_at,
            overwrite=False,
        )
    if prefer_wikipedia:
        try:
            from midterms.evidence.wiki_ratings import write_from_wikipedia

            return write_from_wikipedia(election_id=election_id, available_at=available_at)
        except Exception as exc:  # noqa: BLE001
            if path.exists():
                kept = write_expert_ratings_store(
                    election_id=election_id,
                    available_at=available_at,
                    overwrite=False,
                )
                return {**kept, "wiki_error": str(exc), "refreshed": False}
            curated = write_expert_ratings_store(
                election_id=election_id, available_at=available_at
            )
            return {**curated, "wiki_error": str(exc)}
    return write_expert_ratings_store(election_id=election_id, available_at=available_at)


def _expert_store_is_fresh(
    path: Path,
    *,
    available_at: str,
    prefer_wikipedia: bool,
    max_age_hours: float,
) -> bool:
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return False
    if df.empty:
        return False
    sources = df["source"].astype(str) if "source" in df.columns else pd.Series(dtype=str)
    if prefer_wikipedia and len(sources) and sources.str.startswith("curated").all():
        return False
    if "retrieved_at" in df.columns:
        latest = pd.to_datetime(df["retrieved_at"], utc=True, errors="coerce").max()
        if pd.isna(latest):
            return False
        age_h = (pd.Timestamp.now(tz="UTC") - latest).total_seconds() / 3600.0
        if age_h > float(max_age_hours):
            return False
    if "available_at" in df.columns:
        store_asof = pd.to_datetime(df["available_at"], errors="coerce").max()
        want = pd.Timestamp(str(available_at)[:10])
        if pd.notna(store_asof) and want.date() > store_asof.date():
            return False
    return True


def load_expert_ratings(as_of: str | None = None, election_id: str | None = None) -> pd.DataFrame:
    path = NORMALIZED_DIR / "expert_ratings.parquet"
    if not path.exists():
        write_expert_ratings_store()
    df = pd.read_parquet(path)
    if election_id:
        df = df[df["election_id"] == election_id]
    if as_of and "available_at" in df.columns and len(df):
        df = df[pd.to_datetime(df["available_at"]).dt.date <= pd.Timestamp(as_of).date()]
    return df.reset_index(drop=True)


def ratings_for_races(
    race_ids: list[str],
    states: list[str],
    *,
    as_of: str | None = None,
    election_id: str = "senate-2026",
    fallback_probs: list[float] | None = None,
) -> pd.DataFrame:
    """Join expert ratings onto races; fall back to probability-derived labels."""
    expert = load_expert_ratings(as_of=as_of, election_id=election_id)
    by_id = {}
    by_state = {}
    if len(expert):
        if "race_id" in expert.columns:
            by_id = expert.set_index("race_id")["rating"].to_dict()
        by_state = expert.set_index("state")["rating"].to_dict()
    rows = []
    for i, (rid, st) in enumerate(zip(race_ids, states)):
        if rid in by_id:
            rating = by_id[rid]
            source = "expert"
        elif st in by_state:
            rating = by_state[st]
            source = "expert"
        elif fallback_probs is not None:
            rating = rating_from_probability(float(fallback_probs[i]))
            source = "model_derived"
        else:
            rating = "Tossup"
            source = "default"
        rows.append(
            {
                "race_id": rid,
                "state": st,
                "rating": rating,
                "source": source,
            }
        )
    return pd.DataFrame(rows)
