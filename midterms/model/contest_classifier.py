"""Structural contest classifier based on ballot/candidate structure (not state ID)."""

from __future__ import annotations

from typing import Any

ORDINARY_DVR = "ordinary_d_v_r_binary"
BINARY_NONMAJOR_V_R = "binary_non_major_v_r"
PRINCIPAL_BINARY_WITH_MINORS = "principal_binary_with_minor_residual"
GENUINE_MULTIWAY_PLURALITY = "genuine_multiway_plurality"
ALASKA_RCV_MULTIWAY = "alaska_rcv_multiway"
UNSUPPORTED = "unsupported_or_unknown"


def classify_contest_structure(
    *,
    ballot_candidates: list[dict[str, Any]] | None = None,
    contest_structure_hint: str | None = None,
    state: str | None = None,
    election_rule: str | None = None,
) -> dict[str, Any]:
    """Classify from actual ballot/candidate structure.

    Does not hard-code state→model mappings. Alaska RCV is detected from the
    election rule / registry hint, not merely from state==AK.
    """
    hint = str(contest_structure_hint or "").lower()
    rule = str(election_rule or "").lower()
    if "ranked_choice" in hint or "rcv" in rule or "irv" in rule:
        return {
            "category": ALASKA_RCV_MULTIWAY,
            "basis": "election_rule_or_registry_hint",
            "n_ballot_candidates": len(ballot_candidates or []),
            "state_identity_used": False,
        }

    cands = list(ballot_candidates or [])
    parties = []
    for c in cands:
        if c.get("withdrawn") or c.get("write_in"):
            continue
        party = str(c.get("ballot_party") or "").upper()
        if party in {"REP", "REPUBLICAN"}:
            party = "R"
        elif party in {"DEM", "DEMOCRATIC", "DEMOCRAT"}:
            party = "D"
        elif party in {"IND", "INDEPENDENT"}:
            party = "I"
        elif party in {"LIB", "LIBERTARIAN"}:
            party = "L"
        if party:
            parties.append(party)
    n = len(parties)
    party_set = set(parties)

    if n <= 0:
        if hint in {"binary_dem_vs_rep", "ordinary_d_v_r_binary"}:
            return {
                "category": ORDINARY_DVR,
                "basis": "registry_hint_without_ballot_list",
                "n_ballot_candidates": 0,
                "state_identity_used": False,
            }
        if hint in {"non_major_party_vs_republican", "binary_non_major_v_r"}:
            return {
                "category": BINARY_NONMAJOR_V_R,
                "basis": "registry_hint_without_ballot_list",
                "n_ballot_candidates": 0,
                "state_identity_used": False,
            }
        if hint in {"multiway_plurality"}:
            return {
                "category": GENUINE_MULTIWAY_PLURALITY,
                "basis": "registry_hint_without_ballot_list",
                "n_ballot_candidates": 0,
                "state_identity_used": False,
            }
        return {
            "category": UNSUPPORTED,
            "basis": "insufficient_ballot_structure",
            "n_ballot_candidates": 0,
            "state_identity_used": False,
        }

    if n == 2 and party_set == {"D", "R"}:
        return {
            "category": ORDINARY_DVR,
            "basis": "two_ballot_candidates_d_r",
            "n_ballot_candidates": n,
            "parties": sorted(party_set),
            "state_identity_used": False,
        }
    if n == 2 and "R" in party_set and "D" not in party_set:
        return {
            "category": BINARY_NONMAJOR_V_R,
            "basis": "two_ballot_candidates_nonmajor_v_r",
            "n_ballot_candidates": n,
            "parties": sorted(party_set),
            "state_identity_used": False,
        }
    if n >= 3:
        # Principal D/R with small residual minors vs genuine multiway.
        major = [p for p in parties if p in {"D", "R"}]
        nonmajor = [p for p in parties if p not in {"D", "R"}]
        if len(set(major)) == 2 and len(nonmajor) >= 1:
            # Still multiway on the ballot — classify as genuine multiway unless
            # residual share metadata marks them as negligible (not assumed here).
            return {
                "category": GENUINE_MULTIWAY_PLURALITY,
                "basis": "three_plus_ballot_candidates",
                "n_ballot_candidates": n,
                "parties": sorted(party_set),
                "optional_principal_binary_view": PRINCIPAL_BINARY_WITH_MINORS,
                "state_identity_used": False,
            }
        return {
            "category": GENUINE_MULTIWAY_PLURALITY,
            "basis": "three_plus_ballot_candidates",
            "n_ballot_candidates": n,
            "parties": sorted(party_set),
            "state_identity_used": False,
        }
    return {
        "category": UNSUPPORTED,
        "basis": "unrecognized_structure",
        "n_ballot_candidates": n,
        "parties": sorted(party_set),
        "state_identity_used": False,
        "state_hint_ignored": state,
    }


# Challenger prior SDs for binary non-major adapter — for later OOS only.
BINARY_NONMAJOR_PRIOR_SD_CHALLENGERS = (20.0, 30.0, 40.0)
BINARY_NONMAJOR_REFERENCE_PRIOR_SD = 30.0
