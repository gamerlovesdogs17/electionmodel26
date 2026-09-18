"""Independent official Senate event ledger (fresh audit R-01 / R-03).

The ledger is an external JSON artifact under ``data/raw/external/``. Contest
universes and certified vote counts are loaded from that file — they are never
generated from CYCLE_META. Chamber expectations live in a *separate* JSON so
validators cannot tautologically compare a store to the function that built it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

LEDGER_PATH = RAW_DIR / "external" / "official_senate_ledger.json"
EXPECTATIONS_PATH = RAW_DIR / "external" / "independent_chamber_expectations.json"
PARSER_VERSION = "official-ledger-v2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ledger(*, path: Path | None = None) -> dict[str, Any]:
    path = path or LEDGER_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"missing official ledger {path}; run build_official_ledger / write_official_ledger"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_expectations(*, path: Path | None = None) -> dict[str, Any]:
    path = path or EXPECTATIONS_PATH
    if not path.exists():
        raise FileNotFoundError(f"missing independent expectations {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    from midterms.evidence.truth_contract import normalize_expectation_cycle

    cycles = raw.get("cycles") or {}
    raw["cycles"] = {y: normalize_expectation_cycle(block) for y, block in cycles.items()}
    return raw


def ledger_cycles(ledger: dict[str, Any] | None = None) -> list[int]:
    data = ledger or load_ledger()
    return sorted(int(y) for y in (data.get("cycles") or {}))


def contests_for_year(year: int, *, ledger: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = ledger or load_ledger()
    block = (data.get("cycles") or {}).get(str(year)) or {}
    contests = list(block.get("contests") or [])
    if not contests:
        raise ValueError(f"ledger has no contests for {year}")
    return contests


def results_rows_from_ledger(*, ledger: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Derive certified result rows from nonzero dem/rep vote counts."""
    data = ledger or load_ledger()
    rows: list[dict[str, Any]] = []
    for year_s, block in (data.get("cycles") or {}).items():
        year = int(year_s)
        election_id = f"senate-{year}"
        available_at = str(block.get("certified_available_at") or f"{year}-11-22")
        for c in block.get("contests") or []:
            dem = float(c.get("dem_votes") or 0)
            rep = float(c.get("rep_votes") or 0)
            other = float(c.get("other_votes") or 0)
            multi = c.get("multiway") or {}
            same_party = bool(multi.get("same_party_general"))
            if dem <= 0 and rep <= 0 and not same_party:
                raise ValueError(f"{c.get('race_id')}: ledger requires nonzero vote counts")
            tot = dem + rep
            margin = float(c.get("two_party_margin")) if c.get("two_party_margin") is not None else (
                100.0 * (dem - rep) / tot if tot > 0 else 0.0
            )
            rid = str(c["race_id"])
            st = str(c.get("state") or rid.split("-")[2])
            winner_party = str(c.get("winner_party") or ("D" if margin > 0 else ("R" if margin < 0 else "T")))
            winner_caucus = str(c.get("winner_caucus") or winner_party)
            rows.append(
                {
                    "result_id": f"{rid}-certified",
                    "election_id": election_id,
                    "office": "US_SENATE",
                    "state": st,
                    "race_id": rid,
                    "event_time": str(c.get("election_day") or f"{year}-11-01"),
                    "available_at": str(c.get("available_at") or available_at),
                    "certified_at": str(c.get("available_at") or available_at),
                    "dem_votes": dem,
                    "rep_votes": rep,
                    "other_votes": other,
                    "two_party_margin": float(margin),
                    "winner_party": winner_party,
                    "winner_caucus": winner_caucus,
                    "modeled_side": c.get("modeled_side") or winner_caucus,
                    "stage": c.get("stage"),
                    "certification_status": c.get("certification_status") or "certified",
                    "source_url": str(c.get("source_url") or block.get("source_url") or ""),
                    "raw_hash": c.get("source_object_hash"),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "release_version": 1,
                }
            )
    return rows


