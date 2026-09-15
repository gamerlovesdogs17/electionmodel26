"""Official U.S. Senate ballot / chamber continuity (audit P0.1–P0.2).

Authoritative class membership and per-cycle contested contests replace
fixture Class-II-for-every-midterm approximations. Sources: U.S. Senate class
definitions; Wikipedia / Ballotpedia certified election summaries (research).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.schema import RACE_COLUMNS

PARSER_VERSION = "official-ballot-v1"

# Wikipedia: Classes of United States senators (abbr)
CLASS_I = [
    "AZ", "CA", "CT", "DE", "FL", "HI", "IN", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NJ", "NM", "NY", "ND", "OH", "PA", "RI",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]
CLASS_II = [
    "AL", "AK", "AR", "CO", "DE", "GA", "ID", "IL", "IA", "KS", "KY", "LA",
    "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH", "NJ", "NM", "NC", "OK",
    "OR", "RI", "SC", "SD", "TN", "TX", "VA", "WV", "WY",
]
CLASS_III = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "MD", "MO", "NV", "NH", "NY", "NC", "ND",
    "OH", "OK", "OR", "PA", "SC", "SD", "UT", "VT", "WA", "WI",
]

assert len(CLASS_I) == 33
assert len(CLASS_II) == 33
assert len(CLASS_III) == 34

# Each state has exactly two Senate classes
def _state_classes() -> dict[str, tuple[str, str]]:
    out: dict[str, list[str]] = {}
    for label, members in (("I", CLASS_I), ("II", CLASS_II), ("III", CLASS_III)):
        for st in members:
            out.setdefault(st, []).append(label)
    return {st: (vals[0], vals[1]) for st, vals in out.items()}


STATE_CLASSES = _state_classes()
assert all(len(v) == 2 for v in STATE_CLASSES.values())
assert len(STATE_CLASSES) == 50

# Approximate long-run leans for prior_lean on official races (same as fixtures)
BASE_LEANS = {
    "AL": -28, "AK": -15, "AZ": -2, "AR": -27, "CA": 22, "CO": 6, "CT": 14,
    "DE": 12, "FL": -4, "GA": 0, "HI": 28, "ID": -32, "IL": 14, "IN": -16,
    "IA": -6, "KS": -14, "KY": -24, "LA": -18, "ME": 4, "MD": 22, "MA": 26,
    "MI": 2, "MN": 4, "MS": -18, "MO": -14, "MT": -12, "NE": -20, "NV": 0,
    "NH": 2, "NJ": 10, "NM": 8, "NY": 18, "NC": -2, "ND": -28, "OH": -6,
    "OK": -30, "OR": 10, "PA": 1, "RI": 18, "SC": -14, "SD": -26, "TN": -22,
    "TX": -8, "UT": -24, "VT": 30, "VA": 6, "WA": 12, "WV": -30, "WI": 1,
    "WY": -40,
}


def election_day(year: int) -> date:
    from datetime import timedelta

    d = date(year, 11, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d + timedelta(days=1)


# Per-cycle official metadata (pre-election held caucus counts + post control)
# held_dem includes Independents who caucus with Democrats.
CYCLE_META: dict[int, dict[str, Any]] = {
    2018: {
        "seat_class_up": "I",
        "vp_tiebreak_party": "R",
        "held_dem": 47,  # overwritten by sync_held_counts_from_certified()
        "held_rep": 20,
        "held_ind": 2,
        "post_dem_seats": 47,  # 45D+2I after 2018
        "post_dem_control": False,
        "specials": [],
        "note": "Class I regular; GOP retained majority",
        "source": "Wikipedia: 2018 United States Senate elections",
    },
    2020: {
        "seat_class_up": "II",
        "vp_tiebreak_party": "D",  # cycle completion after GA runoffs + Harris inauguration
        "held_dem": 35,
        "held_rep": 30,
        "held_ind": 2,
        "post_dem_seats": 50,
        "post_dem_control": True,
        "specials": [],
        "ga_runoffs": ["GA"],
        "note": "Class II; GA dual runoffs Jan 5 2021 sealed 50-50 + Dem VP control",
        "source": "Wikipedia: 2020 United States Senate elections",
    },
    2022: {
        "seat_class_up": "III",
        "vp_tiebreak_party": "D",
        "held_dem": 36,  # 34 D + 2 I not up
        "held_rep": 29,
        "held_ind": 2,
        "post_dem_seats": 51,
        "post_dem_control": True,
        "specials": [
            {
                "state": "OK",
                "seat_class": "II",
                "kind": "special",
                "race_suffix": "OK-special",
                "vacancy_reason": "resignation",
            }
        ],
        "note": "Class III + OK Class II special; Dems 51–49",
        "source": "Wikipedia: 2022 United States Senate elections",
    },
    2024: {
        "seat_class_up": "I",
        "vp_tiebreak_party": "D",  # election day under Harris VP
        "held_dem": 28,
        "held_rep": 38,
        "held_ind": 1,
        "post_dem_seats": 47,
        "post_dem_control": False,
        "specials": [],
        "note": "Class I; GOP majority after cycle",
        "source": "Wikipedia: 2024 United States Senate elections",
    },
    # 2014 / 2016: full certified margins + specials (production gate)
    2014: {
        "seat_class_up": "II",
        "vp_tiebreak_party": "D",
        "held_dem": 34,  # overwritten by sync_held_counts_from_certified()
        "held_rep": 30,
        "held_ind": 2,
        "post_dem_seats": 46,  # 44D+2I caucus
        "post_dem_control": False,
        "specials": [
            {
                "state": "HI",
                "seat_class": "III",
                "kind": "special",
                "race_suffix": "HI-special",
                "vacancy_reason": "appointment",
            },
            {
                "state": "OK",
                "seat_class": "III",
                "kind": "special",
                "race_suffix": "OK-special",
                "vacancy_reason": "resignation",
            },
            {
                "state": "SC",
                "seat_class": "III",
                "kind": "special",
                "race_suffix": "SC-special",
                "vacancy_reason": "appointment",
            },
        ],
        "note": "Class II + HI/OK/SC Class III specials; GOP majority (54–46 caucus)",
        "source": "Wikipedia / FEC Federal Elections 2014",
    },
    2016: {
        "seat_class_up": "III",
        "vp_tiebreak_party": "D",
        "held_dem": 36,
        "held_rep": 30,
        "held_ind": 2,
        "post_dem_seats": 48,  # 46D+2I caucus; chamber 52–48 R
        "post_dem_control": False,
        "specials": [],
        "note": "Class III; chamber 52–48 R after certified margins",
        "source": "Wikipedia / MEDSL+curated certified returns 2016",
    },
}


def sync_held_counts_from_certified() -> None:
    """DEPRECATED (fresh audit R-02).

    Previously solved held seats from post_dem_seats − contested Dem wins, making
    chamber reconcile tautological. Held seats now come only from
    ``independent_chamber_expectations.json``.
    """
    return


def load_cycle_expectations(year: int) -> dict[str, Any]:
    from midterms.evidence.official_ledger import load_expectations

    block = (load_expectations().get("cycles") or {}).get(str(year))
    if not block:
        raise ValueError(f"no independent chamber expectations for {year}")
    return block


def build_held_rows_from_expectations(
    year: int,
    exp: dict[str, Any],
    *,
    election_day: str,
) -> list[dict[str, Any]]:
    """Materialize not-up seats from independent expectations (not winner-solved)."""
    n_held_dem = int(exp["held_dem"])
    n_held_ind = int(exp.get("held_ind") or 0)
    n_held_d_party = max(0, n_held_dem - n_held_ind)
    n_held_rep = int(exp["held_rep"])
    held_parties = (["D"] * n_held_d_party) + (["I"] * n_held_ind) + (["R"] * n_held_rep)
    up = (CYCLE_META.get(year) or {}).get("seat_class_up")
    held_slots: list[tuple[str, str]] = []
    for st, (c1, c2) in sorted(STATE_CLASSES.items()):
        for sc in (c1, c2):
            if up and sc == up:
                continue
            if year == 2022 and st == "OK" and sc == "II":
                continue
            if year == 2018 and st in {"MN", "MS"} and sc == "II":
                continue
            if year == 2020 and st == "AZ" and sc == "III":
                continue
            if year == 2020 and st == "GA" and sc == "III":
                continue
            if year == 2024 and st == "NE" and sc == "II":
                continue
            held_slots.append((st, sc))
    while len(held_slots) < len(held_parties):
        held_slots.append(("XX", "held"))
    held_slots = held_slots[: len(held_parties)]
    rows: list[dict[str, Any]] = []
    for i, ((st, sc), party) in enumerate(zip(held_slots, held_parties)):
        lean = float(BASE_LEANS.get(st, 0.0)) if st in BASE_LEANS else 0.0
        rows.append(
            {
                "race_id": f"senate-{year}-held-{st}-{sc}-{i}",
                "election_id": f"senate-{year}",
                "office": "US_SENATE",
                "state": st if st != "XX" else "AL",
                "seat_class": sc if sc != "held" else "held",
                "election_day": election_day,
                "incumbent_party": party,
                "is_open": False,
                "held_by": party,
                "prior_lean": lean,
                "region": _region(st if st != "XX" else "AL"),
                "not_up": True,
                "fundraising_share": 0.5,
                "pres_approval": 0.0,
                "white_house_party": "D" if year in {2014, 2016, 2022, 2024} else "R",
                "is_midterm": bool(year % 4 == 2),
                "election_phase": "general",
                "runoff_of": None,
                "vacancy_reason": None,
                "ballot_status": "nominated",
                "effective_election_day": election_day,
            }
        )
    return rows


def class_states(seat_class: str) -> list[str]:
    return {
        "I": CLASS_I,
        "II": CLASS_II,
        "III": CLASS_III,
    }[seat_class]


def contested_contests(year: int) -> list[dict[str, Any]]:
    """Official contested race descriptors — loaded from external ledger when present."""
    if year == 2026:
        from midterms.evidence.fixtures import CLASS_II as C2, SPECIALS_2026

        rows = []
        for st in C2:
            rows.append(
                {
                    "state": st,
                    "seat_class": "II",
                    "kind": "regular",
                    "race_id": f"senate-2026-{st}",
                    "vacancy_reason": None,
                }
            )
        for st in SPECIALS_2026:
            rows.append(
                {
                    "state": st,
                    "seat_class": "special",
                    "kind": "special",
                    "race_id": f"senate-2026-{st}",
                    "vacancy_reason": "appointment",
                }
            )
        return rows

    try:
        from midterms.evidence.official_ledger import contests_for_year, LEDGER_PATH

        if LEDGER_PATH.exists():
            return [
                {
                    "state": c["state"],
                    "seat_class": c.get("seat_class"),
                    "kind": c.get("kind") or "regular",
                    "race_id": c["race_id"],
                    "election_day": c.get("election_day"),
                    "vacancy_reason": c.get("vacancy_reason"),
                    "term_type": c.get("term_type"),
                }
                for c in contests_for_year(year)
            ]
    except Exception:
        pass

    # Legacy fallback (should be unused once ledger is written)
    meta = CYCLE_META.get(year)
    if not meta:
        raise ValueError(f"no official ballot metadata for {year}")
    ed = election_day(year)
    rows = []
    for st in class_states(meta["seat_class_up"]):
        rows.append(
            {
                "state": st,
                "seat_class": meta["seat_class_up"],
                "kind": "regular",
                "race_id": f"senate-{year}-{st}",
                "election_day": ed.isoformat(),
                "vacancy_reason": None,
            }
        )
    for sp in meta.get("specials") or []:
        suffix = sp.get("race_suffix") or f"{sp['state']}-special"
        rows.append(
            {
                "state": sp["state"],
                "seat_class": sp.get("seat_class", "special"),
                "kind": "special",
                "race_id": f"senate-{year}-{suffix}",
                "election_day": ed.isoformat(),
                "vacancy_reason": sp.get("vacancy_reason"),
            }
        )
    return rows


def build_official_races_frame(year: int) -> pd.DataFrame:
    """100-seat race table from external ledger + independent held expectations."""
    if year == 2026:
        from midterms.evidence.fixtures import generate_2026_races

        return generate_2026_races()

    from midterms.evidence.official_ledger import contests_for_year

    ed = election_day(year).isoformat()
    contests = contests_for_year(year)
    exp = load_cycle_expectations(year)
    rows: list[dict[str, Any]] = []
    for c in contests:
        st = c["state"]
        lean = float(c.get("prior_lean") if c.get("prior_lean") is not None else BASE_LEANS.get(st, 0.0))
        rows.append(
            {
                "race_id": c["race_id"],
                "election_id": f"senate-{year}",
                "office": "US_SENATE",
                "state": st,
                "seat_class": c.get("seat_class"),
                "election_day": ed,
                "incumbent_party": c.get("incumbent_party"),
                "is_open": bool(c.get("is_open", False)),
                "held_by": c.get("held_by") or ("R" if lean < 0 else "D"),
                "prior_lean": lean,
                "region": _region(st),
                "not_up": False,
                "fundraising_share": 0.5,
                "pres_approval": 0.0,
                "white_house_party": "D" if year in {2014, 2016, 2022, 2024} else "R",
                "is_midterm": bool(year % 4 == 2),
                "election_phase": "general",
                "runoff_of": c.get("runoff_of"),
                "vacancy_reason": c.get("vacancy_reason"),
                "ballot_status": "nominated",
                "effective_election_day": ed,
            }
        )
    rows.extend(build_held_rows_from_expectations(year, exp, election_day=ed))
    df = pd.DataFrame(rows)
    for col in RACE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[RACE_COLUMNS]


def _region(st: str) -> str:
    from midterms.config import REGIONS

    for name, members in REGIONS.items():
        if st in members:
            return name
    return "other"


def write_official_ballot_store(years: tuple[int, ...] | None = None) -> dict[str, Any]:
    """Persist official races from the external ledger (fresh audit R-01)."""
    from midterms.evidence.build_official_ledger import build_and_write
    from midterms.evidence.fixtures import generate_2026_races
    from midterms.evidence.official_ledger import write_ledger_normalized

    build_and_write()
    man = write_ledger_normalized()
    # Append 2026 curated races into races_official
    path = NORMALIZED_DIR / "races_official.parquet"
    hist = pd.read_parquet(path)
    cur = generate_2026_races()
    races = pd.concat([hist[hist["election_id"] != "senate-2026"], cur], ignore_index=True)
    for col in RACE_COLUMNS:
        if col not in races.columns:
            races[col] = None
    races = races[RACE_COLUMNS]
    races.to_parquet(path, index=False)
    man["years"] = sorted({int(str(e).split("-")[-1]) for e in races["election_id"].unique()})
    man["n_rows"] = int(len(races))
    man["includes_2026"] = True
    (MANIFESTS_DIR / "official_senate_ballots.json").write_text(json.dumps(man, indent=2))
    return man


def merge_official_into_races(races: pd.DataFrame) -> pd.DataFrame:
    """Prefer official historical ballots over synthetic fixture races."""
    path = NORMALIZED_DIR / "races_official.parquet"
    if not path.exists():
        write_official_ballot_store()
    official = pd.read_parquet(path)
    if official.empty:
        return races
    eids = set(official["election_id"].unique())
    keep = races[~races["election_id"].isin(eids)] if len(races) else races
    return pd.concat([keep, official], ignore_index=True)
