"""Synthetic stack identity and missing-distribution safeguards."""

from __future__ import annotations

import pytest

from midterms.model.ensemble import align_weights_to_spine
from midterms.validation.stack_weights import fit_stack_weights_from_oof


def test_align_weights_keeps_model_identifiers_distinct():
    weights = align_weights_to_spine(
        {"pymc_dynamic": 0.25, "state_space": 0.75}, spine="pymc"
    )
    assert weights["pymc_dynamic"] == 0.25
    assert "pymc" not in weights


def test_production_fit_requires_frozen_distributions():
    matrix = {"group_a": {"model_a": 1.0}, "group_b": {"model_a": 1.0}}
    with pytest.raises(ValueError, match="frozen OOF draws"):
        fit_stack_weights_from_oof(matrix)


def test_missing_candidate_draws_are_recorded_without_remapping():
    matrix = {
        "group_a": {"pymc": 1.0, "pymc_dynamic": 1.0},
        "group_b": {"pymc": 1.0, "pymc_dynamic": 1.0},
    }
    draws = {
        "pymc": {"case_a": [0.0, 0.0], "case_b": [0.0, 0.0]},
        "pymc_dynamic": {"case_a": [0.0, 0.0]},
    }
    fitted = fit_stack_weights_from_oof(
        matrix, oof_draws=draws, oof_truths={"case_a": 0.0, "case_b": 0.0}
    )
    assert fitted["stack_weights"] == {"pymc": 1.0}
    assert fitted["excluded_missing_predictions"]["pymc_dynamic"] == ["case_b"]
    assert fitted["no_weight_remapping"] is True
