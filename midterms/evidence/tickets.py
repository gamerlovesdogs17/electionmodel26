"""Curated 2026 Senate general-election tickets for UI / artifact display."""

from __future__ import annotations

# (Dem display name, Rep display name) — research labels for contested Class II seats.
# Prefer frontrunners / likely nominees from the public archive; not an endorsement.
TICKETS_2026: dict[str, tuple[str, str]] = {
    "AL": ("Alani Bankhead", "Barry Moore"),
    "AK": ("Mary Peltola", "Dan Sullivan"),
    "AR": ("Hallie Shoffner", "Tom Cotton"),
    "CO": ("Democrat", "Republican"),
    "DE": ("Democrat", "Republican"),
    "GA": ("Jon Ossoff", "Mike Collins"),
    "ID": ("Todd Achilles", "Jim Risch"),
    "IL": ("Democrat", "Republican"),
    "IA": ("Josh Turek", "Ashley Hinson"),
    "KS": ("Adam Hamilton", "Roger Marshall"),
    "KY": ("Democrat", "Republican"),
    "LA": ("Democrat", "Republican"),
    "ME": ("Graham Platner", "Susan Collins"),
    "MA": ("Ed Markey", "John Deaton"),
    "MI": ("Haley Stevens", "Mike Rogers"),
    "MN": ("Angie Craig", "Royce White"),
    "MS": ("Scott Colom", "Cindy Hyde-Smith"),
    "MT": ("Seth Bodnar", "Kurt Alme"),
    "NE": ("Democrat", "Republican"),
    "NH": ("Chris Pappas", "John Sununu"),
    "NJ": ("Democrat", "Republican"),
    "NM": ("Ben Ray Luján", "Larry Marker"),
    "NC": ("Roy Cooper", "Michael Whatley"),
    "OK": ("Democrat", "Republican"),
    "OR": ("Democrat", "Republican"),
    "RI": ("Jack Reed", "Raymond McKay"),
    "SC": ("Annie Andrews", "Lindsey Graham"),
    "SD": ("Marie Gladue", "Mike Rounds"),
    "TN": ("Marquita Bradshaw", "Bill Hagerty"),
    "TX": ("James Talarico", "Ken Paxton"),
    "VA": ("Mark Warner", "Republican"),
    "WV": ("Democrat", "Republican"),
    "WY": ("Democrat", "Republican"),
}


def ticket_for_state(state: str) -> tuple[str, str]:
    return TICKETS_2026.get(str(state).upper(), ("Democrat", "Republican"))
