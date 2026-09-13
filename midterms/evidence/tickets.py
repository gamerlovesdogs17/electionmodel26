"""Curated 2026 Senate general-election tickets for UI / artifact display."""

from __future__ import annotations

from typing import Any

# Research labels for contested seats (Class II + 2026 specials).
# Prefer frontrunners / likely nominees from the public archive; not an endorsement.
# dem_party: "D" or "I" — Independents with no Dem nominee still map to Dem caucus seats.
TICKETS_2026: dict[str, dict[str, Any]] = {
    "AL": {"dem_name": "Alani Bankhead", "rep_name": "Barry Moore", "dem_party": "D"},
    "AK": {"dem_name": "Mary Peltola", "rep_name": "Dan Sullivan", "dem_party": "D"},
    "AR": {"dem_name": "Hallie Shoffner", "rep_name": "Tom Cotton", "dem_party": "D"},
    "CO": {"dem_name": "Michael Bennet", "rep_name": "Janak Joshi", "dem_party": "D"},
    "DE": {"dem_name": "Chris Coons", "rep_name": "Republican nominee", "dem_party": "D"},
    "FL": {"dem_name": "Eugene Vindman", "rep_name": "Ashley Moody", "dem_party": "D"},
    "GA": {"dem_name": "Jon Ossoff", "rep_name": "Mike Collins", "dem_party": "D"},
    "ID": {"dem_name": "Todd Achilles", "rep_name": "Jim Risch", "dem_party": "D"},
    "IL": {"dem_name": "Tammy Duckworth", "rep_name": "Republican nominee", "dem_party": "D"},
    "IA": {"dem_name": "Josh Turek", "rep_name": "Ashley Hinson", "dem_party": "D"},
    "KS": {"dem_name": "Adam Hamilton", "rep_name": "Roger Marshall", "dem_party": "D"},
    "KY": {"dem_name": "Democratic nominee", "rep_name": "Mitch McConnell", "dem_party": "D"},
    "LA": {"dem_name": "Democratic nominee", "rep_name": "Bill Cassidy", "dem_party": "D"},
    "ME": {"dem_name": "Graham Platner", "rep_name": "Susan Collins", "dem_party": "D"},
    "MA": {"dem_name": "Ed Markey", "rep_name": "John Deaton", "dem_party": "D"},
    "MI": {"dem_name": "Haley Stevens", "rep_name": "Mike Rogers", "dem_party": "D"},
    "MN": {"dem_name": "Angie Craig", "rep_name": "Royce White", "dem_party": "D"},
    "MS": {"dem_name": "Scott Colom", "rep_name": "Cindy Hyde-Smith", "dem_party": "D"},
    "MT": {"dem_name": "Seth Bodnar", "rep_name": "Kurt Alme", "dem_party": "D"},
    "NE": {"dem_name": "Democratic nominee", "rep_name": "Deb Fischer", "dem_party": "D"},
    "NH": {"dem_name": "Chris Pappas", "rep_name": "John Sununu", "dem_party": "D"},
    "NJ": {"dem_name": "Cory Booker", "rep_name": "Republican nominee", "dem_party": "D"},
    "NM": {"dem_name": "Ben Ray Luján", "rep_name": "Larry Marker", "dem_party": "D"},
    "NC": {"dem_name": "Roy Cooper", "rep_name": "Michael Whatley", "dem_party": "D"},
    "OH": {"dem_name": "Sherrod Brown", "rep_name": "Jon Husted", "dem_party": "D"},
    "OK": {"dem_name": "Democratic nominee", "rep_name": "Markwayne Mullin", "dem_party": "D"},
    "OR": {"dem_name": "Jeff Merkley", "rep_name": "Republican nominee", "dem_party": "D"},
    "RI": {"dem_name": "Jack Reed", "rep_name": "Raymond McKay", "dem_party": "D"},
    "SC": {"dem_name": "Annie Andrews", "rep_name": "Lindsey Graham", "dem_party": "D"},
    "SD": {"dem_name": "Marie Gladue", "rep_name": "Mike Rounds", "dem_party": "D"},
    "TN": {"dem_name": "Marquita Bradshaw", "rep_name": "Bill Hagerty", "dem_party": "D"},
    "TX": {"dem_name": "James Talarico", "rep_name": "Ken Paxton", "dem_party": "D"},
    "VA": {"dem_name": "Mark Warner", "rep_name": "Republican nominee", "dem_party": "D"},
    "WV": {"dem_name": "Democratic nominee", "rep_name": "Shelley Moore Capito", "dem_party": "D"},
    "WY": {"dem_name": "Democratic nominee", "rep_name": "Cynthia Lummis", "dem_party": "D"},
}


def ticket_for_state(state: str) -> dict[str, Any]:
    st = str(state).upper()
    row = TICKETS_2026.get(st)
    if row:
        return dict(row)
    return {"dem_name": "Democrat", "rep_name": "Republican", "dem_party": "D"}


def ticket_names(state: str) -> tuple[str, str]:
    """Backward-compatible (dem_or_ind_name, rep_name). """
    t = ticket_for_state(state)
    return str(t["dem_name"]), str(t["rep_name"])
