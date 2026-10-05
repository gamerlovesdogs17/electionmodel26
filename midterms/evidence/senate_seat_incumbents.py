"""Seat-specific Senate officeholder identity (not state-level senator lookup).

Nearly every state has two sitting senators. Personal incumbency must bind to
the exact seat being elected (class / special / vacancy), never to ``state``
alone.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from midterms.evidence.fec import fec_name_matches

# Seat identity: US_SENATE:{STATE}:{CLASS|SPECIAL}
# 2026 Class II / special officeholders for the contested seats only.
# Open seats (retiring / not seeking reelection) omit an officeholder or set
# officeholder_name=None — personal incumbency then fails closed to 0.
SEAT_OFFICEHOLDERS_2026: dict[str, dict[str, Any]] = {
    # Regular Class II
    "US_SENATE:AK:II": {"officeholder_name": "Dan Sullivan", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:AL:II": {"officeholder_name": "Tommy Tuberville", "party": "R", "status": "open_not_seeking"},
    "US_SENATE:AR:II": {"officeholder_name": "Tom Cotton", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:CO:II": {"officeholder_name": "John Hickenlooper", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:DE:II": {"officeholder_name": "Chris Coons", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:GA:II": {"officeholder_name": "Jon Ossoff", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:ID:II": {"officeholder_name": "Jim Risch", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:IL:II": {"officeholder_name": "Dick Durbin", "party": "D", "status": "open_not_seeking"},
    "US_SENATE:IA:II": {"officeholder_name": "Joni Ernst", "party": "R", "status": "open_not_seeking"},
    "US_SENATE:KS:II": {"officeholder_name": "Roger Marshall", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:KY:II": {"officeholder_name": "Mitch McConnell", "party": "R", "status": "open_not_seeking"},
    "US_SENATE:LA:II": {"officeholder_name": "Bill Cassidy", "party": "R", "status": "open_not_seeking"},
    "US_SENATE:ME:II": {"officeholder_name": "Susan Collins", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:MA:II": {"officeholder_name": "Ed Markey", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:MI:II": {"officeholder_name": "Gary Peters", "party": "D", "status": "open_not_seeking"},
    "US_SENATE:MN:II": {"officeholder_name": "Tina Smith", "party": "D", "status": "open_not_seeking"},
    "US_SENATE:MS:II": {"officeholder_name": "Cindy Hyde-Smith", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:MT:II": {"officeholder_name": "Steve Daines", "party": "R", "status": "open_not_seeking"},
    # Class II NE is Ricketts (appointed after Sasse; not Fischer Class I).
    "US_SENATE:NE:II": {"officeholder_name": "Pete Ricketts", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:NH:II": {"officeholder_name": "Jeanne Shaheen", "party": "D", "status": "open_not_seeking"},
    "US_SENATE:NJ:II": {"officeholder_name": "Cory Booker", "party": "D", "status": "seeking_reelection"},
    # Class II NM is Luján (not Heinrich Class I).
    "US_SENATE:NM:II": {"officeholder_name": "Ben Ray Luján", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:NC:II": {"officeholder_name": "Thom Tillis", "party": "R", "status": "open_not_seeking"},
    # Class II OK is Mullin (Inhofe special successor); Lankford is Class III.
    "US_SENATE:OK:II": {"officeholder_name": "Markwayne Mullin", "party": "R", "status": "open_not_seeking"},
    "US_SENATE:OR:II": {"officeholder_name": "Jeff Merkley", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:RI:II": {"officeholder_name": "Jack Reed", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:SC:II": {"officeholder_name": "Lindsey Graham", "party": "R", "status": "open_not_seeking"},
    "US_SENATE:SD:II": {"officeholder_name": "Mike Rounds", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:TN:II": {"officeholder_name": "Bill Hagerty", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:TX:II": {"officeholder_name": "John Cornyn", "party": "R", "status": "open_not_seeking"},
    "US_SENATE:VA:II": {"officeholder_name": "Mark Warner", "party": "D", "status": "seeking_reelection"},
    "US_SENATE:WV:II": {"officeholder_name": "Shelley Moore Capito", "party": "R", "status": "seeking_reelection"},
    "US_SENATE:WY:II": {"officeholder_name": "Cynthia Lummis", "party": "R", "status": "open_not_seeking"},
    # 2026 specials (appointed officeholders seeking election)
    "US_SENATE:FL:SPECIAL": {"officeholder_name": "Ashley Moody", "party": "R", "status": "seeking_election"},
    "US_SENATE:OH:SPECIAL": {"officeholder_name": "Jon Husted", "party": "R", "status": "seeking_election"},
}


def seat_identity_from_race(row: dict[str, Any] | pd.Series) -> str | None:
    """Return ``US_SENATE:{ST}:{CLASS|SPECIAL}`` for a race row."""
    state = str(row.get("state") or "").upper()
    if not state or len(state) != 2:
        return None
    seat_class = str(row.get("seat_class") or "").upper()
    phase = str(row.get("election_phase") or "").lower()
    vacancy = row.get("vacancy_reason")
    race_id = str(row.get("race_id") or "")
    if (
        seat_class == "SPECIAL"
        or phase == "special"
        or (vacancy is not None and not (isinstance(vacancy, float) and pd.isna(vacancy)) and str(vacancy).strip())
        or race_id.endswith("-special")
        or "-unexpired" in race_id
    ):
        return f"US_SENATE:{state}:SPECIAL"
    if seat_class in {"I", "II", "III"}:
        return f"US_SENATE:{state}:{seat_class}"
    # 2026 contested fixtures: Class II unless special.
    if str(row.get("election_id") or "").endswith("2026"):
        return f"US_SENATE:{state}:II"
    return None


def resolve_2026_personal_incumbency(
    *,
    race_id: str,
    state: str,
    seat_class: str | None,
    election_phase: str | None,
    vacancy_reason: str | None,
    modeled_candidate_name: str | None,
    opposing_candidate_name: str | None,
) -> dict[str, Any]:
    """Derive personal-incumbency flags from seat-specific officeholder identity."""
    seat_id = seat_identity_from_race(
        {
            "race_id": race_id,
            "state": state,
            "seat_class": seat_class,
            "election_phase": election_phase,
            "vacancy_reason": vacancy_reason,
            "election_id": "senate-2026",
        }
    )
    office = SEAT_OFFICEHOLDERS_2026.get(seat_id or "") if seat_id else None
    if seat_id is None or office is None:
        return {
            "seat_identity": seat_id,
            "sitting_senator_name": None,
            "sitting_senator_status": "unresolved",
            "modeled_candidate_is_incumbent": False,
            "opposing_candidate_is_incumbent": False,
            "personal_incumbency": 0.0,
            "identity_status": "seat_identity_unresolved",
            "provenance": "seat_officeholders_2026_v1",
        }
    name = office.get("officeholder_name")
    status = str(office.get("status") or "")
    seeking = status in {"seeking_reelection", "seeking_election"}
    modeled_inc = bool(seeking and name and fec_name_matches(name, modeled_candidate_name))
    opposing_inc = bool(seeking and name and fec_name_matches(name, opposing_candidate_name))
    if modeled_inc and opposing_inc:
        modeled_inc = opposing_inc = False
        identity_status = "ill_formed_dual_incumbent"
        personal = 0.0
    elif modeled_inc:
        identity_status = "modeled_is_seat_incumbent"
        personal = 1.0
    elif opposing_inc:
        identity_status = "opposing_is_seat_incumbent"
        personal = -1.0
    elif not seeking:
        identity_status = "open_seat_officeholder_not_nominee"
        personal = 0.0
    else:
        identity_status = "seat_incumbent_not_on_ticket"
        personal = 0.0
    return {
        "seat_identity": seat_id,
        "sitting_senator_name": name,
        "sitting_senator_status": status,
        "modeled_candidate_is_incumbent": modeled_inc,
        "opposing_candidate_is_incumbent": opposing_inc,
        "personal_incumbency": personal,
        "identity_status": identity_status,
        "provenance": "seat_officeholders_2026_v1",
    }


def apply_seat_incumbency_to_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    """Update one current-registry race with seat-specific incumbency fields."""
    resolved = resolve_2026_personal_incumbency(
        race_id=str(row["race_id"]),
        state=str(row["state"]),
        seat_class="SPECIAL"
        if str(row.get("contest_structure") or "") == "ranked_choice_multiway"
        and str(row["state"]) in {"FL", "OH"}
        else ("II" if str(row["state"]) not in {"FL", "OH"} else "SPECIAL"),
        election_phase="special" if str(row["state"]) in {"FL", "OH"} else "general",
        vacancy_reason="appointment" if str(row["state"]) in {"FL", "OH"} else None,
        modeled_candidate_name=row.get("modeled_candidate_name"),
        opposing_candidate_name=row.get("opposing_candidate_name"),
    )
    # FL/OH are specials in fixtures regardless of contest_structure.
    if str(row["state"]) in {"FL", "OH"}:
        resolved = resolve_2026_personal_incumbency(
            race_id=str(row["race_id"]),
            state=str(row["state"]),
            seat_class="SPECIAL",
            election_phase="special",
            vacancy_reason="appointment",
            modeled_candidate_name=row.get("modeled_candidate_name"),
            opposing_candidate_name=row.get("opposing_candidate_name"),
        )
    row = dict(row)
    row["seat_identity"] = resolved["seat_identity"]
    row["sitting_senator_name"] = resolved["sitting_senator_name"]
    row["modeled_candidate_is_incumbent"] = resolved["modeled_candidate_is_incumbent"]
    row["opposing_candidate_is_incumbent"] = resolved["opposing_candidate_is_incumbent"]
    row["personal_incumbency_identity_status"] = resolved["identity_status"]
    return row
