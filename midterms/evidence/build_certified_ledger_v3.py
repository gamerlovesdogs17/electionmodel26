"""Build vote-count ledger from hashed FTE Senate results CSV (truth_v1).

Primary machine-readable candidate totals with per-row state SOS source URLs.
Known FEC/state canvass overrides applied for audited canaries.
Expectations are written from a separate seat-roster file only (held seats),
with independently curated post-election seat totals and canaries.

Wikipedia ``certified_vote_counts.json`` is quarantined and never read here.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import RAW_DIR
from midterms.evidence.official_ballot import CLASS_I, CLASS_II, CLASS_III
from midterms.evidence.official_ledger import EXPECTATIONS_PATH, LEDGER_PATH
from midterms.evidence.truth_contract import (
    SCHEMA_VERSION,
    caucus_for_winner,
    classify_row_role,
    normalize_expectation_cycle,
)

CERT_DIR = RAW_DIR / "external" / "certified"
FTE_CSV = CERT_DIR / "fte_senate.csv"
ROSTER_PATH = RAW_DIR / "external" / "pre_election_seat_rosters.json"
CANDIDATE_LEDGER_PATH = RAW_DIR / "external" / "official_senate_candidates.json"

# Exact canvass overrides (FEC Federal Elections / state SOS).
# Temporal fields for runoffs: event date = decisive stage; available_at = day-after
# as conservative first-public bound when exact release timestamp is undocumented;
# certified_at stays null unless a certification record is archived.
CANVASS_OVERRIDES: dict[str, dict[str, Any]] = {
    "senate-2014-LA": {
        "dem_votes": 561_210,
        "rep_votes": 712_379,
        "other_votes": 0,
        "stage": "runoff",
        "election_day": "2014-12-06",
        "available_at": "2014-12-07",
        "certified_at": None,
        "certification_status": "public_canvass_unproven",
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2014.pdf",
        "truth_tier": "fec_canvass_transcribed",
        "dem_nominee": "Mary L. Landrieu",
        "rep_nominee": "Bill Cassidy",
        "winner_party": "R",
    },
    "senate-2018-OH": {
        "dem_votes": 2_358_508,
        "rep_votes": 2_057_559,
        "other_votes": 1_017,
        "stage": "general",
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2018.pdf",
        "truth_tier": "fec_canvass_transcribed",
        "certification_status": "public_canvass_unproven",
        "certified_at": None,
    },
    "senate-2020-GA-special": {
        "dem_votes": 2_289_113,
        "rep_votes": 2_195_841,
        "other_votes": 0,
        "stage": "runoff",
        "election_day": "2021-01-05",
        "available_at": "2021-01-06",
        "certified_at": None,
        "certification_status": "public_canvass_unproven",
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2020.pdf",
        "truth_tier": "fec_canvass_transcribed",
        "dem_nominee": "Raphael Warnock",
        "rep_nominee": "Kelly Loeffler",
        "winner_party": "D",
    },
    "senate-2022-OK": {
        "other_votes": 41_402,
        "stage": "general",
        "truth_tier": "fec_canvass_transcribed",
        "certification_status": "public_canvass_unproven",
        "certified_at": None,
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2022.pdf",
    },
    "senate-2022-OK-special": {
        "other_votes": 34_449,
        "stage": "general",
        "truth_tier": "fec_canvass_transcribed",
        "certification_status": "public_canvass_unproven",
        "certified_at": None,
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2022.pdf",
    },
    "senate-2022-CA": {
        "dem_votes": 6_621_621,
        "rep_votes": 4_222_029,
        "other_votes": 0,
        "stage": "general",
        "truth_tier": "fec_canvass_transcribed",
        "certification_status": "public_canvass_unproven",
        "certified_at": None,
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2022.pdf",
    },
    "senate-2022-CA-unexpired": {
        "dem_votes": 6_559_308,
        "rep_votes": 4_212_450,
        "other_votes": 0,
        "stage": "general",
        "truth_tier": "fec_canvass_transcribed",
        "certification_status": "public_canvass_unproven",
        "certified_at": None,
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2022.pdf",
        "dem_nominee": "Alex Padilla",
        "rep_nominee": "Mark P. Meuser",
        "winner_party": "D",
    },
    "senate-2024-AZ": {
        "dem_votes": 1_676_335,
        "rep_votes": 1_595_761,
        "other_votes": 75_868,
        "stage": "general",
        "source_url": "https://azsos.gov/elections/election-information/2024-election-info",
        "truth_tier": "state_canvass_transcribed",
        "certification_status": "public_canvass_unproven",
        "certified_at": None,
    },
}

# Decisive-stage calendars (FEC / state). available_at = day after event when
# exact first-public timestamp is not archived (fail-closed vs pre-event leakage).
RUNOFF_STAGE_CALENDAR: dict[str, dict[str, Any]] = {
    "senate-2014-LA": {
        "election_day": "2014-12-06",
        "available_at": "2014-12-07",
        "certified_at": None,
        "stage": "runoff",
    },
    "senate-2016-LA": {
        "election_day": "2016-12-10",
        "available_at": "2016-12-11",
        "certified_at": None,
        "stage": "runoff",
    },
    "senate-2018-MS-special": {
        "election_day": "2018-11-27",
        "available_at": "2018-11-28",
        "certified_at": None,
        "stage": "runoff",
    },
    "senate-2020-GA": {
        "election_day": "2021-01-05",
        "available_at": "2021-01-06",
        "certified_at": None,
        "stage": "runoff",
    },
    "senate-2020-GA-special": {
        "election_day": "2021-01-05",
        "available_at": "2021-01-06",
        "certified_at": None,
        "stage": "runoff",
    },
    "senate-2022-GA": {
        "election_day": "2022-12-06",
        "available_at": "2022-12-07",
        "certified_at": None,
        "stage": "runoff",
    },
}

# Independently curated post-election Dem caucus seats (not solved at reconcile time).
POST_DEM = {2014: 46, 2016: 48, 2018: 47, 2020: 50, 2022: 51, 2024: 47}
POST_CONTROL = {2014: False, 2016: False, 2018: False, 2020: True, 2022: True, 2024: False}
VP = {2014: "D", 2016: "D", 2018: "R", 2020: "D", 2022: "D", 2024: "D"}

CLASS_BY_YEAR = {
    2014: "II",
    2016: "III",
    2018: "I",
    2020: "II",
    2022: "III",
    2024: "I",
}
CLASS_STATES = {"I": set(CLASS_I), "II": set(CLASS_II), "III": set(CLASS_III)}


def _party_bucket(ballot_party: object, party: object) -> str:
    for raw in (ballot_party, party):
        p = str(raw or "").upper()
        if p.startswith("DEM") or p in {"D", "DEM"}:
            return "D"
        if p.startswith("REP") or p in {"R", "REP", "GOP"}:
            return "R"
        if p.startswith("IND") or p in {"I", "IND", "INDEPENDENT"}:
            return "I"
    return "O"


def _race_key(year: int, state: str, special: bool, seat_name: str) -> tuple[str, str]:
    """Return (race_id, kind)."""
    seat = str(seat_name or "")
    if year == 2024 and special and state == "CA":
        return "senate-2024-CA-unexpired", "unexpired"
    if year == 2024 and special and state == "NE":
        return "senate-2024-NE-unexpired", "unexpired"
    if year == 2022 and special and state == "CA":
        return "senate-2022-CA-unexpired", "unexpired"
    if special or "special" in seat.lower():
        return f"senate-{year}-{state}-special", "special"
    if "unexpired" in seat.lower() or "short" in seat.lower():
        return f"senate-{year}-{state}-unexpired", "unexpired"
    return f"senate-{year}-{state}", "regular"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _election_day(year: int) -> str:
    # First Tuesday after first Monday in November — approximate fixed dates used elsewhere.
    return {
        2014: "2014-11-04",
        2016: "2016-11-08",
        2018: "2018-11-06",
        2020: "2020-11-03",
        2022: "2022-11-08",
        2024: "2024-11-05",
    }.get(year, f"{year}-11-01")


def build_from_fte(years: tuple[int, ...] = (2014, 2016, 2018, 2020, 2022, 2024)) -> dict[str, Any]:
    if not FTE_CSV.exists():
        raise FileNotFoundError(FTE_CSV)
    source_hash = _sha(FTE_CSV)
    df = pd.read_csv(FTE_CSV)
    df = df[df["cycle"].isin(years)].copy()
    stage = df["stage"].astype(str).str.lower()
    # Prefer decisive stages: runoff > general > jungle primary (LA majority wins).
    df = df[stage.isin({"general", "runoff", "jungle primary"})].copy()
    df["_stage_rank"] = stage.map({"runoff": 3, "general": 2, "jungle primary": 1}).fillna(0)
    key_cols = ["cycle", "state_abbrev", "special", "office_seat_name"]
    max_rank = df.groupby(key_cols, dropna=False)["_stage_rank"].transform("max")
    df = df[df["_stage_rank"] == max_rank]
    if "ranked_choice_round" in df.columns:
        df["_rcv"] = pd.to_numeric(df["ranked_choice_round"], errors="coerce")
        max_rcv = df.groupby(key_cols, dropna=False)["_rcv"].transform("max")
        keep = df["_rcv"].isna() | (df["_rcv"] == max_rcv) | (max_rcv.isna())
        df = df[keep]

    candidate_rows: list[dict[str, Any]] = []
    contests_by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for keys, g in df.groupby(key_cols, dropna=False):
        year, state, special, seat_name = keys
        year = int(year)
        state = str(state).upper()
        special_b = bool(special) if not (isinstance(special, float) and pd.isna(special)) else False
        race_id, kind = _race_key(year, state, special_b, str(seat_name))
        seat_class = CLASS_BY_YEAR.get(year, "?")
        if kind == "special" and year == 2018 and state in {"MN", "MS"}:
            seat_class = "II"
        if kind == "special" and year == 2020 and state in {"AZ", "GA"}:
            seat_class = "III"
        if kind in {"special", "unexpired"} and year == 2024 and state == "CA":
            seat_class = "I"
        if kind in {"special", "unexpired"} and year == 2024 and state == "NE":
            seat_class = "II"
        if kind == "unexpired" and year == 2022 and state == "CA":
            seat_class = "III"

        stage_label = str(g["_stage_rank"].map({3: "runoff", 2: "general", 1: "jungle_primary"}).iloc[0])

        dem_v = rep_v = oth_v = 0
        dem_name = rep_name = None
        winner_name = None
        winner_ballot = None
        parties_present: set[str] = set()
        sources: list[str] = []
        cand_vote_pairs: list[tuple[str, str, int]] = []

        for _, row in g.iterrows():
            name = str(row.get("candidate_name") or "") or None
            role = classify_row_role(name)
            if role == "meta":
                continue
            votes = int(round(float(row["votes"]))) if pd.notna(row.get("votes")) else 0
            bucket = _party_bucket(row.get("ballot_party"), row.get("party"))
            parties_present.add(bucket)
            if role == "write_in":
                oth_v += votes
                continue
            if name:
                cand_vote_pairs.append((name, bucket, votes))
            if bucket == "D":
                dem_v += votes
                if name and (dem_name is None or votes > 0):
                    dem_name = name
            elif bucket == "R":
                rep_v += votes
                if name and (rep_name is None or votes > 0):
                    rep_name = name
            else:
                oth_v += votes
            if bool(row.get("winner")) if pd.notna(row.get("winner")) else False:
                winner_name = name
                winner_ballot = bucket
            src = str(row.get("source") or "")
            if src:
                sources.append(src)
            candidate_rows.append(
                {
                    "race_id": race_id,
                    "election_year": year,
                    "state": state,
                    "candidate_name": name,
                    "ballot_party": row.get("ballot_party"),
                    "party_bucket": bucket,
                    "row_role": role,
                    "votes": votes,
                    "winner": bool(row.get("winner")) if pd.notna(row.get("winner")) else False,
                    "source_url": src,
                    "fte_race_id": row.get("race_id"),
                    "stage": stage_label,
                }
            )

        same_party = False
        top = sorted(cand_vote_pairs, key=lambda x: x[2], reverse=True)[:2]
        if len(top) >= 2 and top[0][1] == top[1][1] and top[0][1] in {"D", "R"}:
            same_party = True
            # Modeled margin among same-party finalists; keep caucus = that party.
            w_votes, r_votes = top[0][2], top[1][2]
            if top[0][1] == "D":
                dem_v, rep_v = w_votes, 0
                oth_v = r_votes
                dem_name, rep_name = top[0][0], None
            else:
                dem_v, rep_v = 0, w_votes
                oth_v = r_votes
                dem_name, rep_name = None, top[0][0]
            winner_name = top[0][0]
            winner_ballot = top[0][1]

        total = dem_v + rep_v
        if same_party and oth_v > 0 and total == dem_v + rep_v:
            # Margin vs same-party runner-up stored in other_votes.
            margin_den = dem_v + oth_v if dem_v else rep_v + oth_v
            lead = dem_v if dem_v else rep_v
            margin = 100.0 * (lead - oth_v) / margin_den if margin_den else 0.0
        else:
            margin = (100.0 * (dem_v - rep_v) / total) if total else 0.0

        if winner_ballot in {"D", "R", "I"}:
            winner = winner_ballot if winner_ballot != "I" else "I"
        elif dem_v > rep_v:
            winner = "D"
        elif rep_v > dem_v:
            winner = "R"
        else:
            winner = "T"

        winner_caucus = caucus_for_winner(winner_party=winner, winner_name=winner_name)
        modeled_side = winner_caucus if winner_caucus in {"D", "R"} else (
            "D" if dem_v >= rep_v else "R"
        )

        multiway = {
            "same_party_general": same_party,
            "no_dem_nominee": dem_v == 0 or dem_name is None,
            "no_rep_nominee": rep_v == 0 or rep_name is None,
            "has_other_votes": oth_v > 0,
            "rcv": bool(g["ranked_choice_round"].notna().any())
            if "ranked_choice_round" in g.columns
            else False,
        }

        available_at = f"{year}-11-22"
        contest = {
            "race_id": race_id,
            "state": state,
            "seat_class": seat_class,
            "kind": kind,
            "term_type": "unexpired" if kind == "unexpired" else "full",
            "stage": stage_label,
            "election_day": _election_day(year),
            "available_at": available_at,
            "certified_at": None,
            "dem_votes": dem_v,
            "rep_votes": rep_v,
            "other_votes": oth_v,
            "two_party_margin": round(margin, 4),
            "winner_party": winner,
            "winner_caucus": winner_caucus,
            "modeled_side": modeled_side,
            "winner_name": winner_name,
            "dem_nominee": dem_name,
            "rep_nominee": rep_name,
            "certification_status": "fte_mediated_unproven",
            "source_object_hash": source_hash,
            "source_url": sources[0] if sources else "",
            "source_urls": sorted(set(sources))[:5],
            "truth_tier": "state_sos_via_fte_csv",
            "multiway": multiway,
            "scaled_synthetic": False,
            "schema_version": SCHEMA_VERSION,
        }
        # Apply decisive-stage calendar before vote overrides so event dates win.
        if race_id in RUNOFF_STAGE_CALENDAR:
            for k, v in RUNOFF_STAGE_CALENDAR[race_id].items():
                contest[k] = v
            contest["certification_status"] = "public_canvass_unproven"
        if race_id in CANVASS_OVERRIDES:
            ov = CANVASS_OVERRIDES[race_id]
            for k, v in ov.items():
                contest[k] = v
            tot = int(contest["dem_votes"]) + int(contest["rep_votes"])
            if tot > 0 and not contest.get("multiway", {}).get("same_party_general"):
                # Provisional D−R; annotate_margin_semantics may clear/replace.
                contest["two_party_margin"] = round(
                    100.0 * (contest["dem_votes"] - contest["rep_votes"]) / tot, 4
                )
            if "winner_party" not in ov:
                contest["winner_party"] = (
                    "D" if contest["dem_votes"] > contest["rep_votes"] else "R"
                )
            contest["winner_caucus"] = caucus_for_winner(
                winner_party=str(contest["winner_party"]),
                winner_name=contest.get("winner_name") or contest.get("dem_nominee")
                if contest["winner_party"] == "D"
                else contest.get("rep_nominee"),
            )
            contest["modeled_side"] = contest["winner_caucus"]
            contest["truth_tier"] = ov.get("truth_tier", contest["truth_tier"])
            ov_hash_payload = {
                k: v
                for k, v in ov.items()
                if k
                not in {
                    "source_object_hash",
                    "discovery_source_hash",
                    "override_note",
                }
            }
            contest["discovery_source_hash"] = source_hash
            contest["source_object_hash"] = hashlib.sha256(
                json.dumps(ov_hash_payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            contest["override_note"] = "manual_canvass_transcription"
        from midterms.evidence.score_targets import annotate_margin_semantics

        contest = annotate_margin_semantics(contest)
        contests_by_year[str(year)].append(contest)

    for y, rows in contests_by_year.items():
        by_id: dict[str, dict[str, Any]] = {}
        for r in rows:
            rid = r["race_id"]
            if rid not in by_id or (r["dem_votes"] + r["rep_votes"]) > (
                by_id[rid]["dem_votes"] + by_id[rid]["rep_votes"]
            ):
                by_id[rid] = r
        contests_by_year[y] = sorted(by_id.values(), key=lambda x: x["race_id"])

    ledger = {
        "parser_version": "official-ledger-v3-fte-truth_v1",
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(FTE_CSV.as_posix()),
        "source_sha256": source_hash,
        "note": (
            "Candidate totals from hashed FiveThirtyEight election_results_senate.csv "
            "(per-row state SOS URLs). Decisive stage = runoff > general > jungle. "
            "Canvass overrides for audited canaries. Wikipedia certified_vote_counts.json "
            "is quarantined and never read."
        ),
        "cycles": {},
    }
    for y in years:
        rows = contests_by_year.get(str(y), [])
        ledger["cycles"][str(y)] = {
            "election_id": f"senate-{y}",
            "n_contests": len(rows),
            "contests": rows,
            "source_url": "https://github.com/fivethirtyeight/election-results",
            "certified_available_at": (
                "2021-01-06" if y == 2020 else f"{y}-11-22"
            ),
        }

    cand_payload = {
        "parser_version": "candidate-ledger-v1",
        "schema_version": SCHEMA_VERSION,
        "source_sha256": source_hash,
        "n": len(candidate_rows),
        "rows": candidate_rows,
    }
    CANDIDATE_LEDGER_PATH.write_text(json.dumps(cand_payload, indent=2), encoding="utf-8")
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return {
        "ledger": str(LEDGER_PATH),
        "candidates": str(CANDIDATE_LEDGER_PATH),
        "n_contests": sum(len(v["contests"]) for v in ledger["cycles"].values()),
        "schema_version": SCHEMA_VERSION,
    }


def write_expectations_from_roster() -> dict[str, Any]:
    """Held seats from roster; post seats / canaries independently curated.

    The roster must already exist as a separately curated artifact — never
    regenerated from the result ledger under test (audit P2).
    """
    if not ROSTER_PATH.exists():
        raise FileNotFoundError(
            f"missing independent seat roster {ROSTER_PATH}; "
            "curate held seats / expected_race_ids offline — do not derive from ledger"
        )
    roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    # Migrate CA-special → CA-unexpired naming only (no inventing contests).
    for y, block in roster.get("cycles", {}).items():
        for key in ("expected_race_ids", "contested_race_ids"):
            if key in block:
                block[key] = [
                    "senate-2022-CA-unexpired" if rid == "senate-2022-CA-special" else rid
                    for rid in block[key]
                ]
        if "held_dem" not in block or "held_rep" not in block:
            raise ValueError(
                f"roster cycle {y} missing held_dem/held_rep — curate offline; "
                "refusing to invent held seats from ledger constants"
            )
        if not (
            block.get("expected_race_ids") or block.get("contested_race_ids")
        ):
            raise ValueError(
                f"roster cycle {y} missing expected_race_ids — do not derive from ledger"
            )
    # Persist naming migration only; never rewrite held counts from results.
    ROSTER_PATH.write_text(json.dumps(roster, indent=2), encoding="utf-8")

    expectations = {
        "parser_version": "chamber-expectations-v4-truth_v1",
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(ROSTER_PATH.as_posix()),
        "source_sha256": _sha(ROSTER_PATH),
        "note": (
            "Held seats from pre_election_seat_rosters.json. "
            "post_dem_seats/canaries are independently curated chamber facts."
        ),
        "cycles": {},
    }
    for y, block in roster.get("cycles", {}).items():
        yi = int(y)
        ids = list(
            block.get("expected_race_ids")
            or block.get("contested_race_ids")
            or []
        )
        special_ids = [
            rid
            for rid in ids
            if "special" in rid or "unexpired" in rid
        ]
        canaries: list[dict[str, Any]] = []
        if yi == 2014:
            canaries.append(
                {
                    "race_id": "senate-2014-LA",
                    "winner_party": "R",
                    "dem_votes": 561_210,
                    "rep_votes": 712_379,
                }
            )
        if yi == 2018:
            canaries.append(
                {
                    "race_id": "senate-2018-OH",
                    "winner_party": "D",
                    "dem_votes": 2_358_508,
                    "rep_votes": 2_057_559,
                    "min_margin_pp": 6.5,
                }
            )
        if yi == 2020:
            canaries.append(
                {
                    "race_id": "senate-2020-GA-special",
                    "winner_party": "D",
                    "dem_votes": 2_289_113,
                    "rep_votes": 2_195_841,
                }
            )
        if yi == 2022:
            canaries.extend(
                [
                    {
                        "race_id": "senate-2022-CA",
                        "winner_party": "D",
                        "dem_votes": 6_621_621,
                        "rep_votes": 4_222_029,
                    },
                    {
                        "race_id": "senate-2022-CA-unexpired",
                        "winner_party": "D",
                        "dem_votes": 6_559_308,
                        "rep_votes": 4_212_450,
                    },
                    {
                        "race_id": "senate-2022-OK",
                        "other_votes": 41_402,
                    },
                    {
                        "race_id": "senate-2022-OK-special",
                        "other_votes": 34_449,
                    },
                ]
            )
        if yi == 2024:
            canaries.append(
                {
                    "race_id": "senate-2024-AZ",
                    "winner_party": "D",
                    "dem_votes": 1_676_335,
                    "rep_votes": 1_595_761,
                    "min_margin_pp": 2.0,
                }
            )
        cycle = {
            "held_dem": int(block["held_dem"]),
            "held_rep": int(block["held_rep"]),
            "held_ind": int(block.get("held_ind") or 0),
            "n_contested_expected": int(
                block.get("n_contested_expected") or len(ids)
            ),
            "expected_race_ids": ids,
            "contested_race_ids": ids,  # alias retained for old readers
            "expected_special_ids": special_ids,
            "post_dem_seats": POST_DEM[yi],
            "post_election_dem_seats": POST_DEM[yi],
            "post_dem_control": POST_CONTROL[yi],
            "vp_tiebreak_party": VP[yi],
            "canaries": canaries,
        }
        # Contract check
        from midterms.evidence.truth_contract import validate_expectation_cycle

        errs = validate_expectation_cycle(normalize_expectation_cycle(cycle))
        if errs:
            raise ValueError(f"expectations {y}: {errs}")
        expectations["cycles"][y] = cycle

    EXPECTATIONS_PATH.write_text(json.dumps(expectations, indent=2), encoding="utf-8")
    return {"roster": str(ROSTER_PATH), "expectations": str(EXPECTATIONS_PATH)}


def rebuild_canonical_truth() -> dict[str, Any]:
    """Single entrypoint: FTE ledger + roster expectations + normalized parquet."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    out = build_from_fte()
    from midterms.evidence.truth_contract import validate_ledger_contests
    from midterms.evidence.official_ledger import load_ledger

    # Validate the just-written ledger before expectations/normalized materialize.
    led = load_ledger()
    verrs = validate_ledger_contests(led)
    if verrs:
        raise ValueError("truth_v1 semantic validation failed:\n" + "\n".join(verrs[:20]))
    exp = write_expectations_from_roster()
    from midterms.evidence.official_ledger import write_ledger_normalized
    from midterms.ops.release_identity import write_release_identity

    norm = write_ledger_normalized()
    identity = write_release_identity()
    return {**out, **exp, "normalized": norm, "release_identity": identity}


def main() -> None:
    print(json.dumps(rebuild_canonical_truth(), indent=2, default=str))


if __name__ == "__main__":
    main()
