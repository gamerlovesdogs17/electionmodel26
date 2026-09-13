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
    """Set held_dem/held_rep so certified contested winners reproduce post_dem_seats."""
    from midterms.evidence.results_archive import CERTIFIED_MARGINS

    for year, meta in CYCLE_META.items():
        margins = CERTIFIED_MARGINS.get(f"senate-{year}")
        if not margins:
            continue
        contests = contested_contests(year)
        n_cont = len(contests)
        dem_wins = 0
        for c in contests:
            key = c["race_id"].replace(f"senate-{year}-", "")
            m = margins.get(key)
            if m is None and c.get("kind") == "regular":
                m = margins.get(c["state"])
            if m is not None and float(m) >= 0:
                dem_wins += 1
        post = int(meta["post_dem_seats"])
        held_dem = post - dem_wins
        held_total = 100 - n_cont
        held_rep = held_total - held_dem
        held_ind = min(int(meta.get("held_ind") or 0), max(held_dem, 0))
        if held_dem < 0 or held_rep < 0:
            raise ValueError(
                f"{year}: inconsistent certified margins vs post_dem_seats "
                f"(dem_wins={dem_wins}, post={post}, held_total={held_total})"
            )
        meta["held_dem"] = held_dem
        meta["held_rep"] = held_rep
        meta["held_ind"] = held_ind
        meta["certified_dem_wins"] = dem_wins


def class_states(seat_class: str) -> list[str]:
    return {
        "I": CLASS_I,
        "II": CLASS_II,
        "III": CLASS_III,
    }[seat_class]


def contested_contests(year: int) -> list[dict[str, Any]]:
    """Official contested race descriptors for a Senate cycle."""
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
    """
    100-seat race table: contested official contests + held seats.

    Held seats are materialized to exact pre-election caucus counts from CYCLE_META
    (audit P0.2 continuity). Geographic labels on held seats are continuity fillers;
    contested rows are the authoritative ballot.
    """
    sync_held_counts_from_certified()
    if year == 2026:
        from midterms.evidence.fixtures import generate_2026_races

        return generate_2026_races()

    meta = CYCLE_META[year]
    ed = election_day(year).isoformat()
    contests = contested_contests(year)
    contested_ids = {c["race_id"] for c in contests}
    rows: list[dict[str, Any]] = []

    for c in contests:
        st = c["state"]
        lean = float(BASE_LEANS.get(st, 0.0))
        rows.append(
            {
                "race_id": c["race_id"],
                "election_id": f"senate-{year}",
                "office": "US_SENATE",
                "state": st,
                "seat_class": c["seat_class"],
                "election_day": ed,
                "incumbent_party": None,
                "is_open": False,
                "held_by": "R" if lean < 0 else "D",
                "prior_lean": lean,
                "region": _region(st),
                "not_up": False,
                "fundraising_share": 0.5,
                "pres_approval": 0.0,
                "white_house_party": "D" if year in {2014, 2016, 2022, 2024} else "R",
                "is_midterm": bool(year % 4 == 2),
                "election_phase": "general",
                "runoff_of": None,
                "vacancy_reason": c.get("vacancy_reason"),
                "ballot_status": "nominated",
                "effective_election_day": ed,
            }
        )

    # Held seats: exact caucus counts
    n_held_dem = int(meta["held_dem"])  # includes I in caucus count for simulator
    n_held_ind = int(meta.get("held_ind") or 0)
    n_held_d_party = n_held_dem - n_held_ind
    n_held_rep = int(meta["held_rep"])
    assert n_held_dem + n_held_rep == 100 - len(contests), (
        f"{year}: held+contested must be 100 "
        f"(held={n_held_dem + n_held_rep}, contested={len(contests)})"
    )

    held_parties = (["D"] * n_held_d_party) + (["I"] * n_held_ind) + (["R"] * n_held_rep)
    # Prefer labeling held seats with states whose class is not up
    up = meta["seat_class_up"]
    held_slots: list[tuple[str, str]] = []
    for st, (c1, c2) in sorted(STATE_CLASSES.items()):
        for sc in (c1, c2):
            if sc == up:
                continue
            # Skip OK Class II when 2022 special contests that seat
            if year == 2022 and st == "OK" and sc == "II":
                continue
            held_slots.append((st, sc))
    # Pad if short (should not happen often)
    while len(held_slots) < len(held_parties):
        held_slots.append(("XX", "held"))
    held_slots = held_slots[: len(held_parties)]

    for i, ((st, sc), party) in enumerate(zip(held_slots, held_parties)):
        rid = f"senate-{year}-held-{st}-{sc}-{i}"
        if rid in contested_ids:
            continue
        lean = float(BASE_LEANS.get(st, 0.0)) if st in BASE_LEANS else 0.0
        rows.append(
            {
                "race_id": rid,
                "election_id": f"senate-{year}",
                "office": "US_SENATE",
                "state": st if st != "XX" else "AL",
                "seat_class": sc if sc != "held" else "held",
                "election_day": ed,
                "incumbent_party": party if party != "I" else "I",
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
                "effective_election_day": ed,
            }
        )

    df = pd.DataFrame(rows)
    # Align to schema columns present
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
    """Persist official races for historical cycles + reconciliation manifest."""
    from midterms.config import CYCLES

    years = years or tuple(y for y in CYCLES if y in CYCLE_META)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    frames = [build_official_races_frame(y) for y in years]
    # Keep 2026 from existing generator when building full store
    from midterms.evidence.fixtures import generate_2026_races

    frames.append(generate_2026_races())
    races = pd.concat(frames, ignore_index=True)

    raw_path = RAW_DIR / "external" / "official_senate_ballots.json"
    payload = {
        "parser_version": PARSER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycles": {
            str(y): {
                "meta": CYCLE_META.get(y),
                "contested": contested_contests(y),
                "n_races_rows": int((races["election_id"] == f"senate-{y}").sum()),
            }
            for y in list(years) + [2026]
        },
    }
    raw_path.write_text(json.dumps(payload, indent=2, default=str))

    out = NORMALIZED_DIR / "races_official.parquet"
    races.to_parquet(out, index=False)
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser_version": PARSER_VERSION,
        "years": list(years) + [2026],
        "n_rows": int(len(races)),
        "paths": {"raw": str(raw_path), "normalized": str(out)},
        "note": "Official class/special ballots; replaces fixture Class-II midterm approximation.",
    }
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
    # Drop fixture rows for election_ids present in official store
    eids = set(official["election_id"].unique())
    keep = races[~races["election_id"].isin(eids)] if len(races) else races
    return pd.concat([keep, official], ignore_index=True)


# Finalize held caucus counts from certified margins (must run after contested_contests exists)
sync_held_counts_from_certified()
