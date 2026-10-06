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

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR, ROOT
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

# Git checkout rewrites filesystem mtimes, so living dumps must pin retrieval
# time in a tracked manifest. Using raw mtime after clone falsely marks the
# dump as "future" relative to a sealed as_of and breaks snapshot identity.
LIVING_RATINGS_RETRIEVAL_MANIFEST = MANIFESTS_DIR / "living_pollster_ratings_retrieval.json"
LIVING_RATINGS_RETRIEVAL_SCHEMA = "living-pollster-ratings-retrieval-v1"

# Availability comes from the public source repository commit that introduced
# the exact checked-in bytes, never from the vintage-looking filename.
VERIFIED_VENDORED_RATING_SNAPSHOTS: dict[str, dict[str, str]] = {
    "2018": {
        "available_at": "2018-05-31",
        "sha256": "710139e06c88649a6f7ce30b854c74219c9495f0e80d5e6c0848d98364d1e5a3",
        "source_commit": "de2dfac210b1d63c8a1c160a1e6acbf8dc0b7e6f",
        "source_url": "https://github.com/fivethirtyeight/data/commit/de2dfac210b1d63c8a1c160a1e6acbf8dc0b7e6f",
    },
    "2020": {
        "available_at": "2021-03-19",
        "sha256": "b26bd623e798e3fdfa6ef4ef22ff9489478269aef9b159e33592773000053b91",
        "source_commit": "c1b4d2",
        "source_url": "https://github.com/fivethirtyeight/data/commit/c1b4d2",
    },
    "2023": {
        "available_at": "2024-01-25",
        "sha256": "8501615e7bff043dd3ed0452b875a93f519cec546c190a5d0a462272c387d923",
        "source_commit": "fea5d9",
        "source_url": "https://github.com/fivethirtyeight/data/commit/fea5d9",
    },
}
UNVERIFIED_VENDORED_RATING_SNAPSHOTS = {"2021": "checked-in bytes do not match a verified source commit"}


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


