"""Immutable evidence warehouse + as-of snapshot builder."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.fixtures import build_fixtures
from midterms.evidence.ratings import build_rating_lookup, rating_for
from midterms.evidence.results_archive import merge_certified_into_results
from midterms.evidence.schema import is_active_ballot_row


def _parse_day(value: str | date | datetime | pd.Timestamp | None) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.Timestamp(value).date()


@dataclass
class EvidenceSnapshot:
    as_of: date
    election_id: str
    polls: pd.DataFrame
    races: pd.DataFrame
    results_known: pd.DataFrame
    snapshot_id: str
    pollster_ratings: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        n_contested = (
            int(self.races.apply(is_active_ballot_row, axis=1).sum()) if len(self.races) else 0
        )
        return {
            "as_of": self.as_of.isoformat(),
            "election_id": self.election_id,
            "snapshot_id": self.snapshot_id,
            "n_polls": int(len(self.polls)),
            "n_races_contested": n_contested,
            "n_results_known": int(len(self.results_known)),
            "n_rated_pollsters": int(len(self.pollster_ratings or {})),
        }


class Warehouse:
    def __init__(self, normalized_dir: Path | None = None, ensure_fixtures: bool = True):
        self.normalized_dir = normalized_dir or NORMALIZED_DIR
        self.raw_dir = RAW_DIR
        if ensure_fixtures and not (self.normalized_dir / "polls.parquet").exists():
            build_fixtures()
        self.polls = pd.read_parquet(self.normalized_dir / "polls.parquet")
        self.races = pd.read_parquet(self.normalized_dir / "races.parquet")
        results_path = self.normalized_dir / "results.parquet"
        self.results = (
            pd.read_parquet(results_path) if results_path.exists() else pd.DataFrame()
        )
        # Prefer redistributable certified archive when present
        try:
            self.results = merge_certified_into_results(self.results)
        except Exception:  # noqa: BLE001
            pass
        ratings_path = self.normalized_dir / "pollster_ratings.parquet"
        self.ratings_df = (
            pd.read_parquet(ratings_path) if ratings_path.exists() else pd.DataFrame()
        )

    def _attach_poll_priors(self, polls: pd.DataFrame, as_of_d: date) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Join VoteHub / FTE pollster ratings onto poll rows for the as-of date."""
        if polls.empty:
            return polls, {}
        lookup = build_rating_lookup(as_of=as_of_d)
        quality, house, extra, grade, source = [], [], [], [], []
        used: dict[str, Any] = {}
        for _, row in polls.iterrows():
            r = rating_for(str(row["pollster_id"]), lookup)
            quality.append(r.quality_weight)
            house.append(r.house_effect_dem_pp)
            extra.append(r.extra_sd_prior)
            grade.append(r.grade)
            source.append(r.source)
            used[r.pollster] = {
                "grade": r.grade,
                "quality_weight": r.quality_weight,
                "house_effect_dem_pp": r.house_effect_dem_pp,
                "extra_sd_prior": r.extra_sd_prior,
                "source": r.source,
            }
        out = polls.copy()
        out["quality_weight"] = quality
        out["house_effect_prior"] = house
        out["extra_sd_prior"] = extra
        out["pollster_grade"] = grade
        out["rating_source"] = source
        return out, used

    def build_as_of(self, as_of: str | date, election_id: str) -> EvidenceSnapshot:
        """Return polls/races available at `as_of` for `election_id`. Rejects future rows."""
        as_of_d = _parse_day(as_of)
        assert as_of_d is not None

        polls = self.polls[self.polls["election_id"] == election_id].copy()
        races = self.races[self.races["election_id"] == election_id].copy()
        results = (
            self.results[self.results["election_id"] == election_id].copy()
            if len(self.results)
            else self.results
        )

        # Availability filter — never use event_time alone
        avail = polls["available_at"].map(_parse_day)
        polls = polls[avail.notna() & (avail <= as_of_d)]
        polls = polls[polls["exclusion_status"].fillna("include") == "include"]

        # Bitemporal validity window (corrections): valid_from ≤ as_of < valid_to (or open)
        if len(polls) and "valid_from" in polls.columns:
            vf = polls["valid_from"].map(_parse_day)
            vt = polls["valid_to"].map(_parse_day) if "valid_to" in polls.columns else None
            in_window = vf.isna() | (vf <= as_of_d)
            if vt is not None:
                open_or_future = vt.isna() | (vt > as_of_d)
                in_window = in_window & open_or_future
            polls = polls[in_window]
            if "release_version" in polls.columns and "poll_id" in polls.columns and len(polls):
                polls = (
                    polls.sort_values("release_version")
                    .groupby("poll_id", as_index=False)
                    .tail(1)
                )

        if len(results):
            r_avail = results["available_at"].map(_parse_day)
            results_known = results[r_avail.notna() & (r_avail <= as_of_d)]
        else:
            results_known = results

        # Drop inactive ballot rows from contested forecast universe (keep held)
        if len(races):
            mask = races.apply(
                lambda r: bool(r.get("not_up")) or is_active_ballot_row(r),
                axis=1,
            )
            races = races[mask]

        # Leakage canary: if any remaining poll has available_at > as_of, fail closed
        if len(polls):
            bad = polls["available_at"].map(_parse_day) > as_of_d
            if bad.any():
                raise RuntimeError(f"Leakage canary failed: {int(bad.sum())} future polls")

        polls, rating_meta = self._attach_poll_priors(polls.reset_index(drop=True), as_of_d)

        blob = (
            f"{election_id}|{as_of_d.isoformat()}|{len(polls)}|"
            f"{polls['poll_id'].astype(str).sum() if len(polls) else ''}"
        )
        snapshot_id = hashlib.sha256(blob.encode()).hexdigest()[:16]
        return EvidenceSnapshot(
            as_of=as_of_d,
            election_id=election_id,
            polls=polls,
            races=races.reset_index(drop=True),
            results_known=results_known.reset_index(drop=True)
            if len(results_known)
            else results_known,
            snapshot_id=snapshot_id,
            pollster_ratings=rating_meta,
        )

    def inject_future_poll_for_canary(self, election_id: str, as_of: str | date) -> pd.DataFrame:
        """Return a copy of polls with an injected future-dated row (test helper)."""
        as_of_d = _parse_day(as_of)
        assert as_of_d is not None
        polls = self.polls[self.polls["election_id"] == election_id].copy()
        if polls.empty:
            raise ValueError("no polls to mutate")
        row = polls.iloc[0].copy()
        row["poll_id"] = "CANARY-FUTURE-POLL"
        row["available_at"] = (pd.Timestamp(as_of_d) + pd.Timedelta(days=30)).date().isoformat()
        row["published_at"] = row["available_at"]
        return pd.concat([polls, pd.DataFrame([row])], ignore_index=True)


def write_run_manifest(payload: dict[str, Any], path: Path | None = None) -> Path:
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = path or MANIFESTS_DIR / f"run_{payload.get('run_id', 'unknown')}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
