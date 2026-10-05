"""Historical seat-specific personal incumbency for formal validation folds.

Officeholder identity is seat/race specific. Nomination matching at a cutoff
obeys point-in-time structural gaps from the sealed candidate-source audit:
late primaries / undetermined special finalists fail closed to zero personal
incumbency (no election-day hindsight for those races).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.evidence.fec import REQUIRED_FINANCE_CUTOFFS, fec_name_matches, historical_nominee_tickets
from midterms.evidence.senate_seat_incumbents import seat_identity_from_race
from midterms.model.personal_incumbency import personal_incumbency_signed

HISTORICAL_SEAT_OFFICEHOLDERS: dict[str, dict[str, Any]] = {
    "senate-2018-CA": {"officeholder_name": 'Dianne Feinstein', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-CT": {"officeholder_name": 'Christopher S. Murphy', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-DE": {"officeholder_name": 'Thomas R. Carper', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-FL": {"officeholder_name": 'Bill Nelson', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-HI": {"officeholder_name": 'Mazie K. Hirono', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-IN": {"officeholder_name": 'Joe Donnelly', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-MA": {"officeholder_name": 'Elizabeth Ann Warren', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-MD": {"officeholder_name": 'Benjamin L. Cardin', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-ME": {"officeholder_name": 'Angus King', "party": 'I', "status": 'seeking_reelection'},
    "senate-2018-MI": {"officeholder_name": 'Debbie Stabenow', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-MN": {"officeholder_name": 'Amy Klobuchar', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-MN-special": {"officeholder_name": 'Tina Smith', "party": 'D', "status": 'seeking_election'},
    "senate-2018-MO": {"officeholder_name": 'Claire McCaskill', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-MS": {"officeholder_name": 'Roger F. Wicker', "party": 'R', "status": 'seeking_reelection'},
    "senate-2018-MS-special": {"officeholder_name": 'Cindy Hyde-Smith', "party": 'R', "status": 'seeking_election'},
    "senate-2018-MT": {"officeholder_name": 'Jon Tester', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-ND": {"officeholder_name": 'Heidi Heitkamp', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-NE": {"officeholder_name": 'Deb Fischer', "party": 'R', "status": 'seeking_reelection'},
    "senate-2018-NJ": {"officeholder_name": 'Bob Menendez', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-NM": {"officeholder_name": 'Martin Heinrich', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-NV": {"officeholder_name": 'Dean Heller', "party": 'R', "status": 'seeking_reelection'},
    "senate-2018-NY": {"officeholder_name": 'Kirsten E. Gillibrand', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-OH": {"officeholder_name": 'Sherrod Brown', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-PA": {"officeholder_name": 'Robert P. Casey Jr.', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-RI": {"officeholder_name": 'Sheldon Whitehouse', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-TX": {"officeholder_name": 'Ted Cruz', "party": 'R', "status": 'seeking_reelection'},
    "senate-2018-VA": {"officeholder_name": 'Timothy Michael Kaine', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-VT": {"officeholder_name": 'Bernie Sanders', "party": 'I', "status": 'seeking_reelection'},
    "senate-2018-WA": {"officeholder_name": 'Maria Cantwell', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-WI": {"officeholder_name": 'Tammy Baldwin', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-WV": {"officeholder_name": 'Joe Manchin, III', "party": 'D', "status": 'seeking_reelection'},
    "senate-2018-WY": {"officeholder_name": 'John Barrasso', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-AK": {"officeholder_name": 'Dan Sullivan', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-AL": {"officeholder_name": 'Gordon Douglas Jones', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-AR": {"officeholder_name": 'Tom Cotton', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-AZ-special": {"officeholder_name": 'Martha McSally', "party": 'R', "status": 'seeking_election'},
    "senate-2020-CO": {"officeholder_name": 'Cory Gardner', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-DE": {"officeholder_name": 'Christopher A. Coons', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-GA": {"officeholder_name": 'David A. Perdue', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-GA-special": {"officeholder_name": 'Kelly Loeffler', "party": 'R', "status": 'seeking_election'},
    "senate-2020-IA": {"officeholder_name": 'Joni K. Ernst', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-ID": {"officeholder_name": 'James E. Risch', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-IL": {"officeholder_name": 'Richard J. Durbin', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-KY": {"officeholder_name": 'Mitch McConnell', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-LA": {"officeholder_name": 'Bill Cassidy', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-MA": {"officeholder_name": 'Edward J. Markey', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-ME": {"officeholder_name": 'Susan M. Collins', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-MI": {"officeholder_name": 'Gary C. Peters', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-MN": {"officeholder_name": 'Tina Smith', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-MS": {"officeholder_name": 'Cindy Hyde-Smith', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-MT": {"officeholder_name": 'Steve Daines', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-NC": {"officeholder_name": 'Thomas Roland Tillis', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-NE": {"officeholder_name": 'Ben Sasse', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-NH": {"officeholder_name": 'Jeanne Shaheen', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-NJ": {"officeholder_name": 'Cory A. Booker', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-OK": {"officeholder_name": 'James M. Inhofe', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-OR": {"officeholder_name": 'Jeff Merkley', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-RI": {"officeholder_name": 'Jack Reed', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-SC": {"officeholder_name": 'Lindsey Graham', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-SD": {"officeholder_name": 'M. Michael Rounds', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-TX": {"officeholder_name": 'John Cornyn', "party": 'R', "status": 'seeking_reelection'},
    "senate-2020-VA": {"officeholder_name": 'Mark R. Warner', "party": 'D', "status": 'seeking_reelection'},
    "senate-2020-WV": {"officeholder_name": 'Shelley Moore Capito', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-AK": {"officeholder_name": 'Lisa Murkowski', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-AR": {"officeholder_name": 'John Boozman', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-AZ": {"officeholder_name": 'Mark Kelly', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-CA": {"officeholder_name": 'Alex Padilla', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-CA-unexpired": {"officeholder_name": 'Alex Padilla', "party": 'D', "status": 'seeking_election'},
    "senate-2022-CO": {"officeholder_name": 'Michael Bennet', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-CT": {"officeholder_name": 'Richard Blumenthal', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-FL": {"officeholder_name": 'Marco Rubio', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-GA": {"officeholder_name": 'Raphael Warnock', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-HI": {"officeholder_name": 'Brian Schatz', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-IA": {"officeholder_name": 'Chuck Grassley', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-ID": {"officeholder_name": 'Mike Crapo', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-IL": {"officeholder_name": 'Tammy Duckworth', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-IN": {"officeholder_name": 'Todd Young', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-KS": {"officeholder_name": 'Jerry Moran', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-KY": {"officeholder_name": 'Rand Paul', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-LA": {"officeholder_name": 'John Kennedy', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-MD": {"officeholder_name": 'Chris Van Hollen', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-ND": {"officeholder_name": 'John Hoeven', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-NH": {"officeholder_name": 'Maggie Hassan', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-NV": {"officeholder_name": 'Catherine Cortez Masto', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-NY": {"officeholder_name": 'Charles E. Schumer', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-OK": {"officeholder_name": 'James Lankford', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-OR": {"officeholder_name": 'Ron Wyden', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-SC": {"officeholder_name": 'Tim Scott', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-SD": {"officeholder_name": 'John R. Thune', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-UT": {"officeholder_name": 'Mike Lee', "party": 'R', "status": 'seeking_reelection'},
    "senate-2022-WA": {"officeholder_name": 'Patty Murray', "party": 'D', "status": 'seeking_reelection'},
    "senate-2022-WI": {"officeholder_name": 'Ron Johnson', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-CT": {"officeholder_name": 'Christopher S. Murphy', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-FL": {"officeholder_name": 'Rick Scott', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-HI": {"officeholder_name": 'Mazie K. Hirono', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-MA": {"officeholder_name": 'Elizabeth Ann Warren', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-ME": {"officeholder_name": 'Angus King', "party": 'I', "status": 'seeking_reelection'},
    "senate-2024-MN": {"officeholder_name": 'Amy Klobuchar', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-MO": {"officeholder_name": 'Josh Hawley', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-MS": {"officeholder_name": 'Roger F. Wicker', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-MT": {"officeholder_name": 'Jon Tester', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-ND": {"officeholder_name": 'Kevin Cramer', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-NE": {"officeholder_name": 'Deb Fischer', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-NE-unexpired": {"officeholder_name": 'Pete Ricketts', "party": 'R', "status": 'seeking_election'},
    "senate-2024-NM": {"officeholder_name": 'Martin Heinrich', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-NV": {"officeholder_name": 'Jacky S. Rosen', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-NY": {"officeholder_name": 'Kirsten E. Gillibrand', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-OH": {"officeholder_name": 'Sherrod Brown', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-PA": {"officeholder_name": 'Robert P. Casey Jr.', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-RI": {"officeholder_name": 'Sheldon Whitehouse', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-TN": {"officeholder_name": 'Marsha Blackburn', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-TX": {"officeholder_name": 'Ted Cruz', "party": 'R', "status": 'seeking_reelection'},
    "senate-2024-VA": {"officeholder_name": 'Timothy Michael Kaine', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-VT": {"officeholder_name": 'Bernie Sanders', "party": 'I', "status": 'seeking_reelection'},
    "senate-2024-WA": {"officeholder_name": 'Maria Cantwell', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-WI": {"officeholder_name": 'Tammy Baldwin', "party": 'D', "status": 'seeking_reelection'},
    "senate-2024-WY": {"officeholder_name": 'John Barrasso', "party": 'R', "status": 'seeking_reelection'},
    "senate-2018-AZ": {"officeholder_name": 'Jeff Flake', "party": 'R', "status": "open_not_seeking"},
    "senate-2018-TN": {"officeholder_name": 'Bob Corker', "party": 'R', "status": "open_not_seeking"},
    "senate-2018-UT": {"officeholder_name": 'Orrin Hatch', "party": 'R', "status": "open_not_seeking"},
    "senate-2020-KS": {"officeholder_name": 'Pat Roberts', "party": 'R', "status": "open_not_seeking"},
    "senate-2020-NM": {"officeholder_name": 'Tom Udall', "party": 'D', "status": "open_not_seeking"},
    "senate-2020-TN": {"officeholder_name": 'Lamar Alexander', "party": 'R', "status": "open_not_seeking"},
    "senate-2020-WY": {"officeholder_name": 'Mike Enzi', "party": 'R', "status": "open_not_seeking"},
    "senate-2022-AL": {"officeholder_name": 'Richard Shelby', "party": 'R', "status": "open_not_seeking"},
    "senate-2022-MO": {"officeholder_name": 'Roy Blunt', "party": 'R', "status": "open_not_seeking"},
    "senate-2022-NC": {"officeholder_name": 'Richard Burr', "party": 'R', "status": "open_not_seeking"},
    "senate-2022-OH": {"officeholder_name": 'Rob Portman', "party": 'R', "status": "open_not_seeking"},
    "senate-2022-OK-special": {"officeholder_name": 'Jim Inhofe', "party": 'R', "status": "open_not_seeking"},
    "senate-2022-PA": {"officeholder_name": 'Pat Toomey', "party": 'R', "status": "open_not_seeking"},
    "senate-2022-VT": {"officeholder_name": 'Patrick Leahy', "party": 'D', "status": "open_not_seeking"},
    "senate-2024-AZ": {"officeholder_name": 'Kyrsten Sinema', "party": 'I', "status": "open_not_seeking"},
    "senate-2024-CA": {"officeholder_name": 'Dianne Feinstein', "party": 'D', "status": "open_not_seeking"},
    "senate-2024-CA-unexpired": {"officeholder_name": 'Laphonza Butler', "party": 'D', "status": "open_not_seeking"},
    "senate-2024-DE": {"officeholder_name": 'Tom Carper', "party": 'D', "status": "open_not_seeking"},
    "senate-2024-IN": {"officeholder_name": 'Mike Braun', "party": 'R', "status": "open_not_seeking"},
    "senate-2024-MD": {"officeholder_name": 'Ben Cardin', "party": 'D', "status": "open_not_seeking"},
    "senate-2024-MI": {"officeholder_name": 'Debbie Stabenow', "party": 'D', "status": "open_not_seeking"},
    "senate-2024-NJ": {"officeholder_name": 'Bob Menendez', "party": 'D', "status": "open_not_seeking"},
    "senate-2024-UT": {"officeholder_name": 'Mitt Romney', "party": 'R', "status": "open_not_seeking"},
    "senate-2024-WV": {"officeholder_name": 'Joe Manchin', "party": 'I', "status": "open_not_seeking"},
}


def _structural_gaps(cutoff_label: str) -> dict[str, dict[str, Any]]:
    path = ARTIFACTS_DIR / "candidate_timeline_source_gaps_latest.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    block = (payload.get("coverage") or {}).get(cutoff_label) or {}
    out: dict[str, dict[str, Any]] = {}
    for gap in block.get("structurally_unavailable") or []:
        race_id = str(gap.get("race_id") or "")
        if race_id:
            out[race_id] = dict(gap)
    return out


def resolve_historical_personal_incumbency(
    *,
    race_id: str,
    as_of: str,
    cutoff_label: str,
    modeled_candidate_name: str | None,
    opposing_candidate_name: str | None,
    state: str | None = None,
    seat_class: str | None = None,
) -> dict[str, Any]:
    """Resolve personal-incumbency flags for one historical race at a cutoff."""
    office = HISTORICAL_SEAT_OFFICEHOLDERS.get(race_id)
    seat_id = seat_identity_from_race(
        {
            "race_id": race_id,
            "state": state or (race_id.split("-")[2] if "-" in race_id else ""),
            "seat_class": seat_class,
            "election_id": "-".join(race_id.split("-")[:2]),
        }
    )
    if office is None:
        return {
            "race_id": race_id,
            "seat_identity": seat_id,
            "sitting_senator_name": None,
            "modeled_candidate_is_incumbent": False,
            "opposing_candidate_is_incumbent": False,
            "personal_incumbency": 0.0,
            "identity_status": "incumbency_identity_unresolved",
            "provenance": "historical_seat_officeholders_v1",
            "as_of": as_of,
            "cutoff_label": cutoff_label,
        }
    gaps = _structural_gaps(cutoff_label)
    gap = gaps.get(race_id)
    name = office.get("officeholder_name")
    status = str(office.get("status") or "")
    seeking = status in {"seeking_reelection", "seeking_election"}
    if gap and str(gap.get("gap_type")) in {
        "nomination_not_yet_determined",
        "special_election_finalists_not_yet_determined",
    }:
        return {
            "race_id": race_id,
            "seat_identity": seat_id,
            "sitting_senator_name": name,
            "sitting_senator_status": status,
            "modeled_candidate_is_incumbent": False,
            "opposing_candidate_is_incumbent": False,
            "personal_incumbency": 0.0,
            "identity_status": "nomination_not_knowable_at_cutoff",
            "gap_type": gap.get("gap_type"),
            "gap_reason": gap.get("reason"),
            "provenance": "historical_seat_officeholders_v1+structural_gap",
            "as_of": as_of,
            "cutoff_label": cutoff_label,
        }
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
        identity_status = "seat_incumbent_not_on_major_ticket"
        personal = 0.0
    return {
        "race_id": race_id,
        "seat_identity": seat_id,
        "sitting_senator_name": name,
        "sitting_senator_status": status,
        "modeled_candidate_is_incumbent": modeled_inc,
        "opposing_candidate_is_incumbent": opposing_inc,
        "personal_incumbency": personal,
        "identity_status": identity_status,
        "provenance": "historical_seat_officeholders_v1",
        "as_of": as_of,
        "cutoff_label": cutoff_label,
    }


def legacy_party_incumbency_feature(held_by: Any, is_open: Any) -> float:
    if bool(is_open):
        return 0.0
    party = str(held_by or "")
    if party in {"D", "I"}:
        return 1.0
    if party == "R":
        return -1.0
    return 0.0


def _ledger_nominee_sides(election_id: str) -> dict[str, dict[str, str | None]]:
    """Race-id nominee sides including races missing one major-party nominee."""
    from midterms.evidence.official_ledger import load_ledger

    year = str(election_id).replace("senate-", "")
    ledger = load_ledger()
    contests = ((ledger.get("cycles") or {}).get(year) or {}).get("contests") or []
    out: dict[str, dict[str, str | None]] = {}
    for contest in contests:
        race_id = str(contest.get("race_id") or "")
        if not race_id:
            continue
        dem = contest.get("dem_nominee")
        rep = contest.get("rep_nominee")
        out[race_id] = {
            "dem_name": None if dem is None else str(dem),
            "rep_name": None if rep is None else str(rep),
            "state": str(contest.get("state") or "").upper(),
            "seat_class": str(contest.get("seat_class") or "") or None,
        }
    return out


def audit_historical_personal_incumbency() -> dict[str, Any]:
    """Audit personal incumbency for every formal fold race in the snapshot universe."""
    from midterms.evidence.warehouse import Warehouse

    wh = Warehouse(ensure_fixtures=False)
    records: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []
    for year, cutoffs in REQUIRED_FINANCE_CUTOFFS.items():
        election_id = f"senate-{year}"
        tickets = historical_nominee_tickets(election_id)
        sides = _ledger_nominee_sides(election_id)
        for idx, cutoff in enumerate(cutoffs):
            lead = 60 if idx == 0 else 30
            label = f"{election_id}-lead-{lead}"
            snap = wh.build_as_of(cutoff, election_id)
            races = snap.races[~snap.races["not_up"].fillna(False).astype(bool)].copy()
            n_matched = n_zero = n_unresolved = n_changed = 0
            for _, race in races.iterrows():
                race_id = str(race["race_id"])
                ticket = tickets.get(race_id) or {}
                side = sides.get(race_id) or {}
                dem = (
                    ticket.get("dem_name")
                    or ticket.get("modeled_candidate_name")
                    or side.get("dem_name")
                )
                rep = (
                    ticket.get("rep_name")
                    or ticket.get("opposing_candidate_name")
                    or side.get("rep_name")
                )
                resolved = resolve_historical_personal_incumbency(
                    race_id=race_id,
                    as_of=cutoff,
                    cutoff_label=label,
                    modeled_candidate_name=None if dem is None else str(dem),
                    opposing_candidate_name=None if rep is None else str(rep),
                    state=str(race.get("state") or side.get("state") or ""),
                    seat_class=str(race.get("seat_class") or side.get("seat_class") or "") or None,
                )
                row = {
                    **race.to_dict(),
                    "modeled_candidate_is_incumbent": resolved["modeled_candidate_is_incumbent"],
                    "opposing_candidate_is_incumbent": resolved["opposing_candidate_is_incumbent"],
                    "modeled_candidate_name": dem,
                    "opposing_candidate_name": rep,
                }
                personal = float(personal_incumbency_signed(row))
                # Old validated path used party/held_by proxies when incumbent_party present;
                # contested historical rows have null incumbent_party / is_open=False and
                # held_by lean-filled — reconstruct the intended legacy party feature from
                # officeholder party when available, else held_by.
                office = HISTORICAL_SEAT_OFFICEHOLDERS.get(race_id) or {}
                office_party = office.get("party")
                office_status = str(office.get("status") or "")
                legacy_open = office_status == "open_not_seeking" or not office
                legacy = legacy_party_incumbency_feature(
                    office_party if office_party is not None else race.get("held_by"),
                    legacy_open,
                )
                status = resolved["identity_status"]
                if status in {"incumbency_identity_unresolved", "nomination_not_knowable_at_cutoff"}:
                    n_unresolved += 1
                elif personal == 0.0:
                    n_zero += 1
                else:
                    n_matched += 1
                changed = abs(legacy - personal) > 1e-12
                if changed:
                    n_changed += 1
                records.append({
                    "year": year,
                    "lead_days": lead,
                    "cutoff_label": label,
                    "as_of": cutoff,
                    "race_id": race_id,
                    "state": str(race.get("state") or ""),
                    "seat_class": race.get("seat_class"),
                    "seat_identity": resolved.get("seat_identity"),
                    "incumbent_officeholder": resolved.get("sitting_senator_name"),
                    "modeled_candidate": dem,
                    "opposing_candidate": rep,
                    "modeled_candidate_is_incumbent": resolved["modeled_candidate_is_incumbent"],
                    "opposing_candidate_is_incumbent": resolved["opposing_candidate_is_incumbent"],
                    "personal_incumbency_feature": personal,
                    "legacy_party_incumbency_feature": legacy,
                    "differs_from_legacy_party_feature": changed,
                    "identity_status": status,
                    "provenance": resolved.get("provenance"),
                    "gap_type": resolved.get("gap_type"),
                })
            fold_summaries.append({
                "cutoff_label": label,
                "year": year,
                "lead_days": lead,
                "as_of": cutoff,
                "n_model_races": int(len(races)),
                "n_personal_incumbency_nonzero": n_matched,
                "n_zero_open_or_not_on_ticket": n_zero,
                "n_unresolved_or_not_knowable": n_unresolved,
                "n_changed_vs_legacy_party": n_changed,
            })
    payload = {
        "schema_version": "historical-personal-incumbency-v0923",
        "model_version": MODEL_VERSION,
        "n_records": len(records),
        "fold_summaries": fold_summaries,
        "n_changed_formal_inputs": sum(s["n_changed_vs_legacy_party"] for s in fold_summaries),
        "n_unresolved_formal_inputs": sum(s["n_unresolved_or_not_knowable"] for s in fold_summaries),
        "records": records,
        "note": (
            "Personal incumbency uses seat-specific officeholders + cutoff-safe "
            "nomination knowability. Structural nomination gaps fail closed to 0. "
            "No historical OOF was run."
        ),
    }
    path = ARTIFACTS_DIR / "historical_personal_incumbency_v0923.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    payload["path"] = str(path)
    return payload


if __name__ == "__main__":
    print(json.dumps(audit_historical_personal_incumbency()["fold_summaries"], indent=2))
