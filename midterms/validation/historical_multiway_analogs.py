"""Discover historical Senate multiway plurality analogs from candidate-level results.

Uses ``official_senate_candidates.json`` candidate rows — not a D/R-collapsed table.
Does not enable a production multiway model.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, RAW_DIR
from midterms.model.multiway_plurality import (
    ANALOG_SELECTION_RULE,
    historical_analog_support_report,
)

OFFICIAL_CANDIDATES = RAW_DIR / "external" / "official_senate_candidates.json"
EXCLUDED_STATES = {"AK", "LA", "GA"}  # RCV / jungle / runoff institutional rules


def _party_bucket(raw: object) -> str:
    s = str(raw or "").strip().upper()
    if s in {"D", "DEM", "DEMOCRATIC", "DEMOCRAT"}:
        return "D"
    if s in {"R", "REP", "REPUBLICAN"}:
        return "R"
    if s in {"I", "IND", "INDEPENDENT", "NPA", "NOP"}:
        return "I"
    if s in {"L", "LIB", "LIBERTARIAN"}:
        return "L"
    if s in {"G", "GRN", "GREEN"}:
        return "G"
    return s or "OTHER"


def discover_historical_multiway_plurality_analogs(
    *,
    source_path: Path | None = None,
    min_year: int = 1990,
    max_year: int = 2024,
) -> dict[str, Any]:
    path = source_path or OFFICIAL_CANDIDATES
    if not path.is_file():
        return {
            "schema_version": "historical-multiway-plurality-analogs-v1",
            "model_version": MODEL_VERSION,
            "status": "source_unavailable",
            "n_analogs": 0,
            "source_path": str(path),
            "note": "Candidate-level certified results file missing; refusing false zeroes.",
            "analogs": [],
            "support": historical_analog_support_report(n_analogs=0),
        }

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = list(payload.get("rows") or [])
    by_race: dict[str, list[dict]] = defaultdict(list)
    years_present: set[int] = set()
    for row in rows:
        if str(row.get("row_role") or "candidate") != "candidate":
            continue
        year = int(row.get("election_year") or 0)
        if year < min_year or year > max_year:
            continue
        years_present.add(year)
        stage = str(row.get("stage") or "general").lower()
        if stage not in {"general", "gen", ""}:
            continue
        race_id = str(row.get("race_id") or "")
        if not race_id:
            continue
        by_race[race_id].append(row)

    analogs: list[dict[str, Any]] = []
    coverage_gaps: list[str] = []
    expected_years = set(range(min_year, max_year + 1, 2))
    missing_years = sorted(expected_years - years_present)
    if missing_years:
        coverage_gaps.append(
            f"candidate-level rows absent for election years: {missing_years}"
        )

    for race_id, cands in sorted(by_race.items()):
        state = str(cands[0].get("state") or race_id.split("-")[-1])
        year = int(cands[0].get("election_year") or 0)
        if state in EXCLUDED_STATES:
            continue
        # Ballot-qualified candidates with vote shares
        usable = []
        for c in cands:
            votes = c.get("votes")
            try:
                v = float(votes) if votes is not None else None
            except (TypeError, ValueError):
                v = None
            if v is None or v < 0:
                continue
            usable.append(c)
        if len(usable) < 3:
            continue
        total = sum(float(c["votes"]) for c in usable)
        if total <= 0:
            continue
        shares = []
        for c in usable:
            party = _party_bucket(c.get("party_bucket") or c.get("ballot_party"))
            shares.append(
                {
                    "candidate_id": f"{race_id}:{normalize_slug(c.get('candidate_name'))}",
                    "candidate_name": c.get("candidate_name"),
                    "ballot_party": party,
                    "votes": float(c["votes"]),
                    "share": float(c["votes"]) / total,
                    "winner": bool(c.get("winner")),
                }
            )
        shares.sort(key=lambda r: r["share"], reverse=True)
        dr_share = sum(s["share"] for s in shares if s["ballot_party"] in {"D", "R"})
        third = max((s["share"] for s in shares if s["ballot_party"] not in {"D", "R"}), default=0.0)
        winner = next((s for s in shares if s["winner"]), shares[0])
        parties = {s["ballot_party"] for s in shares}
        analogs.append(
            {
                "race_id": race_id,
                "state": state,
                "year": year,
                "contest_type": "plurality_general",
                "n_candidates": len(shares),
                "candidates": shares,
                "d_r_combined_share": round(dr_share, 6),
                "largest_nonmajor_share": round(third, 6),
                "winner_candidate_name": winner["candidate_name"],
                "winner_ballot_party": winner["ballot_party"],
                "incumbent_status": None,
                "qualifies_multiway_plurality_analog": True,
                "qualifies_candidate_neutral_binary_analog": len(shares) == 2
                and parties != {"D", "R"},
                "parties_present": sorted(parties),
            }
        )

    support = historical_analog_support_report(n_analogs=len(analogs))
    return {
        "schema_version": "historical-multiway-plurality-analogs-v1",
        "model_version": MODEL_VERSION,
        "status": "ok" if analogs else "no_qualifying_analogs",
        "analog_selection_rule": ANALOG_SELECTION_RULE,
        "source_path": str(path.as_posix()),
        "source_sha256": payload.get("source_sha256"),
        "years_requested": [min_year, max_year],
        "years_present": sorted(years_present),
        "coverage_gaps": coverage_gaps,
        "n_analogs": len(analogs),
        "analogs": analogs,
        "support": support,
        "note": (
            "Built from candidate-level certified rows. Production multiway model "
            "is not automatically enabled."
        ),
    }


def normalize_slug(name: object) -> str:
    import re

    key = " ".join(str(name or "").strip().lower().split())
    slug = "-".join(re.findall(r"[a-z0-9]+", key))
    return slug or "unknown"


def write_historical_multiway_analogs_artifact() -> dict[str, Any]:
    payload = discover_historical_multiway_plurality_analogs()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "historical_multiway_plurality_analogs_v0924.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
