"""Registry-derived modeling-path membership (no hard-coded current race lists)."""

from __future__ import annotations

from typing import Any

from midterms.evidence.non_major_contract import PRINCIPAL_BINARY_ELIGIBLE_STRUCTURES

MULTIWAY_PLURALITY_STRUCTURE = "multiway_plurality"
ALASKA_RCV_STRUCTURE = "ranked_choice_multiway"
ORDINARY_STRUCTURES = {
    "two_party_dem_vs_rep",
    "binary_dem_vs_rep",
    "",
}


def _race_rows(registry: dict[str, Any]) -> list[dict[str, Any]]:
    races = registry.get("races") or []
    return [row for row in races if isinstance(row, dict) and row.get("race_id")]


def binary_non_major_eligible_race_ids(registry: dict[str, Any]) -> frozenset[str]:
    """Races eligible for the candidate-neutral binary / principal-binary adapter.

    Genuine multiway plurality and Alaska RCV are never included.
    """

    out: set[str] = set()
    for row in _race_rows(registry):
        structure = str(row.get("contest_structure") or "")
        if structure in PRINCIPAL_BINARY_ELIGIBLE_STRUCTURES:
            out.add(str(row["race_id"]))
    return frozenset(out)


def multiway_plurality_race_ids(registry: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str(row["race_id"])
        for row in _race_rows(registry)
        if str(row.get("contest_structure") or "") == MULTIWAY_PLURALITY_STRUCTURE
    )


def alaska_rcv_race_ids(registry: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str(row["race_id"])
        for row in _race_rows(registry)
        if str(row.get("contest_structure") or "") == ALASKA_RCV_STRUCTURE
    )


def modeling_path_for_structure(structure: str) -> str:
    structure = str(structure or "")
    if structure == ALASKA_RCV_STRUCTURE:
        return "alaska_rcv_adapter"
    if structure == MULTIWAY_PLURALITY_STRUCTURE:
        return "multiway_plurality_adapter"
    if structure in PRINCIPAL_BINARY_ELIGIBLE_STRUCTURES:
        return "binary_non_major_adapter"
    return "ordinary_stack"


def assert_no_binary_contamination(
    *,
    binary_race_ids: set[str] | frozenset[str],
    multiway_race_ids: set[str] | frozenset[str],
) -> None:
    overlap = set(binary_race_ids) & set(multiway_race_ids)
    if overlap:
        raise ValueError(
            "binary non-major adapter contaminated by multiway races: "
            + ", ".join(sorted(overlap))
        )
