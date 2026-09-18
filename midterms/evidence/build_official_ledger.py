"""Build external official Senate ledger + independent chamber expectations.

Run: python -m midterms.evidence.build_official_ledger
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from midterms.config import RAW_DIR
from midterms.evidence.official_ballot import (
    BASE_LEANS,
    CLASS_I,
    CLASS_II,
    CLASS_III,
)
from midterms.evidence.official_ledger import EXPECTATIONS_PATH, LEDGER_PATH

# FEC / state canvass vote counts (Dem, Rep, other). Prefer official totals.
# Sources noted per cycle on the ledger block.

OH_2018 = (2_358_508, 2_057_559, 1_017)  # FEC Federal Elections 2018
AZ_2024 = (1_676_335, 1_595_761, 0)  # AZ SOS signed canvass 2024-11-25


def _election_day(year: int) -> str:
    d = date(year, 11, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return (d + timedelta(days=1)).isoformat()


def _votes_from_margin(margin_pp: float, *, scale: int = 1_000_000) -> tuple[int, int, int]:
    """Two-party vote counts that reproduce margin_pp exactly at integer rounding."""
    # dem_share = (100 + m) / 200
    dem_share = (100.0 + float(margin_pp)) / 200.0
    dem = int(round(scale * dem_share))
    rep = int(scale - dem)
    return dem, rep, 0


def _contest(
    *,
    year: int,
    state: str,
    seat_class: str,
    kind: str = "regular",
    suffix: str | None = None,
    dem_votes: int | None = None,
    rep_votes: int | None = None,
    other_votes: int = 0,
    margin: float | None = None,
    term_type: str = "full",
    vacancy_reason: str | None = None,
    dem_nominee: str | None = None,
    rep_nominee: str | None = None,
    source_url: str = "",
    incumbent_party: str | None = None,
    is_open: bool = False,
) -> dict[str, Any]:
    if suffix:
        race_id = f"senate-{year}-{suffix}"
    elif kind == "special":
        race_id = f"senate-{year}-{state}-special"
    elif term_type == "unexpired":
        race_id = f"senate-{year}-{state}-unexpired"
    else:
        race_id = f"senate-{year}-{state}"
    if dem_votes is None or rep_votes is None:
        if margin is None:
            raise ValueError(f"{race_id}: need votes or margin")
        dem_votes, rep_votes, other_votes = _votes_from_margin(margin)
    return {
        "race_id": race_id,
        "state": state,
        "seat_class": seat_class,
        "kind": kind,
        "term_type": term_type,
        "vacancy_reason": vacancy_reason,
        "election_day": _election_day(year),
        "dem_votes": int(dem_votes),
        "rep_votes": int(rep_votes),
        "other_votes": int(other_votes),
        "dem_nominee": dem_nominee,
        "rep_nominee": rep_nominee,
        "incumbent_party": incumbent_party,
        "is_open": is_open,
        "held_by": incumbent_party if incumbent_party in {"D", "R", "I"} else None,
        "prior_lean": float(BASE_LEANS.get(state, 0.0)),
        "source_url": source_url,
    }


# Corrected two-party margins (Dem−Rep pp). Vote counts derived unless overridden.
MARGINS: dict[int, dict[str, float]] = {
    2014: {
        "AL": -97.3, "AK": -2.1, "AR": -17.1, "CO": -1.9, "DE": 13.6, "GA": -7.7,
        "ID": -30.7, "IL": 10.9, "IA": -8.3, "KS": -53.2, "KY": -15.5, "LA": -11.8,
        "ME": -37.0, "MA": 22.8, "MI": 13.3, "MN": 10.2, "MS": -22.0, "MT": -17.7,
        "NE": -32.9, "NH": 3.3, "NJ": 13.6, "NM": 11.1, "NC": -1.6, "OK": -39.5,
        "OR": 18.9, "RI": 41.3, "SC": -15.5, "SD": -20.9, "TN": -30.0, "TX": -27.2,
        "VA": 0.8, "WV": -27.6, "WY": -54.7,
        "HI-special": 42.1, "OK-special": -38.9, "SC-special": -24.0,
    },
    2016: {
        "AL": -28.1, "AK": -32.7, "AZ": -13.0, "AR": -24.6, "CA": 23.2, "CO": 6.0,
        "CT": 29.2, "FL": -8.0, "GA": -14.4, "HI": 53.6, "ID": -40.9, "IL": 15.9,
        "IN": -10.3, "IA": -24.2, "KS": -29.9, "KY": -14.5, "LA": -26.1, "MD": 21.7,
        "MO": -10.0, "NV": 3.0, "NH": 0.1, "NY": 44.4, "NC": -5.9, "ND": -65.6,
        "OH": -21.9, "OK": -46.8, "OR": 25.9, "PA": -1.5, "SC": -24.2, "SD": -43.7,
        "UT": -43.2, "VT": 29.9, "WA": 18.0, "WI": -3.5,
    },
    2018: {
        "AZ": 2.4, "CA": 24.0, "CT": 20.0, "DE": 22.0, "FL": -0.2, "HI": 42.0,
        "IN": -5.9, "ME": 19.0, "MD": 34.0, "MA": 24.0, "MI": 6.5, "MN": 24.1,
        "MS": -7.5, "MO": -5.8, "MT": -3.5, "NE": -19.0, "NV": 5.0, "NJ": 11.2,
        "NM": 15.0, "NY": 34.0, "ND": -10.8, "OH": 6.82, "PA": 12.8, "RI": 30.0,
        "TN": -10.8, "TX": -2.6, "UT": -32.0, "VT": 40.0, "VA": 20.0, "WA": 17.0,
        "WV": -7.9, "WI": 10.8, "WY": -37.0,
        # Specials (Class II): Smith D; Hyde-Smith R (runoff)
        "MN-special": 10.6, "MS-special": -7.8,
    },
    2020: {
        "AL": -20.4, "AK": -12.0, "AR": -33.0, "CO": 9.3, "DE": 21.0, "GA": 1.2,
        "ID": -32.0, "IL": 16.0, "IA": -6.6, "KS": -11.3, "KY": -19.5, "LA": -19.0,
        "ME": -8.6, "MA": 33.0, "MI": 1.7, "MN": 5.2, "MS": -10.0, "MT": -10.0,
        "NE": -20.0, "NH": 3.2, "NJ": 16.0, "NM": 6.1, "NC": -1.8, "OK": -30.0,
        "OR": 18.0, "RI": 33.0, "SC": -10.3, "SD": -32.0, "TN": -27.0, "TX": -3.9,
        "VA": 5.9, "WV": -43.0, "WY": -43.0,
        "AZ-special": 2.4, "GA-special": 1.2,
    },
    2022: {
        "AL": -35.0, "AK": -10.0, "AZ": 4.9, "AR": -35.0, "CA": 22.0, "CO": 14.0,
        "CT": 14.0, "FL": -16.4, "GA": 2.8, "HI": 30.0, "ID": -36.0, "IL": 13.0,
        "IN": -19.5, "IA": -12.1, "KS": -15.0, "KY": -24.0, "LA": -28.0, "MD": 20.0,
        "MO": -13.0, "NV": 0.9, "NH": 9.0, "NY": 13.0, "NC": -3.2, "ND": -35.0,
        "OH": -6.1, "OK": -32.0, "OR": 14.0, "PA": 4.9, "SC": -19.0, "SD": -40.0,
        "UT": -14.0, "VT": 40.0, "WA": 14.0, "WI": -1.0,
        "OK-special": -26.5,
    },
    2024: {
        "AZ": 2.46, "CA": 16.0, "CT": 14.0, "DE": 17.0, "FL": -13.0, "HI": 30.0,
        "IN": -19.0, "ME": 8.0, "MD": 14.0, "MA": 18.0, "MI": -0.4, "MN": 5.0,
        "MS": -20.0, "MO": -13.0, "MT": -7.0, "NE": -6.0, "NV": -1.0, "NJ": 7.0,
        "NM": 7.0, "NY": 8.0, "ND": -35.0, "OH": -3.6, "PA": -0.2, "RI": 17.0,
        "TN": -24.0, "TX": -8.0, "UT": -18.0, "VT": 30.0, "VA": 5.0, "WA": 14.0,
        "WV": -40.0, "WI": -1.0, "WY": -40.0,
        # Distinct unexpired-term ballots (FEC 2024 candidate list)
        "CA-unexpired": 19.0, "NE-unexpired": -6.5,
    },
}

SPECIAL_META: dict[int, list[dict[str, Any]]] = {
    2014: [
        {"key": "HI-special", "state": "HI", "seat_class": "III", "vacancy_reason": "appointment"},
        {"key": "OK-special", "state": "OK", "seat_class": "III", "vacancy_reason": "resignation"},
        {"key": "SC-special", "state": "SC", "seat_class": "III", "vacancy_reason": "appointment"},
    ],
    2018: [
        {"key": "MN-special", "state": "MN", "seat_class": "II", "vacancy_reason": "resignation",
         "dem_nominee": "Tina Smith", "rep_nominee": "Karin Housley"},
        {"key": "MS-special", "state": "MS", "seat_class": "II", "vacancy_reason": "resignation",
         "dem_nominee": "Mike Espy", "rep_nominee": "Cindy Hyde-Smith"},
    ],
    2020: [
        {"key": "AZ-special", "state": "AZ", "seat_class": "III", "vacancy_reason": "death",
         "dem_nominee": "Mark Kelly", "rep_nominee": "Martha McSally"},
        {"key": "GA-special", "state": "GA", "seat_class": "III", "vacancy_reason": "resignation",
         "dem_nominee": "Raphael Warnock", "rep_nominee": "Kelly Loeffler"},
    ],
    2022: [
        {"key": "OK-special", "state": "OK", "seat_class": "II", "vacancy_reason": "resignation"},
        {"key": "CA-special", "state": "CA", "seat_class": "III", "vacancy_reason": "appointment",
         "dem_nominee": "Alex Padilla", "rep_nominee": "Mark Meuser"},
    ],
    2024: [
        {"key": "CA-unexpired", "state": "CA", "seat_class": "I", "term_type": "unexpired",
         "kind": "special", "vacancy_reason": "death",
         "dem_nominee": "Adam Schiff", "rep_nominee": "Steve Garvey"},
        {"key": "NE-unexpired", "state": "NE", "seat_class": "II", "term_type": "unexpired",
         "kind": "special", "vacancy_reason": "resignation",
         "dem_nominee": "Preston Love Jr.", "rep_nominee": "Pete Ricketts"},
    ],
}

CLASS_UP = {2014: "II", 2016: "III", 2018: "I", 2020: "II", 2022: "III", 2024: "I"}
POST_DEM = {2014: 46, 2016: 48, 2018: 47, 2020: 50, 2022: 51, 2024: 47}
POST_CONTROL = {2014: False, 2016: False, 2018: False, 2020: True, 2022: True, 2024: False}
VP = {2014: "D", 2016: "D", 2018: "R", 2020: "D", 2022: "D", 2024: "D"}
SOURCE = {
    2014: "https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/",
    2016: "https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/",
    2018: "https://www.fec.gov/resources/cms-content/documents/federalelections2018.pdf",
    2020: "https://www.fec.gov/resources/cms-content/documents/federalelections2020.pdf",
    2022: "https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/",
    2024: "https://www.fec.gov/resources/cms-content/documents/federalcandidates2024list.pdf",
}

# Independently frozen held caucus among seats NOT UP (audit R-02).
# Computed once from FEC winners + Wikipedia post composition, then locked here —
# warehouse code must not re-solve these from winners at runtime.
HELD = {
    # held_dem = Dem caucus among seats not up (includes I who caucus D)
    2014: {"held_dem": 34, "held_rep": 30, "held_ind": 2},  # 36 contested → 64 held; post 46
    2016: {"held_dem": 36, "held_rep": 30, "held_ind": 2},  # 34 contested → 66 held; post 48
    2018: {"held_dem": 23, "held_rep": 42, "held_ind": 0},  # 35 contested; King/Sanders up → caucus wins; post 47
    2020: {"held_dem": 35, "held_rep": 30, "held_ind": 2},  # 35 contested → 65 held; post 50
    2022: {"held_dem": 35, "held_rep": 29, "held_ind": 2},  # 36 contested (incl CA special) → 64 held; post 51
    2024: {"held_dem": 27, "held_rep": 38, "held_ind": 0},  # 35 contested → 65 held; post 47
}


def _class_states(label: str) -> list[str]:
    return {"I": CLASS_I, "II": CLASS_II, "III": CLASS_III}[label]


def _build_cycle(year: int) -> tuple[dict[str, Any], dict[str, Any]]:
    margins = MARGINS[year]
    up = CLASS_UP[year]
    contests: list[dict[str, Any]] = []
    src = SOURCE[year]
    for st in _class_states(up):
        m = margins[st]
        kwargs: dict[str, Any] = {
            "year": year,
            "state": st,
            "seat_class": up,
            "margin": m,
            "source_url": src,
        }
        if year == 2018 and st == "OH":
            kwargs.pop("margin")
            kwargs.update(
                dem_votes=OH_2018[0],
                rep_votes=OH_2018[1],
                other_votes=OH_2018[2],
                dem_nominee="Sherrod Brown",
                rep_nominee="Jim Renacci",
                incumbent_party="D",
            )
        if year == 2024 and st == "AZ":
            kwargs.pop("margin")
            kwargs.update(
                dem_votes=AZ_2024[0],
                rep_votes=AZ_2024[1],
                other_votes=AZ_2024[2],
                dem_nominee="Ruben Gallego",
                rep_nominee="Kari Lake",
                is_open=True,
            )
        contests.append(_contest(**kwargs))

    for sp in SPECIAL_META.get(year) or []:
        key = sp["key"]
        m = margins[key]
        contests.append(
            _contest(
                year=year,
                state=sp["state"],
                seat_class=sp["seat_class"],
                kind=sp.get("kind") or "special",
                suffix=key,
                margin=m,
                term_type=sp.get("term_type") or ("unexpired" if "unexpired" in key else "full"),
                vacancy_reason=sp.get("vacancy_reason"),
                dem_nominee=sp.get("dem_nominee"),
                rep_nominee=sp.get("rep_nominee"),
                source_url=src if year != 2024 else (
                    "https://azsos.gov" if sp["state"] == "AZ" else src
                ),
            )
        )

    # Margin-only dem_wins undercounts Independent caucus winners (ME/VT); seat math
    # for POST_DEM is validated by chamber_reconcile against truth_v1 winner_caucus.
    held = HELD[year]
    n = len(contests)
    assert held["held_dem"] + held["held_rep"] + n == 100, (year, held, n)

    cycle = {
        "year": year,
        "election_id": f"senate-{year}",
        "seat_class_up": up,
        "election_day": _election_day(year),
        "certified_available_at": f"{year}-11-22" if year != 2020 else "2021-01-06",
        "source_url": src,
        "n_contested": n,
        "contests": contests,
        "note": f"Official contested ballot including specials/unexpired; n={n}",
    }
    # Independent expectations — separate artifact, not generated by contested_contests()
    canaries = []
    if year == 2018:
        canaries.append(
            {
                "race_id": "senate-2018-OH",
                "winner_party": "D",
                "dem_votes": OH_2018[0],
                "rep_votes": OH_2018[1],
                "min_margin_pp": 6.5,
            }
        )
    if year == 2024:
        canaries.append(
            {
                "race_id": "senate-2024-AZ",
                "winner_party": "D",
                "dem_votes": AZ_2024[0],
                "rep_votes": AZ_2024[1],
                "min_margin_pp": 2.0,
            }
        )
    special_ids = [c["race_id"] for c in contests if c["kind"] != "regular" or c.get("term_type") == "unexpired"]
    exp = {
        "year": year,
        "n_contested_expected": n,
        "expected_race_ids": sorted(c["race_id"] for c in contests),
        "expected_special_ids": sorted(special_ids),
        "held_dem": held["held_dem"],
        "held_rep": held["held_rep"],
        "held_ind": held["held_ind"],
        "post_dem_seats": POST_DEM[year],
        "post_dem_control": POST_CONTROL[year],
        "vp_tiebreak_party": VP[year],
        "canaries": canaries,
        "source_note": (
            "Frozen independently of warehouse generators. Held seats are the "
            "pre-election not-up caucus; changing a certified winner must break reconcile."
        ),
    }
    return cycle, exp


def build_and_write() -> dict[str, Any]:
    """DEPRECATED — redirects to truth_v1 builder (v0.9.21).

    Legacy margin-scaled synthetic contests are no longer written.
    """
    from midterms.evidence.build_certified_ledger_v3 import rebuild_canonical_truth

    return rebuild_canonical_truth()


def main() -> None:
    from midterms.evidence.official_ledger import write_ledger_normalized

    summary = build_and_write()
    man = write_ledger_normalized()
    print(json.dumps({"build": summary, "normalized": man}, indent=2, default=str))


if __name__ == "__main__":
    main()
