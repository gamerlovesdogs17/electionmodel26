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
CANDIDATE_STATE_SCHEMA_VERSION = "candidate-state-eligibility-v1"
CURRENT_CANDIDATE_STATE_SCHEMA_VERSION = "candidate-state-eligibility-v2-current-registry"
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
    "qualified", "qualification", "withdrawal", "withdrawn", "replacement", "party_change",
    "status_change", "vacancy", "death", "runoff_advancement", "special_election_phase",
})
BALLOT_IDENTITY_EVENT_TYPES = frozenset({
    "nomination", "nominated", "ballot_qualification", "qualified", "qualification",
    "replacement", "runoff_advancement",
})
IDENTITY_SENSITIVE_EVENT_TYPES = frozenset({
    "withdrawal", "withdrawn", "replacement", "party_change", "status_change",
    "vacancy", "death", "runoff_advancement", "special_election_phase",
})

CANDIDATE_STATE_COLUMNS = (
    "candidate_state",
    "candidate_state_reason",
    "candidate_state_eligible",
    "candidate_identity_required",
    "candidate_identity_resolved",
    "candidate_identity_eligible",
    "binary_score_eligible",
    "binary_score_exclusion_reason",
)

REQUIRED_CONTESTED_IDENTITY_COLUMNS = (
    "modeled_candidate_id",
    "modeled_ballot_party",
    "opposing_candidate_id",
    "opposing_ballot_party",
)


