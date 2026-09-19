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
        "election_day",
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


def _parse_iso_date(value: object) -> "date | None":
    from datetime import date, datetime

    if value is None or value == "":
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def validate_contest(contest: dict[str, Any]) -> list[str]:
    """Presence + semantic checks (v0.9.21 audit P0/P2)."""
    errs = [f"missing contest field: {k}" for k in sorted(CONTEST_REQUIRED - set(contest))]
    dem = contest.get("dem_votes")
    rep = contest.get("rep_votes")
    other = contest.get("other_votes")
    for label, val in (("dem_votes", dem), ("rep_votes", rep), ("other_votes", other)):
        try:
            if float(val) < 0:
                errs.append(f"{label} must be non-negative")
        except (TypeError, ValueError):
            errs.append(f"{label} must be numeric")
    wp = str(contest.get("winner_party") or "")
    wc = str(contest.get("winner_caucus") or "")
    if wp and wp not in {"D", "R", "I", "T"}:
        errs.append(f"winner_party unexpected: {wp}")
    if wc and wc not in {"D", "R", "I"}:
        errs.append(f"winner_caucus unexpected: {wc}")
    event = _parse_iso_date(contest.get("election_day"))
    available = _parse_iso_date(contest.get("available_at"))
    certified = _parse_iso_date(contest.get("certified_at"))
    if event is None:
        errs.append("election_day missing or invalid")
    if available is None:
        errs.append("available_at missing or invalid")
    if event and available and available < event:
        errs.append(
            f"available_at {available} precedes election_day {event} (pre-event leakage)"
        )
    if certified is not None and available is not None and certified < available:
        errs.append(f"certified_at {certified} precedes available_at {available}")
    status = str(contest.get("certification_status") or "")
    if status == "certified" and certified is None:
        errs.append("certification_status=certified requires certified_at evidence")
    tier = str(contest.get("truth_tier") or "")
    if tier.startswith("fec_canvass") or tier.startswith("state_canvass"):
        if not contest.get("source_url"):
            errs.append(f"truth_tier={tier} requires source_url")
        # Reject bare fec_canvass/state_canvass claims that still point at the FTE CSV hash
        # without an override transcription marker (audit P1 provenance).
        if tier in {"fec_canvass", "state_canvass"}:
            errs.append(
                f"truth_tier={tier} overstates provenance; use *_transcribed "
                "with discovery_source_hash or archive a primary object hash"
            )
        disc = contest.get("discovery_source_hash")
        src_hash = contest.get("source_object_hash")
        if disc and src_hash and disc == src_hash and "transcribed" in tier:
            errs.append(
                "override source_object_hash must differ from discovery FTE hash"
            )
        if "transcribed" in tier and not contest.get("override_note") and not disc:
            errs.append(f"truth_tier={tier} requires override_note or discovery_source_hash")
    # Margin semantics
    if "score_eligible" in contest:
        if contest.get("score_eligible") is True:
            if contest.get("two_party_margin") is None and contest.get("margin_value") is None:
                errs.append("score_eligible=True requires margin_value or two_party_margin")
            if contest.get("margin_definition") not in {None, "dem_minus_rep"}:
                # Eligible D−R models only score dem_minus_rep
                if contest.get("margin_definition") != "dem_minus_rep":
                    errs.append("score_eligible=True requires margin_definition=dem_minus_rep")
        if contest.get("score_eligible") is False and contest.get("two_party_margin") == -100.0:
            errs.append("ineligible independent contest must not carry -100 two_party_margin")
    return errs


def validate_ledger_contests(ledger: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    for year, block in (ledger.get("cycles") or {}).items():
        for contest in block.get("contests") or []:
            for msg in validate_contest(contest):
                errs.append(f"{year}/{contest.get('race_id')}: {msg}")
    return errs


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
