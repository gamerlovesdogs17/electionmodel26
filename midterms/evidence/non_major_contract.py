"""Identity and eligibility contract for the narrow non-major-party adapter.

This module deliberately contains no forecasting code.  It lets evidence,
coverage, and chamber accounting agree on which target the exceptional model
can represent without redefining an Independent candidate as a Democrat.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

NON_MAJOR_CONTEST_STRUCTURE = "non_major_party_vs_republican"
PRINCIPAL_BINARY_CONTEST_STRUCTURE = "principal_binary_with_minor_residual"
NON_MAJOR_ADAPTER_METHOD = "limited_validation_exception_model-v1"
NON_MAJOR_TARGET = "modeled_candidate_margin"
PRINCIPAL_BINARY_ELIGIBLE_STRUCTURES = {
    NON_MAJOR_CONTEST_STRUCTURE,
    PRINCIPAL_BINARY_CONTEST_STRUCTURE,
}


def _present(value: Any) -> bool:
    return value is not None and not pd.isna(value) and bool(str(value).strip())


def non_major_identity_supported(row: Mapping[str, Any] | pd.Series) -> bool:
    """Return whether a row has the explicit identity/caucus contract.

    Poll availability is intentionally checked elsewhere.  The adapter only
    represents an Independent/non-major modeled candidate against a Republican
    opponent, with both caucus mappings explicitly declared.
    """

    structure = str(row.get("contest_structure") or "")
    return bool(
        structure in PRINCIPAL_BINARY_ELIGIBLE_STRUCTURES
        and str(row.get("modeled_ballot_party") or "").upper() == "I"
        and str(row.get("opposing_ballot_party") or "").upper() == "R"
        and str(row.get("modeled_caucus") or "").upper() == "D"
        and str(row.get("opposing_caucus") or "").upper() == "R"
        and _present(row.get("modeled_candidate_id"))
        and _present(row.get("opposing_candidate_id"))
        and _present(row.get("modeled_caucus_basis"))
        and _present(row.get("opposing_caucus_basis"))
        and str(row.get("current_matchup_status") or "") == "reviewed_current"
    )


def probability_support_status(
    row: Mapping[str, Any] | pd.Series,
    *,
    n_compatible_polls: int,
) -> tuple[bool, str, str]:
    """Return (supported, status, reason) for current probability coverage."""

    structure = str(row.get("contest_structure") or "")
    if structure == "ranked_choice_multiway":
        try:
            from midterms.evidence.alaska_rcv import RACE_ID, load_alaska_rcv_evidence

            evidence = load_alaska_rcv_evidence()
            candidate_ids = {str(value.get("candidate_id")) for value in evidence["candidate_field"]}
            required = {
                f"{RACE_ID}:gerald-l-heikes", f"{RACE_ID}:mary-peltola",
                f"{RACE_ID}:dan-s-sullivan", f"{RACE_ID}:daniel-j-sullivan-jr",
            }
            if str(row.get("race_id")) == RACE_ID and candidate_ids == required:
                return True, "limited_supported", "limited_validation_alaska_rcv_model"
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            pass
        return False, "unsupported", "ranked_choice_multiway_evidence_or_adapter_not_ready"
    if structure == "multiway_plurality":
        from midterms.model.multiway_plurality import multiway_probability_supported

        return multiway_probability_supported()
    if structure == PRINCIPAL_BINARY_CONTEST_STRUCTURE:
        if not non_major_identity_supported(row):
            return False, "unsupported", "principal_binary_identity_or_caucus_contract_incomplete"
        if int(n_compatible_polls) < 1:
            return False, "unsupported", "principal_binary_requires_compatible_polls"
        return (
            True,
            "limited_supported",
            "principal_binary_with_minor_residual_candidate_neutral_path",
        )
    if structure != NON_MAJOR_CONTEST_STRUCTURE:
        binary_value = row.get("binary_score_eligible")
        binary = (
            True
            if binary_value is None or pd.isna(binary_value)
            else bool(binary_value)
        )
        return (
            binary,
            "ordinary_binary_model" if binary else "unsupported",
            "ordinary_dem_vs_rep" if binary else "unsupported_binary_target",
        )
    if not non_major_identity_supported(row):
        return False, "unsupported", "non_major_identity_or_caucus_contract_incomplete"
    if int(n_compatible_polls) < 1:
        prior = pd.to_numeric(row.get("prior_lean"), errors="coerce")
        eligible_value = row.get("prior_production_eligible")
        prior_eligible = bool(eligible_value) if pd.notna(eligible_value) else False
        has_sourced_prior = bool(
            pd.notna(prior)
            and row.get("prior_source") == "observed_presidential_relative_v1"
            and prior_eligible
            and _present(row.get("prior_snapshot_sha256"))
            and _present(row.get("prior_provenance_sha256"))
        )
        if has_sourced_prior:
            return (
                True,
                "limited_supported_prior_only",
                "state_structural_prior_only_zero_candidate_compatible_polls",
            )
        return (
            False,
            "withheld",
            "zero_candidate_compatible_polls_and_no_eligible_structural_prior",
        )
    return True, "limited_supported", NON_MAJOR_ADAPTER_METHOD
