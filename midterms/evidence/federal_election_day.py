"""Authoritative U.S. federal Election Day helper.

Federal Election Day is the Tuesday next after the first Monday in November
(2 U.S.C. § 7). All formal fold/cutoff construction must use this helper.
"""

from __future__ import annotations

from datetime import date, timedelta


def federal_election_day(year: int) -> date:
    """Return the federal general Election Day for ``year``.

    Rule: Tuesday next after the first Monday in November.
    Equivalent construction: find the first Monday on/after November 1, then
    add one day. This is **not** the first Tuesday on or after November 1 —
    those differ when November 1 itself is Tuesday (e.g. 2022 → Nov 8, not 1).
    """
    d = date(int(year), 11, 1)
    while d.weekday() != 0:  # Monday
        d += timedelta(days=1)
    return d + timedelta(days=1)


def federal_election_day_from_election_id(election_id: str) -> date:
    """Parse ``senate-YYYY`` (or trailing year) and return Election Day."""
    token = str(election_id).split("-")[-1]
    try:
        year = int(token)
    except ValueError as exc:
        raise ValueError(
            f"election_id {election_id!r} does not end with a calendar year"
        ) from exc
    return federal_election_day(year)


def formal_cutoff(year: int, lead_days: int) -> date:
    """Election Day minus ``lead_days`` for a formal validation fold."""
    return federal_election_day(year) - timedelta(days=int(lead_days))


__all__ = [
    "federal_election_day",
    "federal_election_day_from_election_id",
    "formal_cutoff",
]
