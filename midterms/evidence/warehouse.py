"""Immutable evidence warehouse + as-of snapshot builder."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR, ROOT
from midterms.evidence.fixtures import build_fixtures
from midterms.evidence.historical_polls import merge_historical_polls
from midterms.evidence.ratings import rating_for
from midterms.evidence.results_archive import merge_certified_into_results
from midterms.evidence.schema import align_poll_frame, is_active_ballot_row

SNAPSHOT_FINGERPRINT_VERSION = "evidence-snapshot-fingerprint-v2"


def _canonical_value(value: Any) -> Any:
    """Return a platform-independent strict-JSON representation."""
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes, dict, list, tuple)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, set):
        normalized = [_canonical_value(v) for v in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ),
        )
    if isinstance(value, (list, tuple)):
        return [_canonical_value(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _semantic_sha256(payload: Any) -> str:
    canonical = json.dumps(
        _canonical_value(payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dataframe_semantic_sha256(frame: pd.DataFrame) -> str:
    """Hash dataframe meaning independently of row/column order and newlines."""
    columns = sorted(str(column) for column in frame.columns)
    records = [
        {column: _canonical_value(row.get(column)) for column in columns}
        for row in frame.to_dict(orient="records")
    ]
    records.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return _semantic_sha256({"columns": columns, "records": records})


def evidence_snapshot_fingerprint(
    *,
    election_id: str,
    as_of: str | date,
    polls: pd.DataFrame,
    races: pd.DataFrame,
    candidate_timeline: dict[str, Any] | None,
    pollster_ratings: dict[str, Any] | None,
    prior_snapshot_sha256: str | None,
    presidential_source_sha256: str | None,
    presidential_source_years: tuple[int, ...] | list[int] = (),
    material_source_hashes: dict[str, str | None] | None = None,
) -> tuple[str, dict[str, str | None]]:
    """Create a content-addressed identity from selected, knowable evidence."""
    components: dict[str, str | None] = {
        "polls_semantic_sha256": dataframe_semantic_sha256(polls),
        "races_semantic_sha256": dataframe_semantic_sha256(races),
        "candidate_timeline_sha256": _semantic_sha256(candidate_timeline or {}),
        "pollster_ratings_sha256": _semantic_sha256(pollster_ratings or {}),
        "structural_prior_snapshot_sha256": prior_snapshot_sha256,
        "presidential_source_set_sha256": presidential_source_sha256,
        **{
            f"material_source_{name}_sha256": value
            for name, value in sorted((material_source_hashes or {}).items())
        },
    }
    payload = {
        "schema_version": SNAPSHOT_FINGERPRINT_VERSION,
        "election_id": str(election_id),
        "as_of": _parse_day(as_of).isoformat(),
        "presidential_source_years": [int(year) for year in presidential_source_years],
        "components": components,
    }
    return _semantic_sha256(payload), components


def _selected_material_source_hashes(
    *, normalized_dir: Path, as_of: date, election_id: str,
) -> dict[str, str | None]:
    """Hash only knowable rows from secondary stores used by the fit."""
    stores = {
        "finance": "fundraising_shares.parquet",
        "economics": "economics_vintages.parquet",
        "approval": "pres_approval.parquet",
        "demographics": "demography.parquet",
    }
    manifest_names = {
        "finance": "fundraising_shares.json",
        "economics": "economics_vintages.json",
        "approval": "pres_approval.json",
        "demographics": "demography.json",
    }
    identity_keys = (
        "source_url", "source", "tier", "license", "parser_version",
        "source_sha256", "normalized_sha256", "vintage_class",
    )
    hashes: dict[str, str | None] = {}
    for domain, filename in stores.items():
        path = normalized_dir / filename
        if not path.exists():
            hashes[domain] = None
            continue
        frame = pd.read_parquet(path)
        if "election_id" in frame.columns:
            frame = frame[frame["election_id"].astype(str) == election_id]
        if "available_at" in frame.columns:
            available = frame["available_at"].map(_parse_day)
            frame = frame[available.notna() & (available <= as_of)]
        manifest_path = MANIFESTS_DIR / manifest_names[domain]
        manifest_identity: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_identity = {
                    key: manifest.get(key) for key in identity_keys if manifest.get(key) is not None
                }
            except (OSError, json.JSONDecodeError):
                manifest_identity = {"manifest_status": "invalid_json"}
        hashes[domain] = _semantic_sha256({
            "selected_rows_sha256": dataframe_semantic_sha256(frame),
            "source_identity": manifest_identity,
        })
    return hashes


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
    presidential_source_sha256: str | None = None
    presidential_source_years: tuple[int, ...] = ()
    prior_snapshot_sha256: str | None = None
    prior_snapshot_path: str | None = None
    candidate_timeline: dict[str, Any] | None = None
    fingerprint_schema: str = SNAPSHOT_FINGERPRINT_VERSION
    component_hashes: dict[str, str | None] | None = None

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
            "presidential_source_sha256": self.presidential_source_sha256,
            "presidential_source_years": list(self.presidential_source_years),
            "prior_snapshot_sha256": self.prior_snapshot_sha256,
            "prior_snapshot_path": self.prior_snapshot_path,
            "candidate_timeline": self.candidate_timeline,
            "fingerprint_schema": self.fingerprint_schema,
            "component_hashes": self.component_hashes,
        }


class Warehouse:
    def __init__(self, normalized_dir: Path | None = None, ensure_fixtures: bool = True):
        self.normalized_dir = normalized_dir or NORMALIZED_DIR
        self.raw_dir = RAW_DIR
        if ensure_fixtures and not (self.normalized_dir / "polls.parquet").exists():
            build_fixtures()
        self.polls = pd.read_parquet(self.normalized_dir / "polls.parquet")
        try:
            self.polls = align_poll_frame(merge_historical_polls(self.polls))
        except Exception:  # noqa: BLE001
            self.polls = align_poll_frame(self.polls)
        self.races = pd.read_parquet(self.normalized_dir / "races.parquet")
        timeline_path = self.normalized_dir / "candidate_timeline.parquet"
        self.candidate_timeline = (
            pd.read_parquet(timeline_path) if timeline_path.exists() else pd.DataFrame()
        )
        try:
            from midterms.evidence.official_ballot import merge_official_into_races

            self.races = merge_official_into_races(self.races)
        except Exception:  # noqa: BLE001
            pass
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
        """Join VoteHub / FTE pollster ratings onto poll rows for the as-of date.

        Never rebuilds an unfiltered (current) ratings lookup when the historical
        filter yields zero rows — unrated pollsters get prior_default.
        """
        if polls.empty:
            return polls, {"as_of": as_of_d.isoformat(), "n_rated": 0, "n_prior_default": 0}
        from midterms.evidence.ratings import build_rating_lookup_with_meta

        lookup, lookup_meta = build_rating_lookup_with_meta(as_of=as_of_d)
        quality, house, extra, grade, source = [], [], [], [], []
        used: dict[str, Any] = {}
        n_default = 0
        for _, row in polls.iterrows():
            r = rating_for(str(row["pollster_id"]), lookup)
            if r.source == "prior_default":
                n_default += 1
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
                "available_at": r.available_at,
            }
        out = polls.copy()
        out["quality_weight"] = quality
        out["house_effect_prior"] = house
        out["extra_sd_prior"] = extra
        out["pollster_grade"] = grade
        out["rating_source"] = source
        meta = {
            **lookup_meta,
            "n_rated": int(len(lookup)),
            "n_prior_default": int(n_default),
            "pollsters": used,
        }
        return out, meta

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

        # Resolve candidate/race state before filtering inactive ballot rows.
        from midterms.evidence.candidate_timeline import apply_candidate_timeline

        races, candidate_timeline_meta = apply_candidate_timeline(
            races, getattr(self, "candidate_timeline", pd.DataFrame()), as_of=as_of_d
        )

        # Drop inactive ballot rows from contested forecast universe (keep held)
        if len(races):
            mask = races.apply(
                lambda r: bool(r.get("not_up")) or is_active_ballot_row(r),
                axis=1,
            )
            races = races[mask]
        if len(races):
            from midterms.evidence.outcome_identity import (
                attach_declared_held_independent_caucus,
            )

            # Existing normalized stores may predate the explicit caucus columns.
            # Attach the declared accounting assumption in the model snapshot.
            races = attach_declared_held_independent_caucus(races)
        if election_id == "senate-2026" and len(races):
            from midterms.evidence.outcome_identity import attach_2026_ticket_identities

            # Development fallback only. The snapshot remains explicitly
            # degraded until a sourced bitemporal candidate timeline exists.
            races = attach_2026_ticket_identities(races)

        # Verify source bytes, then attach one point-in-time derived side table
        # to all races in this snapshot. Truth/result fields are untouched.
        source_sha = None
        source_years: tuple[int, ...] = ()
        prior_snapshot_sha = None
        prior_snapshot_path = None
        if self.normalized_dir.resolve() == NORMALIZED_DIR.resolve():
            from midterms.evidence.presidential_results import (
                SOURCE_MANIFEST_PATH,
                select_source_years,
                verified_source_set_sha256,
            )

            if SOURCE_MANIFEST_PATH.exists():
                source_sha = verified_source_set_sha256()
                source_years = select_source_years(as_of_d)
                if source_years:
                    from midterms.evidence.presidential_prior import (
                        attach_prior_snapshot,
                        materialize_prior_snapshot,
                    )

                    prior_snapshot, prior_path = materialize_prior_snapshot(as_of_d)
                    races = attach_prior_snapshot(races, prior_snapshot)
                    prior_snapshot_sha = str(prior_snapshot["snapshot_sha256"])
                    prior_snapshot_path = prior_path.resolve().relative_to(ROOT.resolve()).as_posix()
        if "prior_source" not in races.columns:
            races["prior_source"] = "legacy_unverified_fixture"
        else:
            races["prior_source"] = races["prior_source"].fillna("legacy_unverified_fixture")

        # Leakage canary: if any remaining poll has available_at > as_of, fail closed
        if len(polls):
            from midterms.evidence.point_in_time import assert_no_future_rows

            assert_no_future_rows(polls, as_of_d, column="available_at", label="polls")

        polls, rating_meta = self._attach_poll_priors(polls.reset_index(drop=True), as_of_d)

        races = races.reset_index(drop=True)
        material_source_hashes = _selected_material_source_hashes(
            normalized_dir=self.normalized_dir,
            as_of=as_of_d,
            election_id=election_id,
        )
        snapshot_id, component_hashes = evidence_snapshot_fingerprint(
            election_id=election_id,
            as_of=as_of_d,
            polls=polls,
            races=races,
            candidate_timeline=candidate_timeline_meta,
            pollster_ratings=rating_meta,
            prior_snapshot_sha256=prior_snapshot_sha,
            presidential_source_sha256=source_sha,
            presidential_source_years=source_years,
            material_source_hashes=material_source_hashes,
        )
        return EvidenceSnapshot(
            as_of=as_of_d,
            election_id=election_id,
            polls=polls,
            races=races,
            results_known=results_known.reset_index(drop=True)
            if len(results_known)
            else results_known,
            snapshot_id=snapshot_id,
            pollster_ratings=rating_meta,
            presidential_source_sha256=source_sha,
            presidential_source_years=source_years,
            prior_snapshot_sha256=prior_snapshot_sha,
            prior_snapshot_path=prior_snapshot_path,
            candidate_timeline=candidate_timeline_meta,
            component_hashes=component_hashes,
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
