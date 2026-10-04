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
NON_MAJOR_ADAPTER_METHOD = "limited_validation_exception_model-v1"
NON_MAJOR_TARGET = "modeled_candidate_margin"


def _present(value: Any) -> bool:
    return value is not None and not pd.isna(value) and bool(str(value).strip())


def non_major_identity_supported(row: Mapping[str, Any] | pd.Series) -> bool:
    """Return whether a row has the explicit identity/caucus contract.

    Poll availability is intentionally checked elsewhere.  The adapter only
    represents an Independent/non-major modeled candidate against a Republican
    opponent, with both caucus mappings explicitly declared.
    """

    return bool(
        str(row.get("contest_structure") or "") == NON_MAJOR_CONTEST_STRUCTURE
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
        return False, "unsupported", "ranked_choice_multiway_not_supported"
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
