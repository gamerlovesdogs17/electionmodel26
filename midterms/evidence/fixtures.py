"""Synthetic but realistic Senate evidence fixtures (offline / CI / license-safe).

These fixtures are research synthetics shaped like public historical patterns.
They are NOT official poll releases. Manifests record provenance and sha256 hashes.
Real ingestion hooks live in `midterms.evidence.ingest` for when redistributable
archives are available.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from midterms.config import (
    CYCLES,
    DEMO_AS_OF,
    DEMO_ELECTION_DAY,
    DEMO_ELECTION_ID,
    FIXTURES_DIR,
    MANIFESTS_DIR,
    NORMALIZED_DIR,
    RAW_DIR,
    REGIONS,
)
from midterms.evidence.schema import POLL_COLUMNS, RACE_COLUMNS, RESULT_COLUMNS, align_poll_frame, align_result_frame

PARSER_VERSION = "fixtures-v1"

# Class II states (up in 2026) — public institutional knowledge
CLASS_II = [
    "AL", "AK", "AR", "CO", "DE", "GA", "ID", "IL", "IA", "KS", "KY", "LA",
    "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH", "NJ", "NM", "NC", "OK",
    "OR", "RI", "SC", "SD", "TN", "TX", "VA", "WV", "WY",
]

# 2026 special elections (not Class II) — one seat each; other seat remains held
SPECIALS_2026 = ["OH", "FL"]

CONTESTED_2026 = CLASS_II + SPECIALS_2026

# Approximate long-run leans (dem - rep pp) for synthetic generation
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

POLLSTERS = [
    ("YouGov", 0.4, 2.2),
    ("Quinnipiac", 0.2, 1.8),
    ("Marist", 0.1, 2.0),
    ("Siena", 0.0, 1.7),
    ("Trafalgar", -2.5, 3.5),
    ("Emerson", -0.5, 2.8),
    ("SurveyUSA", 0.3, 2.4),
    ("PPP", 1.2, 3.0),
    ("Data for Progress", 1.5, 2.6),
    ("RMG Research", -1.0, 3.2),
]

# Approximate White House party + net approval by cycle (research fixtures)
CYCLE_CONTEXT = {
    2014: {"white_house_party": "D", "pres_approval": -8.0},
    2016: {"white_house_party": "D", "pres_approval": 4.0},
    2018: {"white_house_party": "R", "pres_approval": -6.0},
    2020: {"white_house_party": "R", "pres_approval": -10.0},
    2022: {"white_house_party": "D", "pres_approval": -12.0},
    2024: {"white_house_party": "D", "pres_approval": -14.0},
    2026: {"white_house_party": "R", "pres_approval": -8.0},
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _election_day(year: int) -> date:
    # First Tuesday after first Monday in November
    d = date(year, 11, 1)
    while d.weekday() != 0:  # Monday
        d += timedelta(days=1)
    return d + timedelta(days=1)


def _states_for_cycle(year: int) -> list[str]:
    """Official contested states for historical cycles (audit P0.1)."""
    try:
        from midterms.evidence.official_ballot import contested_contests

        return [c["state"] for c in contested_contests(year) if c.get("kind") == "regular"]
    except Exception:
        rng = np.random.default_rng(year)
        all_states = sorted(BASE_LEANS)
        if year % 4 == 2:
            return list(CLASS_II)
        others = [s for s in all_states if s not in CLASS_II]
        extra = list(rng.choice(CLASS_II, size=5, replace=False))
        return sorted(set(others + extra))[:34]


def _race_struct_fields(
    year: int,
    lean: float,
    rng: np.random.Generator,
    *,
    election_day: str | None = None,
    vacancy_reason: str | None = None,
    election_phase: str = "general",
) -> dict:
    ctx = CYCLE_CONTEXT.get(year, {"white_house_party": "D", "pres_approval": 0.0})
    # Fundraising share correlates loosely with lean + noise (matched-window proxy)
    base = 1 / (1 + np.exp(-lean / 12.0))
    share = float(np.clip(base + rng.normal(0, 0.08), 0.05, 0.95))
    ed = election_day or _election_day(year).isoformat()
    return {
        "fundraising_share": round(share, 3),
        "pres_approval": float(ctx["pres_approval"]),
        "white_house_party": ctx["white_house_party"],
        "is_midterm": bool(year % 4 == 2),
        "election_phase": election_phase,
        "runoff_of": None,
        "vacancy_reason": vacancy_reason,
        "ballot_status": "nominated",
        "effective_election_day": ed,
    }


def _chamber_held(year: int, contested: list[str], rng: np.random.Generator) -> pd.DataFrame:
    """All 100 seats for chamber composition; contested seats get race rows."""
    rows = []
    # Simple synthetic control of chamber: ~49-51 prior to election
    held = {}
    for st in sorted(BASE_LEANS):
        lean = BASE_LEANS[st]
        # two senators per state with sticky party
        for seat in (1, 2):
            p = 1 / (1 + np.exp(-lean / 10))
            party = "D" if rng.random() < p else "R"
            # Independents caucus with D in ME/VT occasionally
            if st in {"ME", "VT"} and seat == 1 and rng.random() < 0.5:
                party = "I"
            held[(st, seat)] = party

    # Map contested races onto seat 1 for simplicity
    for st in contested:
        race_id = f"senate-{year}-{st}"
        lean = float(BASE_LEANS[st] + rng.normal(0, 2))
        inc = held[(st, 1)]
        is_open = bool(rng.random() < 0.18)
        ed = _election_day(year).isoformat()
        struct = _race_struct_fields(year, lean, rng, election_day=ed)
        rows.append(
            {
                "race_id": race_id,
                "election_id": f"senate-{year}",
                "office": "US_SENATE",
                "state": st,
                "seat_class": "II" if year % 4 == 2 else "I/III",
                "election_day": ed,
                "incumbent_party": None if is_open else ("D" if inc == "I" else inc),
                "is_open": is_open,
                "prior_lean": round(lean, 2),
                "region": REGIONS[st],
                "not_up": False,
                "held_by": "D" if inc == "I" else inc,
                **struct,
            }
        )

    # non-contested seats for chamber carry
    for (st, seat), party in held.items():
        if st in contested and seat == 1:
            continue
        lean = float(BASE_LEANS[st])
        ed = _election_day(year).isoformat()
        struct = _race_struct_fields(year, lean, rng, election_day=ed)
        rows.append(
            {
                "race_id": f"senate-{year}-{st}-held{seat}",
                "election_id": f"senate-{year}",
                "office": "US_SENATE",
                "state": st,
                "seat_class": "held",
                "election_day": ed,
                "incumbent_party": "D" if party == "I" else party,
                "is_open": False,
                "prior_lean": lean,
                "region": REGIONS[st],
                "not_up": True,
                "held_by": "D" if party == "I" else party,
                **struct,
            }
        )
    return pd.DataFrame(rows)[RACE_COLUMNS]


def _generate_cycle(year: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(10_000 + year)
    # Audit P0.1: official class/special ballot when metadata exists
    try:
        from midterms.evidence.official_ballot import CYCLE_META, build_official_races_frame

        if year in CYCLE_META:
            races = build_official_races_frame(year)
        else:
            contested = _states_for_cycle(year)
            races = _chamber_held(year, contested, rng)
    except Exception:
        contested = _states_for_cycle(year)
        races = _chamber_held(year, contested, rng)
    ed = _election_day(year)
    national_env = float(rng.normal(0 if year % 4 else 1.5, 2.5))  # midterm vs prez

    poll_rows: list[dict] = []
    result_rows: list[dict] = []

    contested_races = races[~races["not_up"]].copy()
    for _, race in contested_races.iterrows():
        st = race["state"]
        race_id = race["race_id"]
        lean = float(race["prior_lean"])
        # Election Day truth with national + regional + local shocks
        region_shock = float(rng.normal(0, 1.5))
        local = float(rng.normal(0, 2.5))
        truth = lean + 0.55 * national_env + region_shock + local
        # incumbency bump
        if not race["is_open"] and race["incumbent_party"] == "D":
            truth += 2.0
        elif not race["is_open"] and race["incumbent_party"] == "R":
            truth -= 2.0

        dem_share = 50 + truth / 2
        dem_votes = int(500_000 + abs(lean) * 8_000 + rng.integers(0, 50_000))
        rep_votes = int(dem_votes * (100 - dem_share) / max(dem_share, 1))
        margin = 100 * (dem_votes - rep_votes) / (dem_votes + rep_votes)
        result_rows.append(
            {
                "result_id": f"res-{race_id}",
                "election_id": f"senate-{year}",
                "office": "US_SENATE",
                "state": st,
                "race_id": race_id,
                "event_time": ed.isoformat(),
                "available_at": (ed + timedelta(days=21)).isoformat(),  # certification lag
                "certified_at": (ed + timedelta(days=21)).isoformat(),
                "dem_votes": dem_votes,
                "rep_votes": rep_votes,
                "other_votes": int(rng.integers(1000, 20000)),
                "two_party_margin": round(margin, 3),
                "winner_party": "D" if margin > 0 else "R",
                "winner_caucus": "D" if margin > 0 else "R",
                "modeled_side": "D" if margin > 0 else "R",
                "stage": "general",
                "certification_status": "certified",
                "source_url": "synthetic://fixtures/certified-results",
                "raw_hash": "",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "release_version": 1,
            }
        )

        # Polls from ED-150 to ED-1 — occasionally flood one prolific pollster / shared study
        n_polls = int(rng.integers(4, 18))
        flood_pollster = POLLSTERS[int(rng.integers(0, len(POLLSTERS)))]
        shared_study_end = ed - timedelta(days=int(rng.integers(20, 60)))
        for j in range(n_polls):
            days_out = int(rng.integers(1, 150))
            field_end = ed - timedelta(days=days_out)
            field_start = field_end - timedelta(days=int(rng.integers(2, 5)))
            published = field_end + timedelta(days=int(rng.integers(0, 2)))
            if j < 4 and rng.random() < 0.7:
                pollster, house, rel = flood_pollster
                # Same study series / tracker reuse → clustered in ENOP
                study_id = f"study-{year}-{st}-{pollster}-tracker"
                field_end = shared_study_end - timedelta(days=j)
                field_start = field_end - timedelta(days=3)
                published = field_end + timedelta(days=1)
            else:
                pollster, house, rel = POLLSTERS[int(rng.integers(0, len(POLLSTERS)))]
                study_id = f"study-{year}-{st}-{pollster}-{field_end.isoformat()}"
            # Opinion path: shrink toward truth as Election Day approaches
            progress = 1 - days_out / 150
            latent = lean + (truth - lean) * (0.3 + 0.7 * progress) + rng.normal(0, 1.2)
            n = int(rng.choice([400, 500, 600, 750, 900, 1200]))
            sampling = rng.normal(0, 100 / np.sqrt(n))
            observed = latent + house + sampling + rng.normal(0, rel)
            dem = 50 + observed / 2 + rng.normal(0, 0.3)
            rep = 100 - dem - abs(rng.normal(2, 1))
            total = dem + rep
            dem_tw = 100 * dem / total
            rep_tw = 100 * rep / total
            margin_obs = dem_tw - rep_tw
            poll_id = f"poll-{year}-{st}-{j}-{days_out}"
            payload = f"{poll_id}|{margin_obs}|{n}".encode()
            poll_rows.append(
                {
                    "poll_id": poll_id,
                    "study_id": study_id,
                    "release_version": 1,
                    "pollster_id": pollster,
                    "sponsor_id": "none",
                    "source_url": "synthetic://fixtures/polls",
                    "raw_hash": _sha256_bytes(payload),
                    "field_start": field_start.isoformat(),
                    "field_end": field_end.isoformat(),
                    "published_at": published.isoformat(),
                    "corrected_at": None,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "valid_from": published.isoformat(),
                    "valid_to": None,
                    "available_at": published.isoformat(),
                    "event_time": field_end.isoformat(),
                    "election_id": f"senate-{year}",
                    "office": "US_SENATE",
                    "state": st,
                    "race_id": race_id,
                    "population": str(rng.choice(["LV", "LV", "RV"])),
                    "sample_size": n,
                    "mode": str(rng.choice(["Online", "Live Phone", "IVR/Online"])),
                    "dem_share": round(dem_tw, 2),
                    "rep_share": round(rep_tw, 2),
                    "undecided": round(abs(rng.normal(4, 1.5)), 2),
                    "other_share": round(abs(rng.normal(1, 0.5)), 2),
                    "two_party_margin": round(margin_obs, 3),
                    "partisan": False,
                    "exclusion_status": "include",
                    "exclusion_reason": None,
                    "parser_version": PARSER_VERSION,
                    "normalized_at": datetime.now(timezone.utc).isoformat(),
                    "supersedes": None,
                }
            )

    polls = align_poll_frame(pd.DataFrame(poll_rows))
    results = align_result_frame(pd.DataFrame(result_rows))
    results["raw_hash"] = results.apply(
        lambda r: _sha256_bytes(f"{r.result_id}|{r.two_party_margin}".encode()), axis=1
    )
    return races, polls, results


def generate_2026_races(rng: np.random.Generator | None = None) -> pd.DataFrame:
    """Build research map: 33 Class II + 2 specials contested + 65 held (=100)."""
    rng = rng or np.random.default_rng(2026)
    rows = []
    # Contested Class II — illustrative incumbency / openings for research demos
    incumbency = {
        "AL": "R", "AK": "R", "AR": "R", "CO": "D", "DE": "D", "GA": "D", "ID": "R",
        "IL": "D", "IA": "R", "KS": "R", "KY": "R", "LA": "R", "ME": "R", "MA": "D",
        "MI": "D", "MN": "D", "MS": "R", "MT": "R", "NE": "R", "NH": "D", "NJ": "D",
        "NM": "D", "NC": "R", "OK": "R", "OR": "D", "RI": "D", "SC": "R", "SD": "R",
        "TN": "R", "TX": "R", "VA": "D", "WV": "R", "WY": "R",
        # Specials: seats vacated / appointed mid-cycle
        "OH": "R", "FL": "R",
    }
    open_seats = {"AL", "IL", "IA", "KY", "MI", "MN", "MT", "NH", "NC", "OK", "SC", "WY"}
    # Soften deep leans so joint chamber totals have mass near majority
    for st in CLASS_II:
        is_open = st in open_seats
        raw = float(BASE_LEANS[st])
        lean = float(np.clip(raw * 0.55, -18, 18) + rng.normal(0, 0.8))
        struct = _race_struct_fields(2026, lean, rng, election_day=DEMO_ELECTION_DAY)
        rows.append(
            {
                "race_id": f"senate-2026-{st}",
                "election_id": DEMO_ELECTION_ID,
                "office": "US_SENATE",
                "state": st,
                "seat_class": "II",
                "election_day": DEMO_ELECTION_DAY,
                "incumbent_party": None if is_open else incumbency[st],
                "is_open": is_open,
                "prior_lean": round(lean, 2),
                "region": REGIONS[st],
                "not_up": False,
                "held_by": incumbency[st],
                **struct,
            }
        )

    for st in SPECIALS_2026:
        raw = float(BASE_LEANS[st])
        lean = float(np.clip(raw * 0.55, -18, 18) + rng.normal(0, 0.8))
        struct = _race_struct_fields(
            2026,
            lean,
            rng,
            election_day=DEMO_ELECTION_DAY,
            vacancy_reason="appointment",
            election_phase="special",
        )
        rows.append(
            {
                "race_id": f"senate-2026-{st}",
                "election_id": DEMO_ELECTION_ID,
                "office": "US_SENATE",
                "state": st,
                "seat_class": "special",
                "election_day": DEMO_ELECTION_DAY,
                "incumbent_party": incumbency[st],
                "is_open": False,
                "prior_lean": round(lean, 2),
                "region": REGIONS[st],
                "not_up": False,
                "held_by": incumbency[st],
                **struct,
            }
        )

    # Held senators not on the 2026 ballot — 65 seats (100 - 35 contested).
    # Independents who caucus with Democrats: ME (King), VT (Sanders).
    # Welch (VT) remains D. Parties are assigned by held_meta index below.
    held_meta: list[str] = []
    for st in CLASS_II:
        held_meta.append(st)
    for st in SPECIALS_2026:
        held_meta.append(st)  # one remaining seat each
    for st in sorted(s for s in BASE_LEANS if s not in CLASS_II and s not in SPECIALS_2026):
        held_meta.extend([st, st])
    assert len(held_meta) == 65, len(held_meta)

    # Curated caucus roster among held seats (~D+I majority carry into midterm)
    # Mark specific Ind caucus seats by (state, occurrence) then fill D then R.
    held_parties: list[str | None] = [None] * 65
    # First VT occurrence → Sanders (I); second → Welch (D)
    vt_ix = [i for i, s in enumerate(held_meta) if s == "VT"]
    if len(vt_ix) >= 1:
        held_parties[vt_ix[0]] = "I"
    if len(vt_ix) >= 2:
        held_parties[vt_ix[1]] = "D"
    # ME Class II held seat → King (I)
    me_ix = [i for i, s in enumerate(held_meta) if s == "ME"]
    if me_ix:
        held_parties[me_ix[0]] = "I"

    # Remaining D caucus targets (excluding already-set I/D)
    preferred_d = [
        "AZ", "AZ", "CA", "CA", "CT", "CT", "HI", "HI", "MD", "MD", "NV", "NV",
        "NY", "NY", "PA", "PA", "WA", "WA", "WI", "WI",
        "CO", "DE", "GA", "IL", "MA", "NJ", "NM", "OR", "RI", "VA", "MI", "MN", "NH",
    ]
    # Count already assigned D+I
    n_caucus = sum(1 for p in held_parties if p in {"D", "I"})
    target_caucus = 38  # of 65 held → competitive chamber with 35 contested
    for st in preferred_d:
        if n_caucus >= target_caucus:
            break
        for i, s in enumerate(held_meta):
            if s == st and held_parties[i] is None:
                held_parties[i] = "D"
                n_caucus += 1
                break
    for i in range(65):
        if held_parties[i] is None:
            held_parties[i] = "R"

    for i, st in enumerate(held_meta):
        party = held_parties[i] or "R"
        lean = float(BASE_LEANS[st])
        struct = _race_struct_fields(2026, lean, rng, election_day=DEMO_ELECTION_DAY)
        rows.append(
            {
                "race_id": f"senate-2026-{st}-held-{i}",
                "election_id": DEMO_ELECTION_ID,
                "office": "US_SENATE",
                "state": st,
                "seat_class": "held",
                "election_day": DEMO_ELECTION_DAY,
                "incumbent_party": party,
                "is_open": False,
                "prior_lean": lean,
                "region": REGIONS[st],
                "not_up": True,
                "held_by": party,
                **struct,
            }
        )
    df = pd.DataFrame(rows)[RACE_COLUMNS]
    assert int((~df["not_up"]).sum()) == 35
    assert int(df["not_up"].sum()) == 65
    assert len(df) == 100
    return df


def generate_2026_polls(races: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(20260901)
    as_of = date.fromisoformat(DEMO_AS_OF)
    ed = date.fromisoformat(DEMO_ELECTION_DAY)
    contested = races[~races["not_up"]]
    rows = []
    national = 0.5  # near-parity national environment for a competitive research demo
    for _, race in contested.iterrows():
        st = race["state"]
        truthish = float(race["prior_lean"]) + 0.5 * national
        if not race["is_open"] and race["incumbent_party"] == "D":
            truthish += 1.5
        elif not race["is_open"] and race["incumbent_party"] == "R":
            truthish -= 1.5
        n_polls = int(rng.integers(3, 12))
        for j in range(n_polls):
            days_out = int(rng.integers((ed - as_of).days, 120))
            field_end = ed - timedelta(days=days_out)
            if field_end > as_of:
                field_end = as_of - timedelta(days=int(rng.integers(0, 5)))
            field_start = field_end - timedelta(days=3)
            published = min(field_end + timedelta(days=1), as_of)
            pollster, house, rel = POLLSTERS[int(rng.integers(0, len(POLLSTERS)))]
            n = int(rng.choice([500, 600, 800, 1000]))
            obs = truthish + house + rng.normal(0, rel) + rng.normal(0, 100 / np.sqrt(n))
            dem_tw = 50 + obs / 2
            rep_tw = 100 - dem_tw
            margin = dem_tw - rep_tw
            poll_id = f"poll-2026-{st}-{j}"
            payload = f"{poll_id}|{margin}|{n}".encode()
            rows.append(
                {
                    "poll_id": poll_id,
                    "study_id": f"study-2026-{st}-{pollster}-{field_end}",
                    "release_version": 1,
                    "pollster_id": pollster,
                    "sponsor_id": "none",
                    "source_url": "synthetic://fixtures/polls-2026",
                    "raw_hash": _sha256_bytes(payload),
                    "field_start": field_start.isoformat(),
                    "field_end": field_end.isoformat(),
                    "published_at": published.isoformat(),
                    "corrected_at": None,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "valid_from": published.isoformat(),
                    "valid_to": None,
                    "available_at": published.isoformat(),
                    "event_time": field_end.isoformat(),
                    "election_id": DEMO_ELECTION_ID,
                    "office": "US_SENATE",
                    "state": st,
                    "race_id": race["race_id"],
                    "population": "LV",
                    "sample_size": n,
                    "mode": "Online",
                    "dem_share": round(float(dem_tw), 2),
                    "rep_share": round(float(rep_tw), 2),
                    "undecided": 5.0,
                    "other_share": 1.0,
                    "two_party_margin": round(float(margin), 3),
                    "partisan": False,
                    "exclusion_status": "include",
                    "exclusion_reason": None,
                    "parser_version": PARSER_VERSION,
                    "normalized_at": datetime.now(timezone.utc).isoformat(),
                    "supersedes": None,
                }
            )
    return align_poll_frame(pd.DataFrame(rows))


def build_fixtures(root: Path | None = None) -> dict[str, str]:
    """Write immutable raw + normalized fixtures and a provenance manifest."""
    root = root or FIXTURES_DIR
    raw = RAW_DIR
    norm = NORMALIZED_DIR
    man = MANIFESTS_DIR
    for p in (root, raw, norm, man):
        p.mkdir(parents=True, exist_ok=True)

    all_races = []
    all_polls = []
    all_results = []
    for year in CYCLES:
        races, polls, results = _generate_cycle(year)
        all_races.append(races)
        all_polls.append(polls)
        all_results.append(results)

    races_2026 = generate_2026_races()
    polls_2026 = generate_2026_polls(races_2026)
    all_races.append(races_2026)
    all_polls.append(polls_2026)

    races_df = pd.concat(all_races, ignore_index=True)
    polls_df = pd.concat(all_polls, ignore_index=True)
    results_df = pd.concat(all_results, ignore_index=True)

    from midterms.evidence.results_archive import merge_certified_into_results, write_results_archive
    from midterms.evidence.historical_polls import (
        freeze_historical_polls_from_warehouse,
        inject_correction_versions,
    )
    from midterms.evidence.approval import write_approval_store

    write_results_archive()
    results_df = merge_certified_into_results(results_df)
    # Prefer official historical ballots in the normalized races store
    try:
        from midterms.evidence.official_ballot import (
            merge_official_into_races,
            write_official_ballot_store,
        )

        write_official_ballot_store()
        races_df = merge_official_into_races(races_df)
    except Exception as exc:  # noqa: BLE001
        provenance_extra = {"official_ballot_error": str(exc)}
    else:
        provenance_extra = {"official_ballot": True}
    polls_df = inject_correction_versions(polls_df)
    write_approval_store()

    paths = {}
    for name, df in [
        ("races", races_df),
        ("polls", polls_df),
        ("results", results_df),
    ]:
        raw_path = raw / f"{name}.csv"
        norm_path = norm / f"{name}.parquet"
        csv_bytes = df.to_csv(index=False).encode()
        raw_path.write_bytes(csv_bytes)
        df.to_parquet(norm_path, index=False)
        paths[name] = {
            "raw": str(raw_path.relative_to(raw_path.parents[1])),
            "normalized": str(norm_path.relative_to(norm_path.parents[1])),
            "sha256": _sha256_bytes(csv_bytes),
            "n_rows": int(len(df)),
        }

    provenance = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "license": "synthetic-research-fixture",
        "note": (
            "Synthetic Senate polls/results for offline CI and blueprint-aligned development. "
            "Not for publication as real forecasts. Replace via midterms.evidence.ingest when "
            "redistributable archives (MEDSL / public poll dumps / certified results) are available."
        ),
        "preferred_live_sources": [
            "MIT Election Lab / MEDSL research layer (results)",
            "data/raw/external/senate_certified_results.json (curated certified margins)",
            "Public poll archives with redistributable licenses (polls)",
            "Official state certification / FEC (results)",
        ],
        "cycles": list(CYCLES) + [2026],
        "parser_version": PARSER_VERSION,
        "artifacts": paths,
        **provenance_extra,
    }
    prov_path = man / "fixtures_provenance.json"
    prov_path.write_text(json.dumps(provenance, indent=2))
    # also copy tidy CSVs into fixtures for easy inspection
    races_df.to_csv(root / "races.csv", index=False)
    polls_df.to_csv(root / "polls.csv", index=False)
    results_df.to_csv(root / "results.csv", index=False)

    # Freeze sealed historical poll archive after write
    freeze_historical_polls_from_warehouse(polls_df)

    return {"provenance": str(prov_path), **{k: v["normalized"] for k, v in paths.items()}}


if __name__ == "__main__":
    print(build_fixtures())