def nominees_from_ledger(year: int, *, ledger: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in contests_for_year(year, ledger=ledger):
        rid = str(c["race_id"])
        out[rid] = {
            "dem_candidate_name": c.get("dem_nominee"),
            "rep_candidate_name": c.get("rep_nominee"),
            "dem_candidate_id": c.get("dem_nominee_id"),
            "rep_candidate_id": c.get("rep_nominee_id"),
            "source": "official_senate_ledger",
            "n_late_polls": None,
        }
    return out


def write_ledger_normalized(*, ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    """Materialize races/results parquet from the external ledger (+ expectations held seats)."""
    from midterms.evidence.official_ballot import (
        BASE_LEANS,
        _region,
        build_held_rows_from_expectations,
        election_day,
    )
    from midterms.evidence.schema import RACE_COLUMNS, RESULT_COLUMNS, align_result_frame

    data = ledger or load_ledger()
    expectations = load_expectations()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    race_rows: list[dict[str, Any]] = []
    for year_s, block in (data.get("cycles") or {}).items():
        year = int(year_s)
        ed = str(block.get("election_day") or election_day(year).isoformat())
        exp = (expectations.get("cycles") or {}).get(str(year)) or {}
        for c in block.get("contests") or []:
            st = str(c["state"])
            lean = float(c.get("prior_lean") if c.get("prior_lean") is not None else BASE_LEANS.get(st, 0.0))
            race_rows.append(
                {
                    "race_id": c["race_id"],
                    "election_id": f"senate-{year}",
                    "office": "US_SENATE",
                    "state": st,
                    "seat_class": c.get("seat_class") or "special",
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
        race_rows.extend(build_held_rows_from_expectations(year, exp, election_day=ed))

    races = pd.DataFrame(race_rows)
    # Ensure schema columns exist
    for col in RACE_COLUMNS:
        if col not in races.columns:
            races[col] = None
    races = races[RACE_COLUMNS]

    results = align_result_frame(pd.DataFrame(results_rows_from_ledger(ledger=data)))

    races_path = NORMALIZED_DIR / "races_official.parquet"
    results_path = NORMALIZED_DIR / "results_certified.parquet"
    races.to_parquet(races_path, index=False)
    results.to_parquet(results_path, index=False)

    raw_results = RAW_DIR / "external" / "senate_certified_results.json"
    raw_results.write_text(
        json.dumps(
            {
                "parser_version": PARSER_VERSION,
                "n": int(len(results)),
                "elections": sorted(results["election_id"].unique()) if len(results) else [],
                "rows": results.to_dict(orient="records"),
                "source_ledger": str(LEDGER_PATH),
                "ledger_sha256": _sha256(LEDGER_PATH) if LEDGER_PATH.exists() else None,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser_version": PARSER_VERSION,
        "ledger_path": str(LEDGER_PATH),
        "ledger_sha256": _sha256(LEDGER_PATH),
        "expectations_path": str(EXPECTATIONS_PATH),
        "expectations_sha256": _sha256(EXPECTATIONS_PATH),
        "n_races": int(len(races)),
        "n_results": int(len(results)),
        "cycles": ledger_cycles(data),
        "paths": {
            "races_official": str(races_path),
            "results_certified": str(results_path),
            "raw_results": str(raw_results),
        },
        "note": (
            "Official contests + vote-count-derived margins from external ledger; "
            "held seats from independent expectations (not sync_held)."
        ),
    }
    (MANIFESTS_DIR / "official_senate_ballots.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    (MANIFESTS_DIR / "results_certified.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    (RAW_DIR / "external" / "official_senate_ballots.json").write_text(
        json.dumps({"parser_version": PARSER_VERSION, "cycles": data.get("cycles")}, indent=2, default=str),
        encoding="utf-8",
    )
    return man
