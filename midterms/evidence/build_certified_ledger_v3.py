"""Build vote-count ledger from hashed FTE Senate results CSV (A-02).

Primary machine-readable candidate totals with per-row state SOS source URLs.
Known FEC/state canvass overrides applied for audited canaries.
Expectations (A-03) are written from a separate seat-roster file only.
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

CERT_DIR = RAW_DIR / "external" / "certified"
FTE_CSV = CERT_DIR / "fte_senate.csv"
ROSTER_PATH = RAW_DIR / "external" / "pre_election_seat_rosters.json"
CANDIDATE_LEDGER_PATH = RAW_DIR / "external" / "official_senate_candidates.json"

# Exact canvass overrides (FEC Federal Elections / state SOS).
CANVASS_OVERRIDES: dict[str, dict[str, Any]] = {
    "senate-2018-OH": {
        "dem_votes": 2_358_508,
        "rep_votes": 2_057_559,
        "other_votes": 1_017,
        "source_url": "https://www.fec.gov/resources/cms-content/documents/federalelections2018.pdf",
        "truth_tier": "fec_canvass",
    },
    "senate-2024-AZ": {
        "dem_votes": 1_676_335,
        "rep_votes": 1_595_761,
        "other_votes": 75_868,
        "source_url": "https://azsos.gov/elections/election-information/2024-election-info",
        "truth_tier": "state_canvass",
    },
}

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
    return "O"


def _race_key(year: int, state: str, special: bool, seat_name: str) -> tuple[str, str]:
    """Return (race_id, kind)."""
    seat = str(seat_name or "")
    if year == 2024 and special and state == "CA":
        return "senate-2024-CA-unexpired", "unexpired"
    if year == 2024 and special and state == "NE":
        return "senate-2024-NE-unexpired", "unexpired"
    if special or "special" in seat.lower():
        return f"senate-{year}-{state}-special", "special"
    if "unexpired" in seat.lower() or "short" in seat.lower():
        return f"senate-{year}-{state}-unexpired", "unexpired"
    return f"senate-{year}-{state}", "regular"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_from_fte(years: tuple[int, ...] = (2014, 2016, 2018, 2020, 2022, 2024)) -> dict[str, Any]:
    if not FTE_CSV.exists():
        raise FileNotFoundError(FTE_CSV)
    df = pd.read_csv(FTE_CSV)
    df = df[df["cycle"].isin(years)].copy()
    stage = df["stage"].astype(str).str.lower()
    # Prefer decisive stages: runoff > general > jungle primary (LA majority wins).
    df = df[stage.isin({"general", "runoff", "jungle primary"})].copy()
    df["_stage_rank"] = stage.map({"runoff": 3, "general": 2, "jungle primary": 1}).fillna(0)
    # Keep highest-rank stage per contest key
    key_cols = ["cycle", "state_abbrev", "special", "office_seat_name"]
    max_rank = df.groupby(key_cols, dropna=False)["_stage_rank"].transform("max")
    df = df[df["_stage_rank"] == max_rank]
    # Prefer final RCV round when present
    if "ranked_choice_round" in df.columns:
        df["_rcv"] = pd.to_numeric(df["ranked_choice_round"], errors="coerce")
        max_rcv = df.groupby(key_cols, dropna=False)["_rcv"].transform("max")
        keep = df["_rcv"].isna() | (df["_rcv"] == max_rcv) | (max_rcv.isna())
        df = df[keep]

    candidate_rows: list[dict[str, Any]] = []
    contests_by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)

    group_cols = ["cycle", "state_abbrev", "special", "office_seat_name"]
    for keys, g in df.groupby(group_cols, dropna=False):
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
        if kind == "regular" and state not in CLASS_STATES.get(seat_class, set()):
            # Keep FTE specials that aren't in the class roster
            pass

        dem_v = rep_v = oth_v = 0
        dem_name = rep_name = None
        parties_present: set[str] = set()
        sources: list[str] = []
        for _, row in g.iterrows():
            votes = int(round(float(row["votes"]))) if pd.notna(row.get("votes")) else 0
            bucket = _party_bucket(row.get("ballot_party"), row.get("party"))
            parties_present.add(bucket)
            name = str(row.get("candidate_name") or "") or None
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
                    "votes": votes,
                    "winner": bool(row.get("winner")) if pd.notna(row.get("winner")) else False,
                    "source_url": src,
                    "fte_race_id": row.get("race_id"),
                }
            )

        total = dem_v + rep_v
        margin = (100.0 * (dem_v - rep_v) / total) if total else 0.0
        if dem_v > rep_v:
            winner = "D"
        elif rep_v > dem_v:
            winner = "R"
        else:
            winner = "T"

        multiway = {
            "same_party_general": parties_present <= {"D"} or parties_present <= {"R"},
            "no_dem_nominee": dem_v == 0 or dem_name is None,
            "no_rep_nominee": rep_v == 0 or rep_name is None,
            "has_other_votes": oth_v > 0,
            "rcv": bool(g["ranked_choice_round"].notna().any())
            if "ranked_choice_round" in g.columns
            else False,
        }
        # Detect same-party: two DEM or two REP among top vote-getters
        top = g.sort_values("votes", ascending=False).head(2)
        if len(top) >= 2:
            b0 = _party_bucket(top.iloc[0].get("ballot_party"), top.iloc[0].get("party"))
            b1 = _party_bucket(top.iloc[1].get("ballot_party"), top.iloc[1].get("party"))
            if b0 == b1 and b0 in {"D", "R"}:
                multiway["same_party_general"] = True

        contest = {
            "race_id": race_id,
            "state": state,
            "seat_class": seat_class,
            "kind": kind,
            "term_type": "unexpired" if kind == "unexpired" else "full",
            "dem_votes": dem_v,
            "rep_votes": rep_v,
            "other_votes": oth_v,
            "two_party_margin": round(margin, 4),
            "winner_party": winner,
            "dem_nominee": dem_name,
            "rep_nominee": rep_name,
            "source_url": sources[0] if sources else "",
            "source_urls": sorted(set(sources))[:5],
            "truth_tier": "state_sos_via_fte_csv",
            "multiway": multiway,
            "scaled_synthetic": False,
        }
        if race_id in CANVASS_OVERRIDES:
            ov = CANVASS_OVERRIDES[race_id]
            contest.update({k: ov[k] for k in ("dem_votes", "rep_votes", "other_votes", "source_url", "truth_tier")})
            tot = contest["dem_votes"] + contest["rep_votes"]
            contest["two_party_margin"] = round(
                100.0 * (contest["dem_votes"] - contest["rep_votes"]) / tot, 4
            )
            contest["winner_party"] = "D" if contest["dem_votes"] > contest["rep_votes"] else "R"
        contests_by_year[str(year)].append(contest)

    # Deduplicate race_ids within year (prefer regular over duplicate seat labels)
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
        "parser_version": "official-ledger-v3-fte",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(FTE_CSV.as_posix()),
        "source_sha256": _sha(FTE_CSV),
        "note": (
            "Candidate totals from hashed FiveThirtyEight election_results_senate.csv "
            "(per-row state SOS URLs). Canvass overrides for OH2018/AZ2024. "
            "No margin-scaled 1e6 placeholders."
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
        }

    cand_payload = {
        "parser_version": "candidate-ledger-v1",
        "source_sha256": _sha(FTE_CSV),
        "n": len(candidate_rows),
        "rows": candidate_rows,
    }
    CANDIDATE_LEDGER_PATH.write_text(json.dumps(cand_payload, indent=2), encoding="utf-8")
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return {"ledger": str(LEDGER_PATH), "candidates": str(CANDIDATE_LEDGER_PATH), "n_contests": sum(len(v["contests"]) for v in ledger["cycles"].values())}


def write_expectations_from_roster() -> dict[str, Any]:
    """A-03: expectations from independent seat roster only (no vote margins)."""
    from midterms.evidence.build_official_ledger import HELD

    if not ROSTER_PATH.exists():
        ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        roster = {
            "parser_version": "seat-roster-v1",
            "note": (
                "Pre-election held seats and contested race_id lists. "
                "Must not be regenerated from vote margins. Held seats locked from "
                "build_official_ledger.HELD constants (separate from vote ingestion)."
            ),
            "cycles": {},
        }
        for y, block in ledger.get("cycles", {}).items():
            yi = int(y)
            h = HELD[yi]
            roster["cycles"][y] = {
                "held_dem": int(h["held_dem"]),
                "held_rep": int(h["held_rep"]),
                "held_ind": int(h["held_ind"]),
                "contested_race_ids": [c["race_id"] for c in block["contests"]],
                "n_contested_expected": len(block["contests"]),
            }
        ROSTER_PATH.write_text(json.dumps(roster, indent=2), encoding="utf-8")
    else:
        roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))

    expectations = {
        "parser_version": "chamber-expectations-v3-roster",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(ROSTER_PATH.as_posix()),
        "source_sha256": _sha(ROSTER_PATH),
        "note": "Independent of result vote ingestion; built from pre_election_seat_rosters.json only.",
        "cycles": {},
    }
    for y, block in roster.get("cycles", {}).items():
        expectations["cycles"][y] = {
            "held_dem": int(block["held_dem"]),
            "held_rep": int(block["held_rep"]),
            "held_ind": int(block.get("held_ind") or 0),
            "n_contested_expected": int(
                block.get("n_contested_expected") or len(block.get("contested_race_ids") or [])
            ),
            "contested_race_ids": list(block.get("contested_race_ids") or []),
            "post_election_dem_seats": None,
        }
    EXPECTATIONS_PATH.write_text(json.dumps(expectations, indent=2), encoding="utf-8")
    return {"roster": str(ROSTER_PATH), "expectations": str(EXPECTATIONS_PATH)}


def main() -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    out = build_from_fte()
    exp = write_expectations_from_roster()
    print(json.dumps({**out, **exp}, indent=2))


if __name__ == "__main__":
    main()
