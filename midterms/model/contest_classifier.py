"""Structural contest classifier based on ballot/candidate structure (not state ID)."""

from __future__ import annotations

from typing import Any

from midterms.model.principal_binary_criterion import (
    CRITERION_ID,
    PRINCIPAL_BINARY_MAX_THIRD_SHARE,
    PRINCIPAL_BINARY_MIN_TOP_TWO_SHARE,
    PRINCIPAL_POLL_SHARE_FLOOR,
    classify_share_profile,
)

ORDINARY_DVR = "ordinary_d_v_r_binary"
BINARY_NONMAJOR_V_R = "binary_non_major_v_r"
PRINCIPAL_BINARY_WITH_MINORS = "principal_binary_with_minor_residual"
GENUINE_MULTIWAY_PLURALITY = "genuine_multiway_plurality"
ALASKA_RCV_MULTIWAY = "alaska_rcv_multiway"
UNSUPPORTED = "unsupported_or_unknown"

# Registry / official-field aliases used elsewhere in the repo.
REGISTRY_MULTIWAY = "multiway_plurality"
REGISTRY_NONMAJOR = "non_major_party_vs_republican"
REGISTRY_PRINCIPAL_BINARY = "principal_binary_with_minor_residual"


def _norm_party(raw: object) -> str:
    party = str(raw or "").upper()
    if party in {"REP", "REPUBLICAN"}:
        return "R"
    if party in {"DEM", "DEMOCRATIC", "DEMOCRAT"}:
        return "D"
    if party in {"IND", "INDEPENDENT", "NPA", "NOP", "BY PETITION", "PETITION"}:
        return "I"
    if party in {"LIB", "LIBERTARIAN"}:
        return "L"
    if party in {"G", "GRN", "GREEN"}:
        return "G"
    return party


