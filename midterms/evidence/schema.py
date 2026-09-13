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
]

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
    row.update(overrides)
    return row
