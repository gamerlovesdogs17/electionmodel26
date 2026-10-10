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


def is_approved_multiway_withheld_race(
    row: dict[str, Any],
    *,
    multiway_race_ids: set[str] | frozenset[str],
) -> bool:
    """True when a forecast failure is an intentionally fail-closed genuine multiway race.

    Evidence must still pass. A multiway race with broken evidence is not "approved
    withheld" — it is a hard evidence failure.
    """

    race_id = str(row.get("race_id") or "")
    if race_id not in multiway_race_ids:
        return False
    # Broken evidence is never an approved probability withholdal.
    if row.get("evidence_status") not in {None, "pass"}:
        return False
    structure = str(row.get("contest_structure") or "")
    if structure and structure != MULTIWAY_PLURALITY_STRUCTURE:
        return False
    return row.get("forecast_status") == "fail"


def classify_forecast_coverage_failures(
    failing_races: list[dict[str, Any]],
    *,
    multiway_race_ids: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Split forecast failures into approved multiway-withheld vs blocking failures."""

    approved: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    for row in failing_races:
        if not isinstance(row, dict):
            unexpected.append({"race_id": None, "reasons": ["malformed_forecast_failure_row"]})
            continue
        if is_approved_multiway_withheld_race(row, multiway_race_ids=multiway_race_ids):
            approved.append(row)
        else:
            unexpected.append(row)
    approved_ids = sorted(
        str(row.get("race_id")) for row in approved if row.get("race_id")
    )
    unexpected_ids = sorted(
        str(row.get("race_id")) for row in unexpected if row.get("race_id")
    )
    return {
        "approved_multiway_withheld": approved,
        "unexpected_forecast_failures": unexpected,
        "approved_multiway_withheld_race_ids": approved_ids,
        "unexpected_forecast_failure_race_ids": unexpected_ids,
        "multiway_withheld_only": bool(approved) and not unexpected,
    }