def _active_candidates(ballot_candidates: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in ballot_candidates or []:
        if c.get("withdrawn") or c.get("write_in"):
            continue
        party = _norm_party(c.get("ballot_party"))
        if not party:
            continue
        row = dict(c)
        row["_party"] = party
        out.append(row)
    return out


def _is_credible_principal(
    cand: dict[str, Any],
    *,
    poll_share: float | None,
) -> bool:
    """General principal-candidate rule (no state hard-coding)."""
    party = cand["_party"]
    if party in {"D", "R"}:
        return True
    caucus = str(cand.get("caucus") or "").upper()
    if party == "I" and caucus in {"D", "R"}:
        # Petition / independent with an explicit caucus facing a major-party opponent.
        return True
    return poll_share is not None and float(poll_share) >= PRINCIPAL_POLL_SHARE_FLOOR


def _principal_pair_and_residuals(
    cands: list[dict[str, Any]],
    *,
    candidate_poll_shares: dict[str, float] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    shares = candidate_poll_shares or {}
    principals: list[dict[str, Any]] = []
    residuals: list[dict[str, Any]] = []
    for c in cands:
        key = str(c.get("candidate_id") or c.get("candidate_name") or "")
        share = shares.get(key)
        if share is None:
            # Also allow bare-name keys.
            share = shares.get(str(c.get("candidate_name") or ""))
        if _is_credible_principal(c, poll_share=share):
            principals.append(c)
        else:
            residuals.append(c)

    evidence = {
        "criterion_id": CRITERION_ID,
        "n_credible_principals": len(principals),
        "n_residual_candidates": len(residuals),
        "principal_poll_share_floor": PRINCIPAL_POLL_SHARE_FLOOR,
        "historical_top_two_min": PRINCIPAL_BINARY_MIN_TOP_TWO_SHARE,
        "historical_third_max": PRINCIPAL_BINARY_MAX_THIRD_SHARE,
        "poll_shares_supplied": bool(shares),
    }
    return principals, residuals, evidence


def classify_contest_structure(
    *,
    ballot_candidates: list[dict[str, Any]] | None = None,
    contest_structure_hint: str | None = None,
    state: str | None = None,
    election_rule: str | None = None,
    candidate_poll_shares: dict[str, float] | None = None,
    result_shares: list[float] | None = None,
) -> dict[str, Any]:
    """Classify from actual ballot/candidate structure.

    Does not hard-code state→model mappings. Alaska RCV is detected from the
    election rule / registry hint, not merely from state==AK.

    For 3+ candidate fields, uses the predeclared historical principal-binary
    criterion (share profile when results/polls exist; otherwise a general
    credible-principal vs residual-minor rule).
    """
    hint = str(contest_structure_hint or "").lower()
    rule = str(election_rule or "").lower()
    if "ranked_choice" in hint or "rcv" in rule or "irv" in rule:
        return {
            "category": ALASKA_RCV_MULTIWAY,
            "registry_alias": "ranked_choice_multiway",
            "basis": "election_rule_or_registry_hint",
            "n_ballot_candidates": len(ballot_candidates or []),
            "state_identity_used": False,
        }

    cands = _active_candidates(ballot_candidates)
    parties = [c["_party"] for c in cands]
    n = len(parties)
    party_set = set(parties)

    if n <= 0:
        if hint in {"binary_dem_vs_rep", "ordinary_d_v_r_binary"}:
            return {
                "category": ORDINARY_DVR,
                "registry_alias": "binary_dem_vs_rep",
                "basis": "registry_hint_without_ballot_list",
                "n_ballot_candidates": 0,
                "state_identity_used": False,
            }
        if hint in {"non_major_party_vs_republican", "binary_non_major_v_r"}:
            return {
                "category": BINARY_NONMAJOR_V_R,
                "registry_alias": REGISTRY_NONMAJOR,
                "basis": "registry_hint_without_ballot_list",
                "n_ballot_candidates": 0,
                "state_identity_used": False,
            }
        if hint in {"multiway_plurality", "genuine_multiway_plurality"}:
            return {
                "category": GENUINE_MULTIWAY_PLURALITY,
                "registry_alias": REGISTRY_MULTIWAY,
                "basis": "registry_hint_without_ballot_list",
                "n_ballot_candidates": 0,
                "state_identity_used": False,
            }
        if hint in {"principal_binary_with_minor_residual"}:
            return {
                "category": PRINCIPAL_BINARY_WITH_MINORS,
                "registry_alias": REGISTRY_PRINCIPAL_BINARY,
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
            "registry_alias": "binary_dem_vs_rep",
            "basis": "two_ballot_candidates_d_r",
            "n_ballot_candidates": n,
            "parties": sorted(party_set),
            "state_identity_used": False,
            "modeling_path": "ordinary_stack",
        }
    if n == 2 and "R" in party_set and "D" not in party_set:
        return {
            "category": BINARY_NONMAJOR_V_R,
            "registry_alias": REGISTRY_NONMAJOR,
            "basis": "two_ballot_candidates_nonmajor_v_r",
            "n_ballot_candidates": n,
            "parties": sorted(party_set),
            "state_identity_used": False,
            "modeling_path": "binary_non_major_adapter",
            "principal_pair": [
                {
                    "candidate_id": c.get("candidate_id"),
                    "candidate_name": c.get("candidate_name"),
                    "ballot_party": c["_party"],
                }
                for c in cands
            ],
            "residual_candidates": [],
        }

    if n >= 3:
        # Prefer explicit certified/result share profile when provided.
        if result_shares is not None and len(result_shares) >= 3:
            ordered = sorted((float(x) for x in result_shares), reverse=True)
            top2 = ordered[0] + ordered[1]
            third = ordered[2]
            label = classify_share_profile(
                top_two_combined_share=top2, third_place_share=third
            )
            principals, residuals, evidence = _principal_pair_and_residuals(
                cands, candidate_poll_shares=candidate_poll_shares
            )
            evidence.update(
                {
                    "top_two_combined_share": top2,
                    "third_place_share": third,
                    "basis": "historical_share_profile_thresholds",
                }
            )
            return {
                "category": label,
                "registry_alias": (
                    REGISTRY_PRINCIPAL_BINARY
                    if label == PRINCIPAL_BINARY_WITH_MINORS
                    else REGISTRY_MULTIWAY
                ),
                "basis": evidence["basis"],
                "n_ballot_candidates": n,
                "parties": sorted(party_set),
                "principal_pair": [
                    {
                        "candidate_id": c.get("candidate_id"),
                        "candidate_name": c.get("candidate_name"),
                        "ballot_party": c["_party"],
                        "caucus": c.get("caucus"),
                    }
                    for c in principals[:2]
                ],
                "residual_candidates": [
                    {
                        "candidate_id": c.get("candidate_id"),
                        "candidate_name": c.get("candidate_name"),
                        "ballot_party": c["_party"],
                    }
                    for c in residuals
                ],
                "classification_evidence": evidence,
                "state_identity_used": False,
                "modeling_path": (
                    "candidate_neutral_binary_with_minor_residual"
                    if label == PRINCIPAL_BINARY_WITH_MINORS
                    else "multiway_plurality_adapter"
                ),
                "estimand_note": (
                    "Principal-candidate relative margin; residual ballot lines "
                    "preserved and do not imply shares sum to 100% over the pair alone."
                    if label == PRINCIPAL_BINARY_WITH_MINORS
                    else None
                ),
            }

        principals, residuals, evidence = _principal_pair_and_residuals(
            cands, candidate_poll_shares=candidate_poll_shares
        )
        # Poll-implied residual mass among non-principals.
        residual_poll = 0.0
        if candidate_poll_shares:
            for c in residuals:
                key = str(c.get("candidate_id") or c.get("candidate_name") or "")
                residual_poll += float(
                    candidate_poll_shares.get(key)
                    or candidate_poll_shares.get(str(c.get("candidate_name") or ""))
                    or 0.0
                )
            evidence["residual_poll_share"] = residual_poll

        if len(principals) >= 3:
            return {
                "category": GENUINE_MULTIWAY_PLURALITY,
                "registry_alias": REGISTRY_MULTIWAY,
                "basis": "three_or_more_credible_principals",
                "n_ballot_candidates": n,
                "parties": sorted(party_set),
                "principal_pair": [
                    {
                        "candidate_id": c.get("candidate_id"),
                        "candidate_name": c.get("candidate_name"),
                        "ballot_party": c["_party"],
                        "caucus": c.get("caucus"),
                    }
                    for c in principals
                ],
                "residual_candidates": [
                    {
                        "candidate_id": c.get("candidate_id"),
                        "candidate_name": c.get("candidate_name"),
                        "ballot_party": c["_party"],
                    }
                    for c in residuals
                ],
                "classification_evidence": evidence,
                "state_identity_used": False,
                "modeling_path": "multiway_plurality_adapter",
            }

        if (
            len(principals) == 2
            and residuals
            and residual_poll <= PRINCIPAL_BINARY_MAX_THIRD_SHARE
        ):
            return {
                "category": PRINCIPAL_BINARY_WITH_MINORS,
                "registry_alias": REGISTRY_PRINCIPAL_BINARY,
                "basis": "two_credible_principals_with_minor_residual",
                "n_ballot_candidates": n,
                "parties": sorted(party_set),
                "principal_pair": [
                    {
                        "candidate_id": c.get("candidate_id"),
                        "candidate_name": c.get("candidate_name"),
                        "ballot_party": c["_party"],
                        "caucus": c.get("caucus"),
                    }
                    for c in principals
                ],
                "residual_candidates": [
                    {
                        "candidate_id": c.get("candidate_id"),
                        "candidate_name": c.get("candidate_name"),
                        "ballot_party": c["_party"],
                    }
                    for c in residuals
                ],
                "classification_evidence": evidence,
                "state_identity_used": False,
                "modeling_path": "candidate_neutral_binary_with_minor_residual",
                "estimand_note": (
                    "Modeled estimand is the principal-candidate relative margin "
                    "(e.g. independent/petition nominee minus Republican). "
                    "Residual minor-party ballot lines are preserved; principal "
                    "shares are not assumed to sum to 100%."
                ),
                "validation_level": "limited_supported_principal_binary",
            }

        # Default: ballot multiway without a stable principal-binary reduction.
        return {
            "category": GENUINE_MULTIWAY_PLURALITY,
            "registry_alias": REGISTRY_MULTIWAY,
            "basis": "three_plus_ballot_candidates_without_principal_binary_support",
            "n_ballot_candidates": n,
            "parties": sorted(party_set),
            "principal_pair": [
                {
                    "candidate_id": c.get("candidate_id"),
                    "candidate_name": c.get("candidate_name"),
                    "ballot_party": c["_party"],
                    "caucus": c.get("caucus"),
                }
                for c in principals
            ],
            "residual_candidates": [
                {
                    "candidate_id": c.get("candidate_id"),
                    "candidate_name": c.get("candidate_name"),
                    "ballot_party": c["_party"],
                }
                for c in residuals
            ],
            "classification_evidence": evidence,
            "state_identity_used": False,
            "modeling_path": "multiway_plurality_adapter",
        }

    return {
        "category": UNSUPPORTED,
        "basis": "unrecognized_structure",
        "n_ballot_candidates": n,
        "parties": sorted(party_set),
        "state_identity_used": False,
        "state_hint_ignored": state,
    }


def registry_contest_structure(classification: dict[str, Any]) -> str:
    """Map classifier category to registry contest_structure string."""
    if classification.get("registry_alias"):
        return str(classification["registry_alias"])
    cat = classification.get("category")
    if cat == ORDINARY_DVR:
        return "binary_dem_vs_rep"
    if cat == BINARY_NONMAJOR_V_R:
        return REGISTRY_NONMAJOR
    if cat == PRINCIPAL_BINARY_WITH_MINORS:
        return REGISTRY_PRINCIPAL_BINARY
    if cat == GENUINE_MULTIWAY_PLURALITY:
        return REGISTRY_MULTIWAY
    if cat == ALASKA_RCV_MULTIWAY:
        return "ranked_choice_multiway"
    return "unsupported"


# Challenger prior SDs for binary non-major adapter — for later OOS only.
BINARY_NONMAJOR_PRIOR_SD_CHALLENGERS = (20.0, 30.0, 40.0)
BINARY_NONMAJOR_REFERENCE_PRIOR_SD = 30.0
