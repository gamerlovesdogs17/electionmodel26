"""Bitemporal candidate and contest-state snapshots.

The loader never invents history.  A missing timeline is explicitly degraded;
callers can choose to block publication-quality replay on that status.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import pandas as pd


TIMELINE_SCHEMA_VERSION = "candidate-timeline-v1"
TIMELINE_COLUMNS = (
    "event_id", "race_id", "candidate_id", "modeled_side", "event_type",
    "effective_at", "available_at", "retrieved_at", "candidate_name",
    "ballot_party", "caucus_affiliation", "caucus_basis", "incumbent_status",
    "vacancy_reason", "election_phase", "ballot_status", "source_url",
    "source_hash", "parser_version",
)


def align_candidate_timeline(frame: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    out = pd.DataFrame(frame).copy()
    for column in TIMELINE_COLUMNS:
        if column not in out.columns:
            out[column] = None
    return out[list(TIMELINE_COLUMNS)]


def candidate_timeline_fingerprint(frame: pd.DataFrame) -> str:
    records = align_candidate_timeline(frame).fillna("").astype(str).sort_values(
        ["race_id", "available_at", "effective_at", "event_id"], kind="stable"
    ).to_dict(orient="records")
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_candidate_timeline(
    races: pd.DataFrame,
    timeline: pd.DataFrame,
    *,
    as_of: str | date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply only events both effective and knowable by ``as_of``."""
    cutoff = pd.Timestamp(as_of).date()
    out = races.copy()
    events = align_candidate_timeline(timeline)
    if events.empty:
        out["candidate_timeline_status"] = "degraded_missing_timeline"
        return out, {
            "schema_version": TIMELINE_SCHEMA_VERSION,
            "status": "degraded_missing_timeline",
            "production_eligible": False,
            "snapshot_sha256": None,
            "n_events_applied": 0,
        }
    for column in ("effective_at", "available_at"):
        parsed = pd.to_datetime(events[column], errors="coerce").dt.date
        if parsed.isna().any():
            raise ValueError(f"candidate timeline has missing/invalid {column}")
        events[column] = parsed
    usable = events[(events["effective_at"] <= cutoff) & (events["available_at"] <= cutoff)].copy()
    usable = usable.sort_values(
        ["race_id", "modeled_side", "effective_at", "available_at", "event_id"], kind="stable"
    )
    mapping = {
        "candidate_id": "{side}_candidate_id",
        "ballot_party": "{side}_ballot_party",
        "caucus_affiliation": "{side}_caucus",
        "caucus_basis": "{side}_caucus_basis",
    }
    for column in (
        "modeled_candidate_id", "modeled_ballot_party", "modeled_caucus",
        "modeled_caucus_basis", "opposing_candidate_id", "opposing_ballot_party",
        "opposing_caucus", "opposing_caucus_basis", "candidate_timeline_status",
    ):
        if column not in out.columns:
            out[column] = None
    # For races governed by a timeline, do not retain a current/future identity
    # from the base race table. Reconstruct the snapshot only from knowable events.
    governed = set(events["race_id"].astype(str))
    governed_mask = out["race_id"].astype(str).isin(governed)
    for column in (
        "modeled_candidate_id", "modeled_ballot_party", "modeled_caucus",
        "modeled_caucus_basis", "opposing_candidate_id", "opposing_ballot_party",
        "opposing_caucus", "opposing_caucus_basis",
    ):
        out.loc[governed_mask, column] = None
    out.loc[governed_mask, "ballot_status"] = "not_yet_known"
    race_index = {str(row["race_id"]): idx for idx, row in out.iterrows()}
    for _, event in usable.iterrows():
        idx = race_index.get(str(event["race_id"]))
        if idx is None:
            continue
        side = str(event["modeled_side"] or "")
        if side not in {"modeled", "opposing"}:
            raise ValueError("candidate timeline modeled_side must be modeled or opposing")
        for source, template in mapping.items():
            value = event[source]
            if pd.notna(value) and str(value) != "":
                out.at[idx, template.format(side=side)] = value
        for source in ("incumbent_status", "vacancy_reason", "election_phase", "ballot_status"):
            value = event[source]
            if pd.notna(value) and str(value) != "":
                target = "is_open" if source == "incumbent_status" else source
                if source == "incumbent_status":
                    value = str(value).lower() == "open"
                out.at[idx, target] = value
        if str(event["event_type"]).lower() in {"withdrawal", "withdrawn"}:
            out.at[idx, "ballot_status"] = "withdrawn"
        elif str(event["event_type"]).lower() in {"nomination", "nominated"} and (
            pd.isna(event["ballot_status"]) or str(event["ballot_status"]) == ""
        ):
            out.at[idx, "ballot_status"] = "nominated"
        elif str(event["event_type"]).lower() in {"ballot_qualification", "qualified"} and (
            pd.isna(event["ballot_status"]) or str(event["ballot_status"]) == ""
        ):
            out.at[idx, "ballot_status"] = "qualified"
        out.at[idx, "candidate_timeline_status"] = "point_in_time"
    out["candidate_timeline_status"] = out["candidate_timeline_status"].fillna(
        "degraded_no_event_for_race"
    )
    traceable = bool(len(usable)) and all(
        usable[column].notna().all() and usable[column].astype(str).str.strip().ne("").all()
        for column in ("retrieved_at", "source_url", "source_hash", "parser_version")
    )
    complete = out["candidate_timeline_status"].eq("point_in_time").all()
    status = "point_in_time" if complete and traceable else (
        "partial_untraceable" if complete else "partial"
    )
    return out, {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "status": status,
        "production_eligible": status == "point_in_time",
        "traceable": traceable,
        "snapshot_sha256": candidate_timeline_fingerprint(usable),
        "n_events_applied": int(len(usable)),
        "as_of": cutoff.isoformat(),
    }
