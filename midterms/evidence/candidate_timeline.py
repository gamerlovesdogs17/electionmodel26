"""Bitemporal candidate and contest-state snapshots.

The loader never invents history.  A missing timeline is explicitly degraded;
callers can choose to block publication-quality replay on that status.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, ROOT

TIMELINE_SCHEMA_VERSION = "candidate-timeline-v2"
TIMELINE_PARSER_VERSION = "candidate-timeline-ingest-v1"
TIMELINE_COLUMNS = (
    "election_id", "event_id", "race_id", "candidate_id", "modeled_side", "event_type",
    "effective_at", "available_at", "retrieved_at", "candidate_name",
    "ballot_party", "caucus_affiliation", "caucus_basis", "incumbent_status",
    "vacancy_reason", "election_phase", "ballot_status", "source_url",
    "source_tier", "source_object_sha256", "source_hash", "parser_version",
    "valid_from", "valid_to", "correction_of_event_id",
)

REQUIRED_SOURCE_COLUMNS = (
    "election_id", "event_id", "race_id", "candidate_id", "modeled_side",
    "event_type", "effective_at", "available_at", "retrieved_at", "source_url",
    "source_tier", "source_object_sha256", "parser_version", "valid_from",
)
ALLOWED_EVENT_TYPES = frozenset({
    "declared", "entered", "nomination", "nominated", "ballot_qualification",
    "qualified", "withdrawal", "withdrawn", "replacement", "party_change",
    "status_change", "vacancy", "runoff_advancement", "special_election_phase",
})

REQUIRED_CONTESTED_IDENTITY_COLUMNS = (
    "modeled_candidate_id",
    "modeled_ballot_party",
    "opposing_candidate_id",
    "opposing_ballot_party",
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


def _read_timeline_input(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("events")
        if not isinstance(payload, list):
            raise ValueError("candidate timeline JSON must be a list or {'events': [...]} object")
        return pd.DataFrame(payload)
    raise ValueError("candidate timeline input must be CSV, JSON, or Parquet")


def validate_candidate_timeline_source(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate source-backed bitemporal events without inventing missing facts."""
    missing_columns = sorted(set(REQUIRED_SOURCE_COLUMNS) - set(frame.columns))
    if missing_columns:
        raise ValueError(f"candidate timeline missing required columns: {missing_columns}")
    out = align_candidate_timeline(frame)
    for column in REQUIRED_SOURCE_COLUMNS:
        missing = out[column].isna() | out[column].astype(str).str.strip().eq("")
        if missing.any():
            raise ValueError(f"candidate timeline has {int(missing.sum())} blank {column} values")
    if not out["modeled_side"].astype(str).isin({"modeled", "opposing"}).all():
        raise ValueError("candidate timeline modeled_side must be modeled or opposing")
    invalid_events = sorted(set(out["event_type"].astype(str)) - ALLOWED_EVENT_TYPES)
    if invalid_events:
        raise ValueError(f"candidate timeline has unsupported event types: {invalid_events}")
    for column in ("effective_at", "available_at", "retrieved_at", "valid_from"):
        parsed = pd.to_datetime(out[column], errors="coerce", utc=True)
        if parsed.isna().any():
            raise ValueError(f"candidate timeline has invalid {column}")
        out[column] = parsed.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_to = pd.to_datetime(out["valid_to"], errors="coerce", utc=True)
    supplied_valid_to = out["valid_to"].notna() & out["valid_to"].astype(str).str.strip().ne("")
    if (supplied_valid_to & valid_to.isna()).any():
        raise ValueError("candidate timeline has invalid valid_to")
    out["valid_to"] = valid_to.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if out["event_id"].astype(str).duplicated().any():
        raise ValueError("candidate timeline event_id must be unique; corrections need a new event_id")
    hashes = out["source_object_sha256"].astype(str).str.lower()
    if not hashes.str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("candidate timeline source_object_sha256 must be a SHA-256 hex digest")
    out["source_hash"] = out["source_hash"].where(
        out["source_hash"].notna() & out["source_hash"].astype(str).str.strip().ne(""),
        out["source_object_sha256"],
    )
    return out.sort_values(
        ["election_id", "race_id", "available_at", "effective_at", "event_id"],
        kind="stable",
    ).reset_index(drop=True)


