"""Pollster quality / house-effect ratings (VoteHub Scorecards + FTE backup).

Point-in-time contract (audit):
- ``build_rating_lookup(as_of=…)`` may only include rows with ``available_at``
  on or before that date.
- Living artifacts stamped only with file mtime are **excluded** from historical
  as-of lookups when that mtime is after the cutoff (no silent use of 2026 grades
  in a 2018 backtest).
- ``rating_for(…, lookup=)`` must never rebuild an unfiltered lookup when the
  caller passed an empty filtered dict (``{}`` is falsy — use ``is None``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, RAW_DIR
from midterms.evidence.candidates import canonicalize_pollster, normalize_candidate_key

GRADE_QUALITY = {
    "A+": 1.00,
    "A": 0.88,
    "B": 0.72,
    "C": 0.52,
    "D": 0.32,
    "F": 0.18,
}

DEFAULT_QUALITY = 0.55
DEFAULT_HOUSE = 0.0
DEFAULT_EXTRA_SD = 2.2

# Living FTE/VoteHub dumps without per-row vintage dates are only safe for
# as-of runs on/after this documented retrieval stamp (file mtime is not a
# historical publication date). Historical backtests before this date use
# prior_default unless a vintaged ratings snapshot is present.
RATINGS_SNAPSHOT_FLOOR = date(2024, 1, 1)


@dataclass
class PollsterRating:
    pollster: str
    grade: str | None
    quality_weight: float
    house_effect_dem_pp: float
    percent_error: float | None
    relative_error: float | None
    herding_error_pct: float | None
    within_moe_pct: float | None
    source: str
    available_at: str

    @property
    def extra_sd_prior(self) -> float:
        """Map relative/percent error into an extra-SD prior (pp)."""
        if self.relative_error is not None and self.relative_error > 0:
            return float(max(1.0, min(4.5, 1.1 * self.relative_error + 0.6)))
        if self.percent_error is not None and self.percent_error > 0:
            return float(max(1.0, min(4.5, 0.55 * self.percent_error)))
        return DEFAULT_EXTRA_SD


def _votehub_scorecards_path() -> Path:
    return RAW_DIR / "external" / "votehub_pollster_scorecards.json"


def _fte_ratings_path() -> Path:
    return RAW_DIR / "external" / "fte_pollster_ratings_combined.csv"


def _vintaged_ratings_path() -> Path:
    """Optional historical snapshot: list of {pollster, …, available_at} rows."""
    return RAW_DIR / "external" / "pollster_ratings_vintages.json"


def _neutral_rating(pollster: str) -> PollsterRating:
    canon = canonicalize_pollster(pollster)
    return PollsterRating(
        pollster=canon,
        grade=None,
        quality_weight=DEFAULT_QUALITY,
        house_effect_dem_pp=DEFAULT_HOUSE,
        percent_error=None,
        relative_error=None,
        herding_error_pct=None,
        within_moe_pct=None,
        source="prior_default",
        available_at="",
    )


def load_votehub_scorecards(path: Path | None = None) -> pd.DataFrame:
    path = path or _votehub_scorecards_path()
    if not path.exists():
        return pd.DataFrame()
    rows = json.loads(path.read_text(encoding="utf-8"))
    # allow wrapper object
    if isinstance(rows, dict):
        rows = rows.get("pollsters") or rows.get("scorecards") or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["pollster_key"] = df["pollster"].map(lambda x: normalize_candidate_key(canonicalize_pollster(x)))
    df["quality_weight"] = df["grade"].map(lambda g: GRADE_QUALITY.get(str(g), DEFAULT_QUALITY))
    df["source"] = "votehub_pollster_scorecards"
    # Living page: retrieval mtime is NOT a historical publication date.
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    if "available_at" not in df.columns or df["available_at"].isna().all():
        df["available_at"] = mtime
    df["provenance"] = "living_scorecard_mtime"
    df["retrieval_mtime"] = mtime
    return df


def load_fte_ratings(path: Path | None = None) -> pd.DataFrame:
    path = path or _fte_ratings_path()
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    out = pd.DataFrame(
        {
            "pollster": df["pollster"],
            "pollster_key": df["pollster"].map(lambda x: normalize_candidate_key(canonicalize_pollster(str(x)))),
            "grade": df.get("numeric_grade"),
            "quality_weight": df.get("numeric_grade", pd.Series([DEFAULT_QUALITY] * len(df))).map(
                lambda g: float(g) / 3.0 if pd.notna(g) else DEFAULT_QUALITY
            ).clip(0.15, 1.0),
            "house_effect_dem_pp": df.get("bias_ppm", pd.Series([0.0] * len(df))).fillna(0.0).astype(float)
            * -1.0,  # FTE bias_ppm is signed R-positive historically in ppm units — treat as soft prior only
            "percent_error": df.get("error_ppm"),
            "relative_error": None,
            "herding_error_pct": None,
            "within_moe_pct": None,
            "source": "fivethirtyeight_pollster_ratings",
            # Living dump without row vintages: stamp retrieval mtime + provenance flag.
            "available_at": mtime,
            "provenance": "living_csv_mtime",
            "retrieval_mtime": mtime,
        }
    )
    # Prefer VoteHub for house effects; FTE bias_ppm scale differs — zero house prior from FTE
    out["house_effect_dem_pp"] = 0.0
    return out


def load_vintaged_ratings(path: Path | None = None) -> pd.DataFrame:
    """Load optional curated historical pollster-rating vintages (explicit available_at)."""
    path = path or _vintaged_ratings_path()
    if not path.exists():
        return pd.DataFrame()
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    df = pd.DataFrame(rows or [])
    if df.empty:
        return df
    if "pollster_key" not in df.columns:
        df["pollster_key"] = df["pollster"].map(
            lambda x: normalize_candidate_key(canonicalize_pollster(str(x)))
        )
    df["source"] = df.get("source", pd.Series(["vintaged_pollster_ratings"] * len(df)))
    df["provenance"] = "vintaged_snapshot"
    return df


def build_rating_lookup(
    as_of: str | date | None = None,
) -> dict[str, PollsterRating]:
    """Merge VoteHub scorecards (primary) with FTE ratings (fill-in).

    When ``as_of`` is set, only rows with ``available_at <= as_of`` are kept.
    Living dumps whose only stamp is a post-cutoff file mtime are excluded
    (no silent future leak). Optional vintaged snapshots fill historical gaps.
    """
    vh = load_votehub_scorecards()
    fte = load_fte_ratings()
    vintaged = load_vintaged_ratings()
    as_of_d = None
    if as_of is not None:
        as_of_d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])

    lookup: dict[str, PollsterRating] = {}
    skipped_future = 0
    skipped_living = 0
    ingested = 0

    def _ingest(df: pd.DataFrame, *, overwrite: bool) -> None:
        nonlocal skipped_future, skipped_living, ingested
        if df.empty:
            return
        for _, row in df.iterrows():
            avail = str(row.get("available_at") or "")
            provenance = str(row.get("provenance") or "")
            if as_of_d is not None:
                if not avail:
                    # Unknown availability → exclude from historical lookups.
                    skipped_living += 1
                    continue
                try:
                    avail_d = date.fromisoformat(avail[:10])
                except ValueError:
                    skipped_living += 1
                    continue
                if avail_d > as_of_d:
                    skipped_future += 1
                    continue
                # Living mtime stamps after the historical cutoff floor are not
                # credible publication dates for pre-floor backtests.
                if (
                    provenance.startswith("living_")
                    and as_of_d < RATINGS_SNAPSHOT_FLOOR
                    and avail_d >= RATINGS_SNAPSHOT_FLOOR
                ):
                    skipped_living += 1
                    continue
            key = str(row["pollster_key"])
            if key in lookup and not overwrite:
                continue
            grade = row.get("grade")
            grade_s = None if pd.isna(grade) else str(grade)
            qw = float(row.get("quality_weight") or DEFAULT_QUALITY)
            if grade_s in GRADE_QUALITY:
                qw = GRADE_QUALITY[grade_s]
            he = row.get("house_effect_dem_pp")
            lookup[key] = PollsterRating(
                pollster=str(row["pollster"]),
                grade=grade_s,
                quality_weight=qw,
                house_effect_dem_pp=float(he) if pd.notna(he) else DEFAULT_HOUSE,
                percent_error=float(row["percent_error"]) if pd.notna(row.get("percent_error")) else None,
                relative_error=float(row["relative_error"]) if pd.notna(row.get("relative_error")) else None,
                herding_error_pct=float(row["herding_error_pct"]) if pd.notna(row.get("herding_error_pct")) else None,
                within_moe_pct=float(row["within_moe_pct"]) if pd.notna(row.get("within_moe_pct")) else None,
                source=str(row.get("source") or "unknown"),
                available_at=avail,
            )
            ingested += 1

    # Vintaged historical first, then FTE fill-in, VoteHub overwrites for live windows.
    _ingest(vintaged, overwrite=True)
    _ingest(fte, overwrite=False)
    _ingest(vh, overwrite=True)
    # Attach debug counters for callers (warehouse) without breaking dict[str, PollsterRating]
    lookup_meta: dict[str, Any] = {
        "as_of": as_of_d.isoformat() if as_of_d else None,
        "n_ratings": len(lookup),
        "n_ingested_rows": ingested,
        "n_skipped_future": skipped_future,
        "n_skipped_living_unvintaged": skipped_living,
    }
    # Store meta on a private attribute via wrapper — callers that need it use
    # build_rating_lookup_with_meta.
    build_rating_lookup._last_meta = lookup_meta  # type: ignore[attr-defined]
    return lookup


def build_rating_lookup_with_meta(
    as_of: str | date | None = None,
) -> tuple[dict[str, PollsterRating], dict[str, Any]]:
    lookup = build_rating_lookup(as_of=as_of)
    meta = getattr(build_rating_lookup, "_last_meta", {}) or {}
    return lookup, dict(meta)


def rating_for(pollster: str, lookup: dict[str, PollsterRating] | None = None) -> PollsterRating:
    """Resolve a pollster rating.

    Critical: an *empty* filtered lookup must yield ``prior_default``, never a
    silent rebuild of current (unfiltered) ratings.
    """
    if lookup is None:
        lookup = build_rating_lookup()
    canon = canonicalize_pollster(pollster)
    key = normalize_candidate_key(canon)
    if key in lookup:
        return lookup[key]
    # try raw
    raw_key = normalize_candidate_key(pollster)
    if raw_key in lookup:
        return lookup[raw_key]
    return _neutral_rating(pollster)


def write_normalized_ratings() -> Path:
    """Materialize a tidy ratings parquet for the warehouse."""
    from midterms.config import NORMALIZED_DIR

    lookup = build_rating_lookup()
    rows = [
        {
            "pollster_id": r.pollster,
            "pollster_key": k,
            "grade": r.grade,
            "quality_weight": r.quality_weight,
            "house_effect_dem_pp": r.house_effect_dem_pp,
            "percent_error": r.percent_error,
            "relative_error": r.relative_error,
            "herding_error_pct": r.herding_error_pct,
            "within_moe_pct": r.within_moe_pct,
            "extra_sd_prior": r.extra_sd_prior,
            "source": r.source,
            "available_at": r.available_at,
        }
        for k, r in sorted(lookup.items())
    ]
    df = pd.DataFrame(rows)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    out = NORMALIZED_DIR / "pollster_ratings.parquet"
    df.to_parquet(out, index=False)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (MANIFESTS_DIR / "pollster_ratings.json").write_text(
        json.dumps(
            {
                "n": len(df),
                "votehub_n": int((df["source"] == "votehub_pollster_scorecards").sum()),
                "fte_n": int((df["source"] == "fivethirtyeight_pollster_ratings").sum()),
                "path": str(out),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "note": (
                    "Living VoteHub/FTE dumps stamped with retrieval mtime; "
                    "historical as-of lookups exclude post-cutoff living stamps."
                ),
            },
            indent=2,
        )
    )
    return out
