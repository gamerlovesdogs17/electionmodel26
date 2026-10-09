"""Candidate-neutral binary challenger for true two-candidate non-major-v-R contests.

Uses the ordinary binary measurement architecture without pretending the modeled
candidate is a Democrat. Not automatically promoted to production.
"""

from __future__ import annotations

from typing import Any

from midterms.model.contest_classifier import (
    BINARY_NONMAJOR_PRIOR_SD_CHALLENGERS,
    BINARY_NONMAJOR_REFERENCE_PRIOR_SD,
    BINARY_NONMAJOR_V_R,
    PRINCIPAL_BINARY_WITH_MINORS,
    classify_contest_structure,
)
from midterms.model.non_major_adapter import PRIOR_SD, modeled_candidate_margin

assert PRIOR_SD == BINARY_NONMAJOR_REFERENCE_PRIOR_SD

CANDIDATE_NEUTRAL_ELIGIBLE = {BINARY_NONMAJOR_V_R, PRINCIPAL_BINARY_WITH_MINORS}


def candidate_neutral_binary_spec(
    *,
    modeled_candidate_id: str,
    opposing_candidate_id: str,
    modeled_ballot_party: str,
    opposing_ballot_party: str = "R",
    modeled_caucus: str | None = None,
    prior_sd: float = BINARY_NONMAJOR_REFERENCE_PRIOR_SD,
    residual_candidates: list[dict[str, Any]] | None = None,
    contest_category: str = BINARY_NONMAJOR_V_R,
) -> dict[str, Any]:
    return {
        "schema_version": "candidate-neutral-binary-challenger-v1",
        "production_enabled": False,
        "estimand": "modeled_minus_opposing_margin",
        "estimand_note": (
            "Principal-candidate relative margin. When residual ballot lines exist, "
            "principal shares are not assumed to sum to 100%."
            if residual_candidates
            else None
        ),
        "modeled_candidate_id": modeled_candidate_id,
        "opposing_candidate_id": opposing_candidate_id,
        "modeled_ballot_party": modeled_ballot_party,
        "opposing_ballot_party": opposing_ballot_party,
        "modeled_caucus": modeled_caucus,
        "prior_sd": float(prior_sd),
        "prior_sd_challengers": list(BINARY_NONMAJOR_PRIOR_SD_CHALLENGERS),
        "margin_fn": "modeled_candidate_margin",
        "eligible_contest_category": contest_category,
        "residual_candidates": list(residual_candidates or []),
        "claims_only_two_ballot_candidates": not bool(residual_candidates),
    }


def is_eligible_candidate_neutral_binary(
    ballot_candidates: list[dict[str, Any]] | None,
    *,
    contest_structure_hint: str | None = None,
) -> bool:
    cls = classify_contest_structure(
        ballot_candidates=ballot_candidates,
        contest_structure_hint=contest_structure_hint,
    )
    return cls["category"] in CANDIDATE_NEUTRAL_ELIGIBLE


__all__ = [
    "BINARY_NONMAJOR_PRIOR_SD_CHALLENGERS",
    "candidate_neutral_binary_spec",
    "is_eligible_candidate_neutral_binary",
    "modeled_candidate_margin",
]