def ingest_candidate_timeline(
    input_path: str | Path,
    *,
    normalized_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Seal a supplied source-backed candidate history into the warehouse."""
    path = Path(input_path)
    raw_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    normalized = validate_candidate_timeline_source(_read_timeline_input(path))
    semantic_sha256 = candidate_timeline_fingerprint(normalized)
    normalized_path = normalized_path or (NORMALIZED_DIR / "candidate_timeline.parquet")
    manifest_path = manifest_path or (MANIFESTS_DIR / "candidate_timeline.json")
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_parquet(normalized_path, index=False)
    elections = sorted(normalized["election_id"].astype(str).unique())
    manifest = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "parser_version": TIMELINE_PARSER_VERSION,
        "raw_input_sha256": raw_sha256,
        "normalized_semantic_sha256": semantic_sha256,
        "n_events": int(len(normalized)),
        "election_ids": elections,
        "normalized_path": (
            normalized_path.resolve().relative_to(ROOT.resolve()).as_posix()
            if normalized_path.resolve().is_relative_to(ROOT.resolve()) else normalized_path.name
        ),
        "source_traceability_required": True,
        "production_eligible": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def apply_candidate_timeline(
    races: pd.DataFrame,
    timeline: pd.DataFrame,
    *,
    as_of: str | date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply only events both effective and knowable by ``as_of``."""
    cutoff = pd.Timestamp(as_of).date()
    out = races.copy()
    required_mask = (
        ~out["not_up"].fillna(False).astype(bool)
        if "not_up" in out.columns else pd.Series(True, index=out.index)
    )
    required_race_ids = sorted(out.loc[required_mask, "race_id"].astype(str).unique())
    events = align_candidate_timeline(timeline)
    if events.empty:
        out["candidate_timeline_status"] = "degraded_missing_timeline"
        return out, {
            "schema_version": TIMELINE_SCHEMA_VERSION,
            "status": "degraded_missing_timeline",
            "production_eligible": False,
            "snapshot_sha256": None,
            "n_events_applied": 0,
            "n_required_races": len(required_race_ids),
            "n_point_in_time": 0,
            "n_degraded": len(required_race_ids),
            "required_race_ids": required_race_ids,
            "traceable": False,
            "reasons": ["candidate timeline source is missing"],
            "latest_retrieved_at": None,
            "latest_effective_at": None,
        }
    for column in ("effective_at", "available_at"):
        parsed = pd.to_datetime(events[column], errors="coerce").dt.date
        if parsed.isna().any():
            raise ValueError(f"candidate timeline has missing/invalid {column}")
        events[column] = parsed
    usable = events[(events["effective_at"] <= cutoff) & (events["available_at"] <= cutoff)].copy()
    if "valid_from" in usable.columns:
        cutoff_ts = pd.Timestamp(cutoff).tz_localize("UTC")
        valid_from = pd.to_datetime(usable["valid_from"], errors="coerce", utc=True)
        valid_to = pd.to_datetime(usable["valid_to"], errors="coerce", utc=True)
        usable = usable[
            (valid_from.isna() | (valid_from <= cutoff_ts))
            & (valid_to.isna() | (valid_to > cutoff_ts))
        ]
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
    identity_complete = pd.Series(True, index=out.index)
    for column in REQUIRED_CONTESTED_IDENTITY_COLUMNS:
        values = out[column]
        identity_complete &= values.notna() & values.astype(str).str.strip().ne("")
    incomplete_required = required_mask & ~identity_complete
    out.loc[
        incomplete_required & out["candidate_timeline_status"].eq("point_in_time"),
        "candidate_timeline_status",
    ] = "degraded_incomplete_identity"
    required_usable = usable[usable["race_id"].astype(str).isin(required_race_ids)]
    traceable = bool(len(required_usable) or not required_race_ids) and all(
        required_usable[column].notna().all()
        and required_usable[column].astype(str).str.strip().ne("").all()
        for column in ("retrieved_at", "source_url", "source_hash", "parser_version")
    )
    required_status = out.loc[required_mask, "candidate_timeline_status"]
    complete = bool(required_status.eq("point_in_time").all())
    status = "point_in_time" if complete and traceable else (
        "partial_untraceable" if complete else "partial"
    )
    n_point_in_time = int(required_status.eq("point_in_time").sum())
    reasons: list[str] = []
    if n_point_in_time != len(required_race_ids):
        reasons.append(
            f"{len(required_race_ids) - n_point_in_time} required contested races lack a complete as-of identity"
        )
    if not traceable:
        reasons.append("required timeline events lack complete source traceability")
    return out, {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "status": status,
        "production_eligible": status == "point_in_time",
        "traceable": traceable,
        "snapshot_sha256": candidate_timeline_fingerprint(usable),
        "n_events_applied": int(len(usable)),
        "as_of": cutoff.isoformat(),
        "n_required_races": len(required_race_ids),
        "n_point_in_time": n_point_in_time,
        "n_degraded": len(required_race_ids) - n_point_in_time,
        "required_race_ids": required_race_ids,
        "reasons": reasons,
        "latest_retrieved_at": (
            pd.to_datetime(required_usable["retrieved_at"], errors="coerce", utc=True).max().isoformat()
            if len(required_usable)
            and pd.to_datetime(required_usable["retrieved_at"], errors="coerce", utc=True).notna().any()
            else None
        ),
        "latest_effective_at": (
            max(required_usable["effective_at"]).isoformat() if len(required_usable) else None
        ),
    }


def audit_candidate_timeline(
    races: pd.DataFrame,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the publication gate for candidate identity at an as-of snapshot."""
    meta = dict(metadata or {})
    required = (
        races[~races["not_up"].fillna(False).astype(bool)]
        if "not_up" in races.columns else races
    )
    statuses = (
        required["candidate_timeline_status"].fillna("degraded_missing_status").astype(str)
        if "candidate_timeline_status" in required.columns else
        pd.Series(["degraded_missing_status"] * len(required), dtype=str)
    )
    n_point = int(statuses.eq("point_in_time").sum())
    n_required = int(len(required))
    reasons = list(meta.get("reasons") or [])
    if n_point != n_required:
        reasons.append(f"{n_required - n_point} required contested races use degraded identity")
    missing_identity = 0
    for _, row in required.iterrows():
        if any(
            pd.isna(row.get(column)) or not str(row.get(column)).strip()
            for column in REQUIRED_CONTESTED_IDENTITY_COLUMNS
        ):
            missing_identity += 1
    if missing_identity:
        reasons.append(
            f"{missing_identity} required contested races lack complete modeled/opposing identity"
        )
    if not meta.get("traceable") and n_required:
        reasons.append("candidate timeline is not source-traceable")
    snapshot_hash = meta.get("snapshot_sha256")
    if n_required and not snapshot_hash:
        reasons.append("candidate timeline snapshot hash is missing")
    eligible = n_point == n_required and missing_identity == 0 and bool(meta.get("traceable") or n_required == 0) and (
        bool(snapshot_hash) or n_required == 0
    )
    return {
        "status": "point_in_time" if eligible else str(meta.get("status") or "degraded"),
        "n_required_races": n_required,
        "n_point_in_time": n_point,
        "n_degraded": n_required - n_point,
        "n_incomplete_identity": missing_identity,
        "required_identity_columns": list(REQUIRED_CONTESTED_IDENTITY_COLUMNS),
        "traceable": bool(meta.get("traceable")),
        "snapshot_sha256": snapshot_hash,
        "reasons": sorted(set(reasons)),
        "eligible": eligible,
        "publication_eligible": eligible,
        "held_seats_excluded": True,
        "schema_version": meta.get("schema_version") or TIMELINE_SCHEMA_VERSION,
    }


def audit_candidate_timeline_history(
    races: pd.DataFrame,
    timeline: pd.DataFrame,
    *,
    cutoffs: dict[str, str | date],
) -> dict[str, Any]:
    """Audit point-in-time identity coverage at predeclared historical cutoffs."""
    rows: list[dict[str, Any]] = []
    for election_id, cutoff in sorted(cutoffs.items()):
        subset = races[
            races["election_id"].astype(str).eq(str(election_id))
        ].copy()
        if subset.empty:
            rows.append({
                "election_id": str(election_id),
                "as_of": pd.Timestamp(cutoff).date().isoformat(),
                "status": "degraded_missing_race_universe",
                "n_required_races": 0,
                "n_point_in_time": 0,
                "n_degraded": 0,
                "traceable": False,
                "snapshot_sha256": None,
                "reasons": ["race universe is missing at required historical cutoff"],
                "eligible": False,
                "publication_eligible": False,
            })
            continue
        applied, metadata = apply_candidate_timeline(subset, timeline, as_of=cutoff)
        audit = audit_candidate_timeline(applied, metadata)
        rows.append({
            "election_id": str(election_id),
            "as_of": pd.Timestamp(cutoff).date().isoformat(),
            **audit,
        })
    blocking = [
        f"{row['election_id']}@{row['as_of']}"
        for row in rows if not row["publication_eligible"]
    ]
    return {
        "schema_version": "candidate-timeline-history-audit-v1",
        "cutoffs": rows,
        "blocking_cutoffs": blocking,
        "publication_eligible": bool(rows) and not blocking,
    }
