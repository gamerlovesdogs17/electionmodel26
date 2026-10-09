"""Challenger-ready statewide prior feature builders (not production-enabled).

Only features that can be reconstructed historically with point-in-time
availability are exposed. Vague biographical judgments are excluded.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from midterms.config import RAW_DIR

OFFICIAL_CANDIDATES = RAW_DIR / "external" / "official_senate_candidates.json"


def recent_senate_two_party_performance(
    *,
    state: str,
    as_of: str | date,
    lookback_cycles: int = 2,
    source_path: Path | None = None,
) -> dict[str, Any]:
    """Lagged statewide Senate D−R two-party performance available as-of."""
    cutoff = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    path = source_path or OFFICIAL_CANDIDATES
    if not path.is_file():
        return {
            "feature": "recent_senate_two_party_performance",
            "available": False,
            "reason": "official_senate_candidates_missing",
            "value": None,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_race: dict[str, list] = defaultdict(list)
    for row in payload.get("rows") or []:
        if str(row.get("state") or "") != state:
            continue
        year = int(row.get("election_year") or 0)
        if year <= 0 or year >= cutoff.year:
            # Same-cycle / future results unavailable.
            continue
        # Election Day of that year must be before as_of.
        from midterms.evidence.federal_election_day import federal_election_day

        ed = federal_election_day(year)
        if ed > cutoff:
            continue
        by_race[str(row.get("race_id"))].append(row)

    margins = []
    used = []
    for race_id, rows in sorted(by_race.items(), reverse=True):
        dem = sum(float(r.get("votes") or 0) for r in rows if str(r.get("party_bucket") or r.get("ballot_party") or "").upper() in {"D", "DEM", "DEMOCRATIC"})
        rep = sum(float(r.get("votes") or 0) for r in rows if str(r.get("party_bucket") or r.get("ballot_party") or "").upper() in {"R", "REP", "REPUBLICAN"})
        if dem + rep <= 0:
            continue
        margin = 100.0 * (dem - rep) / (dem + rep)
        margins.append(margin)
        used.append({"race_id": race_id, "margin": margin})
        if len(margins) >= lookback_cycles:
            break
    if not margins:
        return {
            "feature": "recent_senate_two_party_performance",
            "available": False,
            "reason": "no_lagged_senate_results",
            "value": None,
        }
    return {
        "feature": "recent_senate_two_party_performance",
        "available": True,
        "value": float(sum(margins) / len(margins)),
        "n_cycles": len(margins),
        "components": used,
        "available_at": used[0]["race_id"],
        "production_enabled": False,
    }


def special_election_signal_as_of(
    *,
    state: str,
    as_of: str | date,
) -> dict[str, Any]:
    """Placeholder: only expose when a sealed special-election ledger exists."""
    ledger = RAW_DIR / "external" / "special_election_senate_ledger.json"
    if not ledger.is_file():
        return {
            "feature": "special_election_signal",
            "available": False,
            "reason": "no_point_in_time_special_election_ledger",
            "value": None,
            "production_enabled": False,
        }
    # Ledger present — caller-specific parsing left for OOS wiring.
    del state, as_of
    return {
        "feature": "special_election_signal",
        "available": True,
        "value": None,
        "reason": "ledger_present_but_feature_not_auto_enabled",
        "production_enabled": False,
    }


def candidate_experience_feature(*, objective_only: bool = True) -> dict[str, Any]:
    """Candidate experience excluded unless objective reconstructable coding exists."""
    del objective_only
    return {
        "feature": "candidate_experience",
        "available": False,
        "reason": "no_objective_historically_reconstructable_experience_coding_in_repo",
        "value": None,
        "production_enabled": False,
    }


def challenger_prior_feature_bundle(
    *,
    state: str,
    as_of: str | date,
) -> dict[str, Any]:
    return {
        "schema_version": "statewide-prior-challengers-v1",
        "production_enabled": False,
        "features": {
            "recent_senate_performance": recent_senate_two_party_performance(
                state=state, as_of=as_of
            ),
            "special_election_signal": special_election_signal_as_of(
                state=state, as_of=as_of
            ),
            "candidate_experience": candidate_experience_feature(),
        },
    }
