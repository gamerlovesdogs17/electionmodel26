"""Canonical truth contract (v0.9.21 / data-drop audit §9).

One schema for race outcomes consumed by warehouse, chamber reconcile, poll
identity, scoring, and gates. Wikipedia ``certified_vote_counts.json`` is
explicitly outside this contract (parser-development quarantine).
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "truth_v1"

# Required keys on each contest in the official ledger.
CONTEST_REQUIRED = frozenset(
    {
        "race_id",
        "state",
        "seat_class",
        "kind",
        "term_type",
        "stage",
        "dem_votes",
        "rep_votes",
        "other_votes",
        "two_party_margin",
        "winner_party",
        "winner_caucus",
        "modeled_side",
        "certification_status",
        "source_object_hash",
        "available_at",
    }
)

# Canonical expectation cycle keys (single producer/consumer vocabulary).
EXPECTATION_REQUIRED = frozenset(
    {
        "held_dem",
        "held_rep",
        "held_ind",
        "n_contested_expected",
        "expected_race_ids",
        "post_dem_seats",
        "post_dem_control",
        "vp_tiebreak_party",
    }
)

# Independents who win on the ballot as I but count toward Dem caucus.
INDEPENDENT_CAUCUS_D = frozenset(
    {
        "angus king",
        "angus s king",
        "angus s king jr",
        "bernard sanders",
        "bernie sanders",
    }
)

# Quarantined Wikipedia scrape — never a canonical truth source.
WIKI_VOTE_COUNTS_PATH = "data/raw/external/certified_vote_counts.json"
WIKI_QUARANTINE_LABEL = "parser_development_only"


def normalize_expectation_cycle(raw: dict[str, Any]) -> dict[str, Any]:
    """Map legacy aliases onto the canonical expectation keys."""
    out = dict(raw)
    if "expected_race_ids" not in out and "contested_race_ids" in out:
        out["expected_race_ids"] = list(out["contested_race_ids"])
    if "post_dem_seats" not in out and "post_election_dem_seats" in out:
        out["post_dem_seats"] = out["post_election_dem_seats"]
    if "post_dem_control" not in out and "post_election_dem_control" in out:
        out["post_dem_control"] = out["post_election_dem_control"]
    return out


def validate_expectation_cycle(cycle: dict[str, Any]) -> list[str]:
    missing = sorted(k for k in EXPECTATION_REQUIRED if k not in cycle or cycle[k] is None)
    return [f"missing expectation field: {k}" for k in missing]


def validate_contest(contest: dict[str, Any]) -> list[str]:
    missing = sorted(k for k in CONTEST_REQUIRED if k not in contest)
    return [f"missing contest field: {k}" for k in missing]


def _norm_person_name(name: str | None) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def caucus_for_winner(
    *,
    winner_party: str,
    winner_name: str | None = None,
    ballot_party: str | None = None,
) -> str:
    """Map ballot winner to chamber caucus without inventing D ballot party for I."""
    wp = str(winner_party or "").upper()
    if wp in {"D", "DEM", "DEMOCRATIC"}:
        return "D"
    if wp in {"R", "REP", "REPUBLICAN", "GOP"}:
        return "R"
    name = _norm_person_name(winner_name)
    if ("angus" in name and "king" in name) or ("sanders" in name and ("bernie" in name or "bernard" in name)):
        return "D"
    for known in INDEPENDENT_CAUCUS_D:
        if name == known or name.startswith(known + " "):
            return "D"
    return wp if wp in {"D", "R"} else "I"


def classify_row_role(name: object, votes: object = None) -> str:
    """Candidate vs meta row (turnout / registered / totals)."""
    text = str(name or "").strip().lower()
    if not text or text in {"nan", "none"}:
        return "meta"
    meta_tokens = (
        "registered",
        "turnout",
        "total votes",
        "total vote",
        "ballots cast",
        "electors",
        "voter registration",
        "over votes",
        "under votes",
        "blank",
        "spoiled",
    )
    if any(t in text for t in meta_tokens):
        return "meta"
    if text in {"total", "totals", "other", "write-in", "write in", "scattering"}:
        if text.startswith("write"):
            return "write_in"
        if text in {"other", "scattering"}:
            return "other_aggregate"
        return "meta"
    return "candidate"


def is_quarantined_wiki_source(path_or_label: str) -> bool:
    p = str(path_or_label).replace("\\", "/")
    return p.endswith("certified_vote_counts.json") or "certified_vote_counts" in p
