"""Pollster quality / house-effect ratings (VoteHub Scorecards + FTE backup)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

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
    # Scorecards page is a living artifact — stamp retrieval day as available_at
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    df["available_at"] = mtime
    return df


def load_fte_ratings(path: Path | None = None) -> pd.DataFrame:
    path = path or _fte_ratings_path()
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
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
            "available_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat(),
        }
    )
    # Prefer VoteHub for house effects; FTE bias_ppm scale differs — zero house prior from FTE
    out["house_effect_dem_pp"] = 0.0
    return out


def build_rating_lookup(
    as_of: str | date | None = None,
) -> dict[str, PollsterRating]:
    """Merge VoteHub scorecards (primary) with FTE ratings (fill-in)."""
    vh = load_votehub_scorecards()
    fte = load_fte_ratings()
    as_of_d = None
    if as_of is not None:
        as_of_d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))

    lookup: dict[str, PollsterRating] = {}

    def _ingest(df: pd.DataFrame, *, overwrite: bool) -> None:
        if df.empty:
            return
        for _, row in df.iterrows():
            avail = str(row.get("available_at") or "")
            if as_of_d and avail:
                try:
                    if date.fromisoformat(avail[:10]) > as_of_d:
                        continue
                except ValueError:
                    pass
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

    # FTE first as fill-in, VoteHub overwrites
    _ingest(fte, overwrite=False)
    _ingest(vh, overwrite=True)
    return lookup


def rating_for(pollster: str, lookup: dict[str, PollsterRating] | None = None) -> PollsterRating:
    lookup = lookup or build_rating_lookup()
    canon = canonicalize_pollster(pollster)
    key = normalize_candidate_key(canon)
    if key in lookup:
        return lookup[key]
    # try raw
    raw_key = normalize_candidate_key(pollster)
    if raw_key in lookup:
        return lookup[raw_key]
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
            },
            indent=2,
        )
    )
    return out
