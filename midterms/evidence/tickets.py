"""Compatibility view of the reviewed current 2026 candidate registry."""

from __future__ import annotations

from typing import Any

from midterms.evidence.current_candidates import load_current_candidate_registry

_REGISTRY = load_current_candidate_registry()
TICKET_REGISTRY_VERSION = str(_REGISTRY["registry_version"])

# Legacy callers use dem_name/dem_party for the modeled side.  This is a
# lossless compatibility projection of the single current registry, not a
# second candidate source.
TICKETS_2026: dict[str, dict[str, Any]] = {
    str(row["state"]): {
        "dem_name": row["modeled_candidate_name"],
        "rep_name": row["opposing_candidate_name"],
        "dem_party": row["modeled_ballot_party"],
        "modeled_candidate_id": row["modeled_candidate_id"],
        "opposing_candidate_id": row["opposing_candidate_id"],
        "modeled_caucus": row["modeled_caucus"],
        "modeled_caucus_basis": row["modeled_caucus_basis"],
        "opposing_caucus": row["opposing_caucus"],
        "opposing_caucus_basis": row["opposing_caucus_basis"],
        "contest_structure": row["contest_structure"],
        "statistical_target_supported": row["statistical_target_supported"],
    }
    for row in _REGISTRY["races"]
}


def ticket_for_state(state: str) -> dict[str, Any]:
    st = str(state).upper()
    row = TICKETS_2026.get(st)
    if row:
        return dict(row)
    return {"dem_name": "Democrat", "rep_name": "Republican", "dem_party": "D"}


def ticket_names(state: str) -> tuple[str, str]:
    """Backward-compatible (dem_or_ind_name, rep_name). """
    t = ticket_for_state(state)
    return str(t["dem_name"]), str(t["rep_name"])