def _signed_party_value(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    import re

    match = re.search(r"([DR])\s*\+?\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        return 0.0
    magnitude = float(match.group(2))
    return magnitude if match.group(1).upper() == "D" else -magnitude


def prepare_vendored_pollster_rating_vintages(
    *,
    raw_dir: Path = RAW_DIR,
    normalized_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Seal exact-source historical rating snapshots with defensible availability."""
    rows: list[dict[str, Any]] = []
    source_blocks: list[dict[str, Any]] = []
    for vintage, metadata in sorted(VERIFIED_VENDORED_RATING_SNAPSHOTS.items()):
        path = raw_dir / "external" / f"pollster_ratings_{vintage}.csv"
        if not path.is_file():
            source_blocks.append({"content_vintage": vintage, "status": "missing"})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != metadata["sha256"]:
            source_blocks.append({
                "content_vintage": vintage, "status": "hash_mismatch",
                "expected_sha256": metadata["sha256"], "actual_sha256": actual,
            })
            continue
        frame = pd.read_csv(path)
        pollster_column = "Pollster"
        grade_column = "538 Grade"
        if pollster_column not in frame.columns or grade_column not in frame.columns:
            raise ValueError(f"rating snapshot {vintage} lacks Pollster/538 Grade")
        for _, record in frame.iterrows():
            name = str(record[pollster_column]).strip()
            if not name:
                continue
            grade = None if pd.isna(record.get(grade_column)) else str(record.get(grade_column))
            quality = GRADE_QUALITY.get(grade or "", DEFAULT_QUALITY)
            bias_raw = record.get("House Effect", record.get("Mean-Reverted Bias"))
            rows.append({
                "pollster": name,
                "pollster_key": normalize_candidate_key(canonicalize_pollster(name)),
                "grade": grade,
                "quality_weight": quality,
                "house_effect_dem_pp": _signed_party_value(bias_raw),
                "percent_error": pd.to_numeric(record.get("Simple Average Error"), errors="coerce"),
                "relative_error": None,
                "herding_error_pct": None,
                "within_moe_pct": None,
                "source": "fivethirtyeight_historical_rating_snapshot",
                "content_vintage": vintage,
                "available_at": metadata["available_at"],
                "source_commit": metadata["source_commit"],
                "source_url": metadata["source_url"],
                "source_sha256": actual,
                "parser_version": "pollster-rating-vintage-v1",
                "provenance": "verified_source_commit",
            })
        source_blocks.append({
            "content_vintage": vintage, "status": "verified",
            "available_at": metadata["available_at"], "sha256": actual,
            "source_commit": metadata["source_commit"], "source_url": metadata["source_url"],
        })
    for vintage, reason in sorted(UNVERIFIED_VENDORED_RATING_SNAPSHOTS.items()):
        path = raw_dir / "external" / f"pollster_ratings_{vintage}.csv"
        source_blocks.append({
            "content_vintage": vintage,
            "status": "unverified_excluded",
            "reason": reason,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        })
    output = pd.DataFrame(rows)
    if output.empty:
        raise ValueError("no verified pollster rating snapshots are available")
    output = output.sort_values(
        ["available_at", "content_vintage", "pollster_key"], kind="stable"
    ).reset_index(drop=True)
    normalized_path = normalized_path or (NORMALIZED_DIR / "pollster_ratings.parquet")
    manifest_path = manifest_path or (MANIFESTS_DIR / "pollster_ratings.json")
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(normalized_path, index=False)
    semantic = hashlib.sha256(json.dumps(
        output.fillna("").astype(str).to_dict(orient="records"),
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": "pollster-rating-vintages-v1",
        "parser_version": "pollster-rating-vintage-v1",
        "normalized_semantic_sha256": semantic,
        "sources": source_blocks,
        "n_rows": len(output),
        "content_vintages": sorted(output["content_vintage"].unique()),
        "available_at_values": sorted(output["available_at"].unique()),
        "source_integrity_verified": True,
        "production_eligible": True,
        "production_ineligible_reason": None,
        "known_historical_gap": None,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _votehub_scorecards_path() -> Path:
    return RAW_DIR / "external" / "votehub_pollster_scorecards.json"


def _fte_ratings_path() -> Path:
    return RAW_DIR / "external" / "fte_pollster_ratings_combined.csv"


def _vintaged_ratings_path() -> Path:
    """Optional historical snapshot: list of {pollster, …, available_at} rows."""
    return RAW_DIR / "external" / "pollster_ratings_vintages.json"


def _file_sha256(path: Path) -> str:
    # Normalize newlines so Windows checkout CRLF matches the git blob / Linux CI.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _mtime_available_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()


def living_ratings_retrieval_available_at(path: Path) -> str:
    """Return sealed retrieval day for a living ratings dump, else file mtime day.

    Prefer the tracked retrieval manifest when the on-disk bytes still match the
    sealed content hash. Fall back to mtime only for local/dev trees that have
    not sealed living ratings yet.
    """
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    content_sha = _file_sha256(path)
    if LIVING_RATINGS_RETRIEVAL_MANIFEST.is_file():
        try:
            payload = json.loads(
                LIVING_RATINGS_RETRIEVAL_MANIFEST.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            payload = {}
        for entry in (payload.get("sources") or {}).values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("content_sha256") or "") != content_sha:
                continue
            retrieved = str(entry.get("retrieved_at") or "")[:10]
            if retrieved:
                return retrieved
    return _mtime_available_at(path)


def seal_living_pollster_ratings_retrieval(
    *,
    retrieved_at: str | date,
    fte_path: Path | None = None,
    votehub_path: Path | None = None,
) -> dict[str, Any]:
    """Pin living FTE/VoteHub retrieval time so checkout mtimes cannot drift identity."""
    retrieved = (
        retrieved_at.isoformat()
        if isinstance(retrieved_at, date)
        else str(retrieved_at)[:10]
    )
    date.fromisoformat(retrieved)  # validate
    fte_path = (fte_path or _fte_ratings_path()).resolve()
    votehub_path = (votehub_path or _votehub_scorecards_path()).resolve()
    sources: dict[str, Any] = {}
    for key, path in (
        ("fte_pollster_ratings_combined", fte_path),
        ("votehub_pollster_scorecards", votehub_path),
    ):
        if not path.is_file():
            sources[key] = {"status": "missing", "path": path.as_posix()}
            continue
        try:
            rel = path.relative_to(ROOT.resolve())
        except ValueError:
            rel = path
        sources[key] = {
            "status": "sealed",
            "path": rel.as_posix().replace("\\", "/"),
            "content_sha256": _file_sha256(path),
            "retrieved_at": retrieved,
        }
    payload = {
        "schema_version": LIVING_RATINGS_RETRIEVAL_SCHEMA,
        "retrieved_at": retrieved,
        "sources": sources,
    }
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    LIVING_RATINGS_RETRIEVAL_MANIFEST.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8",
    )
    return payload


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
    # Living page: prefer sealed retrieval day over checkout-volatile mtime.
    retrieved = living_ratings_retrieval_available_at(path)
    if "available_at" not in df.columns or df["available_at"].isna().all():
        df["available_at"] = retrieved
    df["provenance"] = "living_scorecard_retrieval"
    df["retrieval_mtime"] = retrieved
    return df


def load_fte_ratings(path: Path | None = None) -> pd.DataFrame:
    path = path or _fte_ratings_path()
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    retrieved = living_ratings_retrieval_available_at(path)
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
            # Living dump without row vintages: sealed retrieval day (not checkout mtime).
            "available_at": retrieved,
            "provenance": "living_csv_retrieval",
            "retrieval_mtime": retrieved,
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
                "generated_at": datetime.now(UTC).isoformat(),
                "note": (
                    "Living VoteHub/FTE dumps stamped with retrieval mtime; "
                    "historical as-of lookups exclude post-cutoff living stamps."
                ),
            },
            indent=2,
        )
    )
    return out