def _canonical_sha256(payload: Any) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): normalize(item) for key, item in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if isinstance(value, (pd.Timestamp, date)):
            return pd.Timestamp(value).isoformat()
        if hasattr(value, "item") and not isinstance(value, (str, bytes)):
            try:
                value = value.item()
            except (TypeError, ValueError):
                pass
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return value

    canonical = json.dumps(
        normalize(payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _present(value: Any) -> bool:
    return value is not None and not pd.isna(value) and bool(str(value).strip())


def _normalized_name(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _identity_complete(row: pd.Series) -> bool:
    return all(_present(row.get(column)) for column in REQUIRED_CONTESTED_IDENTITY_COLUMNS)


def _usable_events_as_of(timeline: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    events = align_candidate_timeline(timeline)
    if events.empty:
        return events
    effective = pd.to_datetime(events["effective_at"], errors="coerce", utc=True)
    available = pd.to_datetime(events["available_at"], errors="coerce", utc=True)
    if effective.isna().any() or available.isna().any():
        raise ValueError("candidate timeline has missing/invalid effective_at or available_at")
    cutoff_ts = pd.Timestamp(cutoff, tz="UTC")
    usable = events[(effective <= cutoff_ts) & (available <= cutoff_ts)].copy()
    valid_from = pd.to_datetime(usable["valid_from"], errors="coerce", utc=True)
    valid_to = pd.to_datetime(usable["valid_to"], errors="coerce", utc=True)
    usable = usable[
        (valid_from.isna() | (valid_from <= cutoff_ts))
        & (valid_to.isna() | (valid_to > cutoff_ts))
    ]
    return usable.sort_values(
        ["race_id", "modeled_side", "effective_at", "available_at", "event_id"],
        kind="stable",
    ).reset_index(drop=True)


def _poll_matchup_key(row: pd.Series) -> str | None:
    modeled = (
        str(row.get("modeled_candidate_id")).strip()
        if _present(row.get("modeled_candidate_id")) else _normalized_name(
            row.get("modeled_candidate_name") if _present(row.get("modeled_candidate_name")) else ""
        )
    )
    opposing = (
        str(row.get("opposing_candidate_id")).strip()
        if _present(row.get("opposing_candidate_id")) else _normalized_name(
            row.get("opposing_candidate_name") if _present(row.get("opposing_candidate_name")) else ""
        )
    )
    if modeled or opposing:
        return f"{modeled}|{opposing}"
    dem = str(row.get("dem_candidate_id") or "").strip() or _normalized_name(
        row.get("dem_candidate_name")
    )
    rep = str(row.get("rep_candidate_id") or "").strip() or _normalized_name(
        row.get("rep_candidate_name")
    )
    if dem or rep:
        return f"{dem}|{rep}"
    matchup = str(row.get("matchup_id") or "").strip()
    return matchup or None


def _poll_matches_resolved_identity(row: pd.Series, race: pd.Series) -> bool:
    """Match separate poll/timeline identifier namespaces by ID or normalized name."""
    checks: list[bool] = []
    generic = _present(row.get("modeled_candidate_id")) or _present(
        row.get("modeled_candidate_name")
    )
    for poll_id, poll_name, race_id, race_name in (
        (
            "modeled_candidate_id" if generic else "dem_candidate_id",
            "modeled_candidate_name" if generic else "dem_candidate_name",
            "modeled_candidate_id", "modeled_candidate_name",
        ),
        (
            "opposing_candidate_id" if generic else "rep_candidate_id",
            "opposing_candidate_name" if generic else "rep_candidate_name",
            "opposing_candidate_id", "opposing_candidate_name",
        ),
    ):
        pid, rid = row.get(poll_id), race.get(race_id)
        pname, rname = row.get(poll_name), race.get(race_name)
        if _present(pid) and _present(rid) and str(pid) == str(rid):
            checks.append(True)
        elif _present(pname) and _present(rname):
            checks.append(_normalized_name(pname) == _normalized_name(rname))
        else:
            checks.append(False)
    return all(checks)


def candidate_structural_gaps_for_cutoff(
    source_audit: dict[str, Any] | None,
    *,
    election_id: str,
    as_of: str | date,
) -> list[dict[str, Any]]:
    """Return only predeclared structural gaps for the exact replay cutoff."""
    cutoff = pd.Timestamp(as_of).date().isoformat()
    for block in (source_audit or {}).get("coverage", {}).values():
        if str(block.get("as_of")) != cutoff:
            continue
        required = {str(value) for value in block.get("required_race_ids") or []}
        if required and not any(value.startswith(f"{election_id}-") for value in required):
            continue
        return [dict(value) for value in block.get("structurally_unavailable") or []]
    return []


def filter_candidate_state_score_exclusions(
    results: pd.DataFrame,
    metadata: dict[str, Any] | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Remove only predeclared race/cutoff binary-score exclusions."""
    exclusions = [dict(value) for value in (metadata or {}).get("score_exclusions") or []]
    excluded = {str(value.get("race_id")) for value in exclusions if value.get("race_id")}
    if results is None or results.empty or not excluded:
        return results, exclusions
    return results[~results["race_id"].astype(str).isin(excluded)].copy(), exclusions


def apply_candidate_state_contract(
    races: pd.DataFrame,
    timeline: pd.DataFrame,
    polls: pd.DataFrame | None = None,
    *,
    as_of: str | date,
    structural_gaps: list[dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Apply the conditional candidate-identity and binary-score contract.

    Candidate identity is required only when it changes included evidence or
    the definition of the modeled D-vs-R target.  Ordinary races can remain
    side-only.  Pre-primary candidate polls and unresolved incompatible
    matchups are removed without consulting eventual nominees.  The exclusions
    and classifications remain in metadata and therefore in snapshot lineage.
    """
    cutoff = pd.Timestamp(as_of).date()
    out, timeline_meta = apply_candidate_timeline(races, timeline, as_of=cutoff)
    # Present-day 2026 candidate selection is intentionally separate from the
    # historical bitemporal timeline. The current registry is attached only at
    # or after its review boundary and therefore cannot affect prior cycles or
    # an earlier 2026 replay.
    if out.get(
        "election_id", pd.Series("", index=out.index),
    ).astype(str).eq("senate-2026").any():
        from midterms.evidence.outcome_identity import attach_2026_ticket_identities

        out = attach_2026_ticket_identities(out, as_of=cutoff)
    for column in (
        "modeled_candidate_id", "modeled_candidate_name", "modeled_ballot_party",
        "modeled_caucus", "modeled_caucus_basis", "opposing_candidate_id",
        "opposing_candidate_name", "opposing_ballot_party", "opposing_caucus",
        "opposing_caucus_basis",
    ):
        if column not in out.columns:
            out[column] = None
    poll_frame = pd.DataFrame() if polls is None else polls.copy()
    if len(poll_frame):
        available = pd.to_datetime(poll_frame.get("available_at"), errors="coerce").dt.date
        poll_frame = poll_frame[available.notna() & (available <= cutoff)].copy()
        if "exclusion_status" in poll_frame.columns:
            poll_frame = poll_frame[
                poll_frame["exclusion_status"].fillna("include").astype(str).eq("include")
            ].copy()
    usable = _usable_events_as_of(timeline, cutoff)
    gaps_by_race = {
        str(gap.get("race_id")): dict(gap)
        for gap in (structural_gaps or []) if gap.get("race_id")
    }
    for column in CANDIDATE_STATE_COLUMNS:
        if column not in out.columns:
            out[column] = None

    future_or_hypothetical: list[dict[str, Any]] = []
    poll_exclusions: list[dict[str, Any]] = []
    candidate_compatible_indices: set[Any] = set()
    candidate_compatible_by_race: dict[str, list[str]] = {}
    if len(poll_frame) and "hypothetical" in poll_frame.columns:
        hypothetical = poll_frame["hypothetical"].map(
            lambda value: bool(value) if pd.notna(value) else False
        )
        for _, row in poll_frame[hypothetical].iterrows():
            future_or_hypothetical.append({
                "poll_id": str(row.get("poll_id") or ""),
                "race_id": str(row.get("race_id") or ""),
                "reason": "hypothetical_matchup_excluded",
            })
        poll_frame = poll_frame[~hypothetical].copy()

    records: list[dict[str, Any]] = []
    safe_indices = set(poll_frame.index)
    identity_missing: list[str] = []
    ambiguous_cases: list[dict[str, Any]] = []
    score_exclusions: list[dict[str, Any]] = []
    contested = ~out.get("not_up", pd.Series(False, index=out.index)).map(
        lambda value: bool(value) if pd.notna(value) else False
    )

    for index, race in out.iterrows():
        race_id = str(race.get("race_id") or "")
        election_id = str(race.get("election_id") or "")
        year_text = election_id.rsplit("-", 1)[-1]
        year = int(year_text) if year_text.isdigit() else 0
        race_polls = poll_frame[poll_frame.get(
            "race_id", pd.Series("", index=poll_frame.index),
        ).astype(str).eq(race_id)]
        candidate_fields = [
            column for column in (
                "modeled_candidate_id", "modeled_candidate_name",
                "opposing_candidate_id", "opposing_candidate_name",
                "dem_candidate_id", "dem_candidate_name", "rep_candidate_id",
                "rep_candidate_name", "matchup_id",
            ) if column in race_polls.columns
        ]
        candidate_specific = (
            race_polls[candidate_fields].notna().any(axis=1)
            if candidate_fields else pd.Series(False, index=race_polls.index)
        )
        candidate_polls = race_polls[candidate_specific].copy()
        matchup_keys = sorted({
            key for _, row in candidate_polls.iterrows()
            if (key := _poll_matchup_key(row)) is not None
        })
        exact_identity = _identity_complete(race)
        current_registry_resolved = bool(
            year == 2026
            and exact_identity
            and str(race.get("identity_source") or "")
            == "reviewed_current_candidate_registry"
            and str(race.get("current_matchup_status") or "") == "reviewed_current"
        )
        gap = gaps_by_race.get(race_id)
        race_events = usable[usable["race_id"].astype(str).eq(race_id)]
        race_events_traceable = bool(len(race_events)) and all(
            race_events[column].notna().all()
            and race_events[column].astype(str).str.strip().ne("").all()
            for column in ("retrieved_at", "source_url", "source_hash", "parser_version")
        )
        sensitive_events = race_events[
            race_events["event_type"].astype(str).str.lower().isin(IDENTITY_SENSITIVE_EVENT_TYPES)
        ]
        latest_by_side = (
            race_events.groupby("modeled_side", sort=False).tail(1)
            if len(race_events) else race_events
        )
        has_unresolved_identity_status = bool(len(latest_by_side)) and latest_by_side[
            "event_type"
        ].astype(str).str.lower().isin({
            "withdrawal", "withdrawn", "vacancy", "death", "status_change",
        }).any()
        point_in_time_resolved = bool(
            exact_identity
            and race_events_traceable
            and not has_unresolved_identity_status
        )
        current_identity_resolved = bool(
            current_registry_resolved or point_in_time_resolved
        )

        state = "side_only_stable"
        reason = "ordinary_binary_party_sides_stable"
        eligible = True
        identity_required = False
        identity_resolved = False
        score_eligible = bool(contested.loc[index])
        score_reason = None
        if len(matchup_keys) > 1:
            ambiguous_cases.append({
                "race_id": race_id,
                "matchup_ids": matchup_keys,
                "poll_ids": sorted(candidate_polls["poll_id"].astype(str).unique()),
            })

        if not bool(contested.loc[index]):
            state = "not_applicable_held_seat"
            reason = "held_seat_candidate_identity_not_required"
            score_eligible = False
            score_reason = "held_seat_not_contested"
        elif gap and str(gap.get("gap_type")) == "special_election_finalists_not_yet_determined":
            state = "ineligible_for_binary_scoring"
            reason = str(gap.get("gap_type"))
            score_eligible = False
            score_reason = "final_binary_pairing_not_yet_determined"
        elif str(race.get("state") or "") == "AK" and year >= 2022:
            identity_required = year == 2026
            identity_resolved = current_identity_resolved if year == 2026 else False
            state = "ineligible_for_binary_scoring"
            reason = "ranked_choice_multi_candidate_structure"
            eligible = identity_resolved if year == 2026 else True
            score_eligible = False
            score_reason = "no_single_predeclared_dem_vs_rep_final_pair"
        elif (
            year == 2026
            and _present(race.get("modeled_ballot_party"))
            and str(race.get("modeled_ballot_party") or "").upper()
            not in {"D", "DEM", "DEMOCRAT", "DEMOCRATIC"}
        ):
            identity_required = True
            identity_resolved = current_identity_resolved
            state = "ineligible_for_binary_scoring"
            reason = "non_major_party_contest_requires_separate_statistical_target"
            eligible = identity_resolved
            score_eligible = False
            score_reason = "no_validated_non_major_party_margin_transform"
            if not identity_resolved:
                identity_missing.append(race_id)
        elif year == 2026:
            identity_required = True
            identity_resolved = current_identity_resolved
            state = "identity_required"
            reason = (
                "current_matchup_resolved_by_reviewed_registry"
                if current_registry_resolved
                else "current_matchup_resolved_by_point_in_time_identity"
                if point_in_time_resolved
                else "current_candidate_identity_requires_reviewed_registry"
            )
            eligible = identity_resolved
            if not identity_resolved:
                identity_missing.append(race_id)
        elif gap and str(gap.get("gap_type")) == "nomination_not_yet_determined":
            state = "structurally_unresolved"
            reason = "pre_nomination_side_only"
            # A poll naming an eventual nominee before the primary cannot enter
            # the replay merely because that person later won the nomination.
            for poll_index in candidate_polls.index:
                safe_indices.discard(poll_index)
                poll_exclusions.append({
                    "poll_id": str(poll_frame.at[poll_index, "poll_id"]),
                    "race_id": race_id,
                    "reason": "candidate_specific_poll_before_nomination_excluded",
                })
        elif str(race.get("state") or "") == "CA":
            identity_required = True
            identity_resolved = exact_identity and race_events_traceable
            if not identity_resolved:
                state = "identity_required"
                reason = "top_two_pairing_requires_point_in_time_identity"
                eligible = False
                identity_missing.append(race_id)
            else:
                modeled_party = str(race.get("modeled_ballot_party") or "").upper()
                opposing_party = str(race.get("opposing_ballot_party") or "").upper()
                if modeled_party == opposing_party or {modeled_party, opposing_party} != {"DEM", "REP"}:
                    state = "ineligible_for_binary_scoring"
                    reason = "top_two_pairing_is_not_dem_vs_rep"
                    score_eligible = False
                    score_reason = "non_dem_vs_rep_top_two_pairing"
                else:
                    state = "identity_required"
                    reason = "top_two_pairing_resolved_by_official_timeline"
        elif len(sensitive_events):
            identity_required = True
            identity_resolved = (
                exact_identity and race_events_traceable and not has_unresolved_identity_status
            )
            state = "identity_required"
            reason = "candidate_transition_requires_point_in_time_identity"
            eligible = identity_resolved
            if not identity_resolved:
                identity_missing.append(race_id)
        elif len(matchup_keys) > 1:
            if exact_identity and race_events_traceable:
                identity_required = True
                identity_resolved = True
                state = "identity_required"
                reason = "multiple_matchups_resolved_by_point_in_time_identity"
                for poll_index, poll in candidate_polls.iterrows():
                    if not _poll_matches_resolved_identity(poll, race):
                        safe_indices.discard(poll_index)
                        poll_exclusions.append({
                            "poll_id": str(poll.get("poll_id") or ""),
                            "race_id": race_id,
                            "reason": "matchup_not_selected_by_point_in_time_identity",
                        })
            else:
                # Remove every incompatible candidate-specific row.  With no
                # identity-dependent input left, the party-side race may remain
                # usable; the exclusion itself is fingerprinted and reported.
                state = "structurally_unresolved"
                reason = "ambiguous_candidate_matchups_excluded"
                for poll_index in candidate_polls.index:
                    safe_indices.discard(poll_index)
                    poll_exclusions.append({
                        "poll_id": str(poll_frame.at[poll_index, "poll_id"]),
                        "race_id": race_id,
                        "reason": "ambiguous_candidate_matchup_excluded",
                    })

        # The reviewed current registry selects candidate-specific evidence for
        # every 2026 contest, including unsupported I-v-R and RCV targets. Polls
        # matching those identities remain visible as candidate-compatible even
        # when they cannot enter the binary statistical model.
        if year == 2026:
            for poll_index, poll in candidate_polls.iterrows():
                matches = current_identity_resolved and _poll_matches_resolved_identity(
                    poll, race,
                )
                if matches:
                    candidate_compatible_indices.add(poll_index)
                    candidate_compatible_by_race.setdefault(race_id, []).append(
                        str(poll.get("poll_id") or "")
                    )
                else:
                    safe_indices.discard(poll_index)
                    poll_exclusions.append({
                        "poll_id": str(poll.get("poll_id") or ""),
                        "race_id": race_id,
                        "reason": (
                            "matchup_not_selected_by_reviewed_current_registry"
                            if current_registry_resolved
                            else "matchup_not_selected_by_point_in_time_identity"
                            if point_in_time_resolved
                            else "current_identity_unresolved_candidate_poll_excluded"
                        ),
                    })

        # Non-binary race states never contribute candidate-specific polling to
        # the binary margin fit, but remain explicit in snapshot diagnostics.
        if state == "ineligible_for_binary_scoring":
            for poll_index in candidate_polls.index:
                safe_indices.discard(poll_index)
                if year != 2026 or poll_index in candidate_compatible_indices:
                    poll_exclusions.append({
                        "poll_id": str(poll_frame.at[poll_index, "poll_id"]),
                        "race_id": race_id,
                        "reason": "race_ineligible_for_binary_scoring",
                    })
            score_exclusions.append({"race_id": race_id, "reason": score_reason})

        probability_supported = bool(score_eligible)
        probability_support_status_value = (
            "ordinary_binary_model" if score_eligible else "unsupported"
        )
        probability_support_reason = (
            "ordinary_dem_vs_rep" if score_eligible else score_reason
        )
        if year == 2026:
            from midterms.evidence.non_major_contract import probability_support_status

            probability_supported, probability_support_status_value, probability_support_reason = (
                probability_support_status(
                    race,
                    n_compatible_polls=len(
                        candidate_compatible_by_race.get(race_id, [])
                    ),
                )
            )

        values = {
            "candidate_state": state,
            "candidate_state_reason": reason,
            "candidate_state_eligible": bool(eligible),
            "candidate_identity_required": bool(identity_required),
            "candidate_identity_resolved": bool(identity_resolved),
            "binary_score_eligible": bool(score_eligible),
            "binary_score_exclusion_reason": score_reason,
        }
        if year == 2026:
            values.update({
                "candidate_identity_eligible": bool(eligible),
                "probability_model_supported": bool(probability_supported),
                "probability_model_support_status": probability_support_status_value,
                "probability_model_support_reason": probability_support_reason,
            })
        for column, value in values.items():
            out.at[index, column] = value
        race_evidence = {
            key: race.get(key) for key in (
                "election_id", "race_id", "state", "not_up", "election_phase",
                "runoff_of", "vacancy_reason", "ballot_status",
            )
        }
        if bool(contested.loc[index]):
            record = {
                "race_id": race_id,
                **values,
                "modeled_side": (
                    "modeled"
                    if str(race.get("contest_structure") or "")
                    == "non_major_party_vs_republican"
                    else "D"
                ),
                "opposing_side": (
                    "opposing"
                    if str(race.get("contest_structure") or "")
                    == "non_major_party_vs_republican"
                    else "R"
                ),
                "timeline_status": race.get("candidate_timeline_status"),
                "modeled_candidate_id": (
                    race.get("modeled_candidate_id") if identity_resolved else None
                ),
                "opposing_candidate_id": (
                    race.get("opposing_candidate_id") if identity_resolved else None
                ),
                "matchup_ids_seen": matchup_keys,
                "structural_gap": gap,
                "race_evidence_sha256": _canonical_sha256(race_evidence),
            }
            if year == 2026:
                record.update({
                    "identity_source": race.get("identity_source"),
                    "identity_registry_version": race.get("identity_registry_version"),
                    "identity_registry_sha256": race.get("identity_registry_sha256"),
                    "identity_reviewed_as_of": race.get("identity_reviewed_as_of"),
                    "current_matchup_status": race.get("current_matchup_status"),
                    "contest_structure": race.get("contest_structure"),
                    "statistical_target_supported": race.get(
                        "statistical_target_supported"
                    ),
                })
            records.append(record)

    safe_polls = poll_frame.loc[sorted(safe_indices)].copy() if len(poll_frame) else poll_frame
    counts = {
        state: int(sum(record["candidate_state"] == state for record in records))
        for state in (
            "side_only_stable", "identity_required", "structurally_unresolved",
            "ineligible_for_binary_scoring", "not_applicable_held_seat",
        )
    }
    identity_resolved_count = sum(
        record["candidate_identity_required"] and record["candidate_identity_resolved"]
        for record in records
    )
    identity_missing = sorted(set(identity_missing))
    has_current_2026 = any(
        str(record.get("race_id") or "").startswith("senate-2026-")
        for record in records
    )
    state_schema_version = (
        CURRENT_CANDIDATE_STATE_SCHEMA_VERSION
        if has_current_2026 else CANDIDATE_STATE_SCHEMA_VERSION
    )
    semantic = {
        "schema_version": state_schema_version,
        "as_of": cutoff.isoformat(),
        "candidate_timeline_snapshot_sha256": timeline_meta.get("snapshot_sha256"),
        "classification_records": sorted(records, key=lambda value: value["race_id"]),
        "poll_exclusions": sorted(
            {json.dumps(value, sort_keys=True) for value in poll_exclusions}
        ),
        "ambiguous_poll_matchups": ambiguous_cases,
    }
    if has_current_2026:
        semantic["candidate_compatible_poll_ids_by_race"] = {
            race_id: sorted(set(poll_ids))
            for race_id, poll_ids in sorted(candidate_compatible_by_race.items())
        }
    candidate_state_sha = _canonical_sha256(semantic)
    traceable_required = all(
        record["candidate_identity_resolved"]
        for record in records if record["candidate_identity_required"]
    )
    current_probability_blockers = [
        record["race_id"] for record in records
        if str(record.get("race_id") or "").startswith("senate-2026-")
        and not record["probability_model_supported"]
    ]
    # Candidate-source readiness and statistical model coverage are separate
    # contracts.  A fully sourced Alaska ballot may still be unsupported by the
    # current RCV model, but that does not make its identity evidence unready.
    publication_eligible = not identity_missing and traceable_required
    reasons = []
    if identity_missing:
        reasons.append(
            f"{len(identity_missing)} identity-sensitive races lack point-in-time resolution"
        )
    metadata = {
        **timeline_meta,
        "schema_version": state_schema_version,
        "status": "ready" if publication_eligible else "identity_sensitive_unresolved",
        "production_eligible": publication_eligible,
        "publication_eligible": publication_eligible,
        "traceable": bool(traceable_required),
        "candidate_timeline_snapshot_sha256": timeline_meta.get("snapshot_sha256"),
        "snapshot_sha256": candidate_state_sha,
        "candidate_state_snapshot_sha256": candidate_state_sha,
        "classification_records": sorted(records, key=lambda value: value["race_id"]),
        "counts": {
            **counts,
            "identity_required_and_resolved": int(identity_resolved_count),
            "identity_required_and_missing": len(identity_missing),
            "excluded_from_binary_scoring": len(score_exclusions),
            "held_seats_not_applicable": int((~contested).sum()),
        },
        "identity_required_and_missing_race_ids": identity_missing,
        "ambiguous_poll_matchups": ambiguous_cases,
        "poll_exclusions": sorted(
            [dict(value) for value in {tuple(sorted(item.items())) for item in poll_exclusions}],
            key=lambda value: (value.get("race_id", ""), value.get("poll_id", ""), value.get("reason", "")),
        ),
        "score_exclusions": sorted(score_exclusions, key=lambda value: value["race_id"]),
        "n_candidate_polls_excluded": len({
            (item.get("poll_id"), item.get("race_id"), item.get("reason"))
            for item in poll_exclusions
        }),
        "n_hypothetical_polls_excluded": len(future_or_hypothetical),
        "reasons": reasons,
        "held_seats_excluded": True,
        "conditional_identity_contract": True,
    }
    # Reviewed current-registry identities are not timeline events. When they
    # resolve required races, their review boundary is the operational freshness
    # clock so publication eligibility does not demand a missing retrieved_at.
    registry_review_dates = [
        date.fromisoformat(str(record["identity_reviewed_as_of"]))
        for record in records
        if record.get("candidate_identity_required")
        and record.get("candidate_identity_resolved")
        and str(record.get("identity_source") or "")
        == "reviewed_current_candidate_registry"
        and record.get("identity_reviewed_as_of")
    ]
    if registry_review_dates and metadata.get("latest_retrieved_at") is None:
        stamp = max(registry_review_dates).isoformat()
        metadata["latest_retrieved_at"] = stamp
        metadata["latest_effective_at"] = (
            metadata.get("latest_effective_at") or stamp
        )
        metadata["identity_freshness_basis"] = "reviewed_current_candidate_registry"
    if has_current_2026:
        metadata["forecast_model_coverage"] = {
            "complete": not current_probability_blockers,
            "unsupported_race_ids": sorted(current_probability_blockers),
            "separate_from_candidate_source_readiness": True,
        }
        metadata["candidate_compatible_poll_ids_by_race"] = {
            race_id: sorted(set(poll_ids))
            for race_id, poll_ids in sorted(candidate_compatible_by_race.items())
        }
        metadata["current_probability_target_blocked_race_ids"] = sorted(
            current_probability_blockers
        )
    return out, safe_polls.reset_index(drop=True), metadata


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
        "n_events": len(normalized),
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


def parse_fec_form2_candidate_filings(
    input_path: str | Path, *, election_year: int,
) -> pd.DataFrame:
    """Normalize official declarations while preserving filer != nominee.

    Form 2 proves that a person filed with the FEC. It does not prove party
    nomination, ballot qualification, or general-election appearance, and is
    therefore never emitted as a ballot-identity event.
    """
    path = Path(input_path)
    frame = pd.read_csv(path)
    required = {
        "CANDIDATE_ID", "CANDIDATE_NAME", "PARTY_CODE", "CANDIDATE_OFFICE_CODE",
        "CANDIDATE_OFFICE_STATE_CODE", "ELECTION_YEAR", "RECEIPT_DATE",
        "BEGIN_IMAGE_NUMBER",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"FEC Form 2 source missing columns: {missing}")
    out = frame[
        frame["CANDIDATE_OFFICE_CODE"].astype(str).eq("S")
        & pd.to_numeric(frame["ELECTION_YEAR"], errors="coerce").eq(int(election_year))
    ].copy()
    out["available_at"] = pd.to_datetime(
        out["RECEIPT_DATE"], format="%d-%b-%y", errors="coerce"
    ).dt.date
    out = out[out["available_at"].notna()].copy()
    out["event_type"] = "fec_candidate_filing"
    out["establishes_nomination"] = False
    out["establishes_ballot_qualification"] = False
    out["source_object_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    out["source_url"] = "https://www.fec.gov/data/browse-data/?tab=candidates"
    out["parser_version"] = "fec-form2-filing-v1"
    return out.sort_values(
        ["available_at", "CANDIDATE_OFFICE_STATE_CODE", "CANDIDATE_ID"], kind="stable"
    ).reset_index(drop=True)


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
        "candidate_name": "{side}_candidate_name",
        "ballot_party": "{side}_ballot_party",
        "caucus_affiliation": "{side}_caucus",
        "caucus_basis": "{side}_caucus_basis",
    }
    for column in (
        "modeled_candidate_id", "modeled_candidate_name", "modeled_ballot_party", "modeled_caucus",
        "modeled_caucus_basis", "opposing_candidate_id", "opposing_ballot_party",
        "opposing_candidate_name", "opposing_caucus", "opposing_caucus_basis",
        "candidate_timeline_status",
    ):
        if column not in out.columns:
            out[column] = None
    # For races governed by a timeline, do not retain a current/future identity
    # from the base race table. Reconstruct the snapshot only from knowable events.
    governed = set(events["race_id"].astype(str))
    governed_mask = out["race_id"].astype(str).isin(governed)
    for column in (
        "modeled_candidate_id", "modeled_candidate_name", "modeled_ballot_party", "modeled_caucus",
        "modeled_caucus_basis", "opposing_candidate_id", "opposing_ballot_party",
        "opposing_candidate_name", "opposing_caucus", "opposing_caucus_basis",
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
        event_type = str(event["event_type"]).lower()
        if event_type in {"withdrawal", "withdrawn"}:
            out.at[idx, "ballot_status"] = "withdrawn"
        elif event_type in {"nomination", "nominated"} and (
            pd.isna(event["ballot_status"]) or str(event["ballot_status"]) == ""
        ):
            out.at[idx, "ballot_status"] = "nominated"
        elif event_type in {"ballot_qualification", "qualified", "qualification"} and (
            pd.isna(event["ballot_status"]) or str(event["ballot_status"]) == ""
        ):
            out.at[idx, "ballot_status"] = "qualified"
        if event_type in BALLOT_IDENTITY_EVENT_TYPES:
            out.at[idx, "candidate_timeline_status"] = "point_in_time"
        elif out.at[idx, "candidate_timeline_status"] != "point_in_time":
            out.at[idx, "candidate_timeline_status"] = "degraded_filer_or_nonballot_event"
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
        "n_events_applied": len(usable),
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
    if "candidate_state" in races.columns or meta.get("conditional_identity_contract"):
        required = (
            races[~races["not_up"].map(lambda value: bool(value) if pd.notna(value) else False)]
            if "not_up" in races.columns else races
        )
        counts = dict(meta.get("counts") or {})
        missing = sorted(meta.get("identity_required_and_missing_race_ids") or [])
        state_eligible = required.get(
            "candidate_state_eligible", pd.Series(False, index=required.index),
        )
        identity_eligible = required.get(
            "candidate_identity_eligible", state_eligible,
        ).where(lambda values: values.notna(), state_eligible)
        row_ineligible = ~identity_eligible.map(
            lambda value: bool(value) if pd.notna(value) else False
        )
        missing = sorted(set(missing) | set(
            required.loc[row_ineligible, "race_id"].astype(str)
        ))
        snapshot_hash = meta.get("candidate_state_snapshot_sha256") or meta.get("snapshot_sha256")
        eligible = not missing and bool(snapshot_hash)
        reasons = list(meta.get("reasons") or [])
        if missing:
            reasons.append(
                f"{len(missing)} identity-sensitive races lack point-in-time resolution"
            )
        if not snapshot_hash:
            reasons.append("candidate-state snapshot hash is missing")
        return {
            "status": "ready" if eligible else "identity_sensitive_unresolved",
            "n_required_races": len(required),
            "n_point_in_time": int(counts.get("identity_required_and_resolved", 0)),
            "n_degraded": int(counts.get("identity_required_and_missing", 0)),
            "n_incomplete_identity": len(missing),
            "n_side_only_stable": int(counts.get("side_only_stable", 0)),
            "n_structurally_unresolved": int(counts.get("structurally_unresolved", 0)),
            "n_identity_required_and_resolved": int(
                counts.get("identity_required_and_resolved", 0)
            ),
            "n_identity_required_and_missing": len(missing),
            "n_excluded_from_binary_scoring": int(
                counts.get("excluded_from_binary_scoring", 0)
            ),
            "required_identity_columns": list(REQUIRED_CONTESTED_IDENTITY_COLUMNS),
            "identity_required_and_missing_race_ids": missing,
            "classification_records": meta.get("classification_records") or [],
            "ambiguous_poll_matchups": meta.get("ambiguous_poll_matchups") or [],
            "poll_exclusions": meta.get("poll_exclusions") or [],
            "score_exclusions": meta.get("score_exclusions") or [],
            "traceable": bool(meta.get("traceable")),
            "snapshot_sha256": snapshot_hash,
            "candidate_timeline_snapshot_sha256": meta.get(
                "candidate_timeline_snapshot_sha256"
            ),
            "reasons": sorted(set(reasons)),
            "eligible": eligible,
            "publication_eligible": eligible,
            "held_seats_excluded": True,
            "conditional_identity_contract": True,
            "schema_version": CANDIDATE_STATE_SCHEMA_VERSION,
        }
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
    n_required = len(required)
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
    polls: pd.DataFrame | None = None,
    source_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit conditional point-in-time candidate state at historical cutoffs."""
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
        cutoff_polls = pd.DataFrame() if polls is None else polls[
            polls.get("election_id", pd.Series("", index=polls.index)).astype(str).eq(
                str(election_id)
            )
        ].copy()
        gaps = candidate_structural_gaps_for_cutoff(
            source_audit, election_id=str(election_id), as_of=cutoff,
        )
        applied, _safe_polls, metadata = apply_candidate_state_contract(
            subset,
            timeline,
            cutoff_polls,
            as_of=cutoff,
            structural_gaps=gaps,
        )
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
