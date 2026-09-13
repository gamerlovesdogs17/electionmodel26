"""Canonical evidence schemas (blueprint Appendix A.1 / A.4)."""

from __future__ import annotations

from typing import Any

POLL_COLUMNS = [
    "poll_id",
    "study_id",
    "release_version",
    "pollster_id",
    "sponsor_id",
    "source_url",
    "raw_hash",
    "field_start",
    "field_end",
    "published_at",
    "corrected_at",
    "retrieved_at",
    "valid_from",
    "valid_to",
    "available_at",
    "event_time",
    "election_id",
    "office",
    "state",
    "race_id",
    "population",
    "sample_size",
    "mode",
    "dem_share",
    "rep_share",
    "undecided",
    "other_share",
    "two_party_margin",  # dem - rep on two-party basis, percentage points
    "partisan",
    "exclusion_status",
    "exclusion_reason",
    "parser_version",
    "normalized_at",
    "supersedes",
    # Optional blueprint A.1 extensions (nullable; defaults filled on ingest)
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
    # Audit P0.3 — candidate / matchup identity
    "dem_candidate_id",
    "dem_candidate_name",
    "rep_candidate_id",
    "rep_candidate_name",
    "matchup_id",
    "hypothetical",
    "contest_kind",  # regular | special
    "seat_name",
    "election_stage",  # general | runoff | ...
]

RESULT_COLUMNS = [
    "result_id",
    "election_id",
    "office",
    "state",
    "race_id",
    "event_time",
    "available_at",
    "certified_at",
    "dem_votes",
    "rep_votes",
    "other_votes",
    "two_party_margin",
    "winner_party",
    "source_url",
    "raw_hash",
    "retrieved_at",
    "release_version",
]

RACE_COLUMNS = [
    "race_id",
    "election_id",
    "office",
    "state",
    "seat_class",
    "election_day",
    "incumbent_party",
    "is_open",
    "prior_lean",  # dem - rep two-party margin last comparable election
    "region",
    "not_up",  # seats not contested this cycle but held for chamber totals
    "held_by",  # for chamber composition: D/R/I
    "fundraising_share",  # Dem share of matched-window receipts in [0, 1]
    "pres_approval",  # presidential net approval (cycle-level, positive = popular)
    "white_house_party",  # 'D' | 'R'
    "is_midterm",
    # Institutional / calendar extensions (blueprint race schema)
    "election_phase",  # general | runoff | runoff_pending | special
    "runoff_of",  # parent race_id when this row is a runoff
    "vacancy_reason",  # appointment | resignation | death | None
    "ballot_status",  # nominated | withdrawn | deceased | write_in
    "effective_election_day",  # runoff day when phase advances; else election_day
]


def empty_race_row(**overrides: Any) -> dict[str, Any]:
    row = {c: None for c in RACE_COLUMNS}
    row.update(
        {
            "office": "US_SENATE",
            "not_up": False,
            "is_open": False,
            "is_midterm": False,
            "election_phase": "general",
            "ballot_status": "nominated",
        }
    )
    row.update(overrides)
    return row


def is_active_ballot_row(row: dict[str, Any] | Any) -> bool:
    """True if the race should enter the joint forecast / chamber sim."""
    get = row.get if isinstance(row, dict) else lambda k, d=None: row[k] if k in row.index else d
    if bool(get("not_up", False)):
        return False
    status = str(get("ballot_status") or "nominated").lower()
    if status in {"withdrawn", "deceased"}:
        return False
    phase = str(get("election_phase") or "general").lower()
    if phase in {"runoff_pending"}:
        return False
    return True

MANIFEST_FIELDS = [
    "run_id",
    "generated_at",
    "forecast_as_of",
    "election_id",
    "model_version",
    "code_commit",
    "configuration_hash",
    "snapshot_ids",
    "seed",
    "draws",
    "tune",
    "chains",
    "output_hashes",
]


def empty_poll_row(**overrides: Any) -> dict[str, Any]:
    row = {c: None for c in POLL_COLUMNS}
    row.update(
        {
            "office": "US_SENATE",
            "exclusion_status": "include",
            "release_version": 1,
            "geography_version_id": "state-usps-v1",
            "candidate_set_version": "ticket-v1",
            "question_id": "generic_two_way",
            "language": "en",
            "design_effect": 1.0,
            "leaners_included": True,
            "multiway": False,
        }
    )
    row.update(overrides)
    return row


def align_poll_frame(df: "Any") -> "Any":
    """Ensure DataFrame has all POLL_COLUMNS (fill missing with None)."""
    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    out = df.copy()
    for c in POLL_COLUMNS:
        if c not in out.columns:
            out[c] = None
    return out[POLL_COLUMNS]
