"""Tests for same-family challenger exclusion from stack and config selection.

Covers four invariants:
  1. EXCLUDE_FROM_STACK / SPECIFICATION_CHALLENGERS includes every known
     same-family challenger (state-space and national-env variants).
  2. Production stack keys can never simultaneously include a reference model
     and same-family challengers from the same family.
  3. select_same_family_configs correctly picks the winning config or retains
     the reference when no challenger earns replacement.
  4. Selected configs bind into candidate spec identity via ordinary-statistical-
     spec SHA256.
"""

from __future__ import annotations

import pytest

from midterms.model.challengers import STATE_SPACE_CHALLENGERS, STATE_SPACE_REFERENCE_CONFIG
from midterms.model.national_environment_ablations import NATIONAL_ENVIRONMENT_ABLATIONS
from midterms.validation.same_family_selection import (
    NATIONAL_ENVIRONMENT_REFERENCE_CONFIG,
    NATIONAL_ENVIRONMENT_REFERENCE_NAME,
    STATE_SPACE_REFERENCE_NAME,
    select_same_family_configs,
)
from midterms.validation.stack_weights import (
    EXCLUDE_FROM_STACK,
    SPECIFICATION_CHALLENGERS,
    STACK_CANDIDATES,
    assert_no_same_family_stack_leakage,
    fit_stack_weights_from_oof,
    specification_challenger_names,
)

# ---------------------------------------------------------------------------
# 1. Exclusion set correctness
# ---------------------------------------------------------------------------


def test_exclude_from_stack_equals_specification_challengers():
    """EXCLUDE_FROM_STACK must be identical to SPECIFICATION_CHALLENGERS."""
    assert EXCLUDE_FROM_STACK == SPECIFICATION_CHALLENGERS


def test_specification_challenger_names_returns_frozen_set():
    names = specification_challenger_names()
    assert isinstance(names, frozenset)
    assert names == SPECIFICATION_CHALLENGERS


def test_all_state_space_challengers_in_specification_challengers():
    """Every STATE_SPACE_CHALLENGERS key must be banned from the stack."""
    for name in STATE_SPACE_CHALLENGERS:
        assert name in SPECIFICATION_CHALLENGERS, (
            f"State-space challenger {name!r} missing from SPECIFICATION_CHALLENGERS"
        )


def test_all_national_env_challengers_in_specification_challengers():
    """Every NATIONAL_ENVIRONMENT_ABLATIONS key must be banned from the stack."""
    for name in NATIONAL_ENVIRONMENT_ABLATIONS:
        assert name in SPECIFICATION_CHALLENGERS, (
            f"National-env challenger {name!r} missing from SPECIFICATION_CHALLENGERS"
        )


def test_expected_state_space_names_are_excluded():
    """Spot-check the three required state-space same-family names."""
    required = {
        "state_space_no_ed_fund_repull",
        "state_space_process_sd_0_5",
        "state_space_process_sd_1_2",
    }
    assert required <= SPECIFICATION_CHALLENGERS


def test_expected_national_env_names_are_excluded():
    """Spot-check the four required national-environment ablation names."""
    required = {
        "no_generic_ballot",
        "no_approval",
        "no_midterm_outparty",
        "no_income_economic",
    }
    assert required <= SPECIFICATION_CHALLENGERS


def test_specification_challengers_and_stack_candidates_are_disjoint():
    """No model can be both a stack candidate and a specification challenger."""
    overlap = SPECIFICATION_CHALLENGERS & STACK_CANDIDATES
    assert not overlap, (
        f"Models appear in both STACK_CANDIDATES and SPECIFICATION_CHALLENGERS: {overlap}"
    )


def test_hier_ablations_still_excluded():
    """Original hier_* structural ablations remain in the exclusion set."""
    hier_ablations = {
        "hier_no_similarity",
        "hier_no_terminal_race",
        "hier_no_study_effect",
        "hier_no_sponsor_effect",
        "hier_no_questionnaire_effect",
        "hier_plus_study_effect",
        "hier_plus_sponsor_effect",
        "hier_plus_questionnaire_effect",
    }
    assert hier_ablations <= SPECIFICATION_CHALLENGERS


# ---------------------------------------------------------------------------
# 2. Stack leakage guard
# ---------------------------------------------------------------------------


def test_assert_no_same_family_stack_leakage_passes_for_clean_weights():
    """Clean production weights should pass the leakage check without error."""
    clean = {"pymc": 0.4, "state_space": 0.3, "ridge_fundamentals": 0.3}
    assert_no_same_family_stack_leakage(clean)  # must not raise


def test_assert_no_same_family_stack_leakage_passes_for_zero_weight():
    """A challenger with weight 0.0 is technically absent — should not raise."""
    weights = {"pymc": 1.0, "state_space_no_ed_fund_repull": 0.0}
    assert_no_same_family_stack_leakage(weights)


def test_assert_no_same_family_stack_leakage_raises_for_state_space_challenger():
    bad = {"state_space": 0.5, "state_space_no_ed_fund_repull": 0.5}
    with pytest.raises(ValueError, match="same-family"):
        assert_no_same_family_stack_leakage(bad)


def test_assert_no_same_family_stack_leakage_raises_for_nat_env_challenger():
    bad = {"ridge_fundamentals": 0.6, "no_generic_ballot": 0.4}
    with pytest.raises(ValueError, match="same-family"):
        assert_no_same_family_stack_leakage(bad)


def test_assert_no_same_family_stack_leakage_raises_for_process_sd_challenger():
    bad = {"pymc": 0.7, "state_space_process_sd_0_5": 0.3}
    with pytest.raises(ValueError, match="same-family"):
        assert_no_same_family_stack_leakage(bad)


def test_fit_stack_excludes_state_space_challenger_from_mixture():
    """When crps matrix includes a same-family challenger, it is filtered out."""
    matrix = {
        "2022": {
            "pymc": 0.30,
            "state_space": 0.35,
            "state_space_no_ed_fund_repull": 0.29,  # would "win" if included
        },
        "2020": {
            "pymc": 0.32,
            "state_space": 0.33,
            "state_space_no_ed_fund_repull": 0.31,
        },
    }
    draws = {
        "pymc": {"case_a": [0.0, 0.0], "case_b": [0.0, 0.0]},
        "state_space": {"case_a": [0.0, 0.0], "case_b": [0.0, 0.0]},
        # challenger draws are present but must not be used
        "state_space_no_ed_fund_repull": {"case_a": [0.0, 0.0], "case_b": [0.0, 0.0]},
    }
    fitted = fit_stack_weights_from_oof(
        matrix,
        oof_draws=draws,
        oof_truths={"case_a": 0.0, "case_b": 0.0},
    )
    weights = fitted["stack_weights"]
    assert "state_space_no_ed_fund_repull" not in weights
    assert_no_same_family_stack_leakage(weights)


def test_fit_stack_excludes_nat_env_challenger_from_mixture():
    """National-env challengers in the CRPS matrix are filtered before mixture fit."""
    matrix = {
        "2022": {"ridge_fundamentals": 0.50, "no_generic_ballot": 0.44},
        "2020": {"ridge_fundamentals": 0.48, "no_generic_ballot": 0.45},
    }
    draws = {
        "ridge_fundamentals": {"case_a": [0.0, 0.0], "case_b": [0.0, 0.0]},
        "no_generic_ballot": {"case_a": [0.0, 0.0], "case_b": [0.0, 0.0]},
    }
    fitted = fit_stack_weights_from_oof(
        matrix,
        oof_draws=draws,
        oof_truths={"case_a": 0.0, "case_b": 0.0},
    )
    weights = fitted["stack_weights"]
    assert "no_generic_ballot" not in weights
    assert_no_same_family_stack_leakage(weights)


# ---------------------------------------------------------------------------
# 3. Same-family config selection
# ---------------------------------------------------------------------------


def test_select_retains_state_space_reference_when_no_challenger_scored():
    """No challenger data → retain reference state-space config."""
    result = select_same_family_configs({"crps_by_fold": {}})
    assert result["selected_state_space_name"] == STATE_SPACE_REFERENCE_NAME
    assert result["selected_state_space_config"] == dict(STATE_SPACE_REFERENCE_CONFIG)


def test_select_retains_nat_env_reference_when_no_challenger_scored():
    """No challenger data → retain all-enabled national-environment config."""
    result = select_same_family_configs({"crps_by_fold": {}})
    assert result["selected_national_environment_name"] == NATIONAL_ENVIRONMENT_REFERENCE_NAME
    assert result["selected_national_environment_config"] == dict(NATIONAL_ENVIRONMENT_REFERENCE_CONFIG)
    # All terms must be enabled
    assert all(v is True for v in result["selected_national_environment_config"].values())


def test_select_picks_state_space_challenger_when_majority_wins():
    """A challenger that beats reference on ≥ ceil(n/2) folds is selected."""
    crps_by_fold = {
        "2022": {"state_space": 0.50, "state_space_no_ed_fund_repull": 0.40},
        "2020": {"state_space": 0.48, "state_space_no_ed_fund_repull": 0.42},
        "2018": {"state_space": 0.45, "state_space_no_ed_fund_repull": 0.43},
    }
    result = select_same_family_configs({"crps_by_fold": crps_by_fold})
    assert result["selected_state_space_name"] == "state_space_no_ed_fund_repull"
    assert result["selected_state_space_config"]["fund_pull"] == 0.0


def test_select_retains_reference_when_challenger_loses_on_majority():
    """A challenger that loses on majority of folds does not replace reference."""
    crps_by_fold = {
        "2022": {"state_space": 0.40, "state_space_no_ed_fund_repull": 0.50},
        "2020": {"state_space": 0.42, "state_space_no_ed_fund_repull": 0.48},
        "2018": {"state_space": 0.39, "state_space_no_ed_fund_repull": 0.41},
    }
    result = select_same_family_configs({"crps_by_fold": crps_by_fold})
    assert result["selected_state_space_name"] == STATE_SPACE_REFERENCE_NAME
    assert result["selected_state_space_config"] == dict(STATE_SPACE_REFERENCE_CONFIG)


def test_select_minority_win_does_not_displace_reference():
    """Challenger winning only 1 out of 3 folds does not beat the majority threshold."""
    # majority threshold for n=3: max(1, (3+1)//2) = 2 wins required
    crps_by_fold = {
        "2022": {"state_space": 0.50, "state_space_process_sd_0_5": 0.48},  # challenger wins
        "2020": {"state_space": 0.46, "state_space_process_sd_0_5": 0.48},  # reference wins
        "2018": {"state_space": 0.44, "state_space_process_sd_0_5": 0.47},  # reference wins
    }
    result = select_same_family_configs({"crps_by_fold": crps_by_fold})
    # 1 win out of 3 folds: needs 2, so reference is retained
    assert result["selected_state_space_name"] == STATE_SPACE_REFERENCE_NAME


def test_select_picks_best_state_space_challenger_when_multiple_win():
    """When multiple challengers beat reference, select the one with lower mean CRPS."""
    crps_by_fold = {
        "2022": {
            "state_space": 0.50,
            "state_space_no_ed_fund_repull": 0.43,   # better
            "state_space_process_sd_0_5": 0.46,       # also better but worse than no_repull
        },
        "2020": {
            "state_space": 0.48,
            "state_space_no_ed_fund_repull": 0.40,
            "state_space_process_sd_0_5": 0.44,
        },
        "2018": {
            "state_space": 0.45,
            "state_space_no_ed_fund_repull": 0.38,
            "state_space_process_sd_0_5": 0.42,
        },
    }
    result = select_same_family_configs({"crps_by_fold": crps_by_fold})
    assert result["selected_state_space_name"] == "state_space_no_ed_fund_repull"


def test_select_disables_nat_env_term_when_ablation_wins():
    """An ablation that beats ridge_fundamentals on majority of folds disables the term."""
    crps_by_fold = {
        "2022": {"ridge_fundamentals": 0.50, "no_generic_ballot": 0.44},
        "2020": {"ridge_fundamentals": 0.48, "no_generic_ballot": 0.45},
        "2018": {"ridge_fundamentals": 0.46, "no_generic_ballot": 0.44},
    }
    result = select_same_family_configs({"crps_by_fold": crps_by_fold})
    assert result["selected_national_environment_config"]["generic_ballot_enabled"] is False
    # All other terms should still be enabled
    assert result["selected_national_environment_config"]["approval_enabled"] is True
    assert result["selected_national_environment_config"]["midterm_outparty_enabled"] is True
    assert result["selected_national_environment_config"]["income_enabled"] is True


def test_select_keeps_nat_env_term_when_ablation_loses():
    """If removing a term hurts CRPS, keep the term enabled."""
    crps_by_fold = {
        "2022": {"ridge_fundamentals": 0.44, "no_approval": 0.50},
        "2020": {"ridge_fundamentals": 0.45, "no_approval": 0.48},
    }
    result = select_same_family_configs({"crps_by_fold": crps_by_fold})
    assert result["selected_national_environment_config"]["approval_enabled"] is True


def test_select_audit_bundle_contains_both_families():
    result = select_same_family_configs({"crps_by_fold": {}})
    audit = result["same_family_selection_audit"]
    assert "state_space" in audit
    assert "national_environment" in audit
    assert "challengers_scored" in audit["state_space"]
    assert "challengers_scored" in audit["national_environment"]


# ---------------------------------------------------------------------------
# 4. Selected configs bind into ordinary-statistical-spec SHA256
# ---------------------------------------------------------------------------


def test_selected_state_space_config_in_ordinary_spec_fields():
    from midterms.validation.validated_model_spec import ORDINARY_STATISTICAL_SPEC_FIELDS
    assert "selected_state_space_config" in ORDINARY_STATISTICAL_SPEC_FIELDS


def test_selected_national_environment_config_in_ordinary_spec_fields():
    from midterms.validation.validated_model_spec import ORDINARY_STATISTICAL_SPEC_FIELDS
    assert "selected_national_environment_config" in ORDINARY_STATISTICAL_SPEC_FIELDS


def _base_spec() -> dict:
    """Minimal synthetic candidate spec payload."""
    return {
        "schema_version": "validated-model-spec-candidate-v1",
        "model_version": "test-model-v0",
        "evidence_bundle_id": "eb-synth-001",
        "evidence_bundle_sha256": "aabbcc112233",
        "selected_structure_id": "pymc",
        "selected_poll_structure": {"study_effect": False, "sponsor_effect": False},
        "selected_poll_structure_id": "psc-synth-000",
        "historical_cycles": [2018, 2020, 2022, 2024],
        "lead_cutoffs_days": [60, 30],
        "historical_structural_feature_schema": "historical-structural-features-v1",
        "structural_feature_enables": {
            "similarity_terminal": True,
            "terminal_race": True,
        },
        "selected_state_space_config": {
            "fund_pull": 0.35,
            "flat_prior": False,
            "process_sd_per_sqrt_day": 0.8,
        },
        "selected_national_environment_config": {
            "generic_ballot_enabled": True,
            "approval_enabled": True,
            "midterm_outparty_enabled": True,
            "income_enabled": True,
        },
    }


def test_changing_state_space_config_changes_ordinary_spec_sha256():
    from midterms.validation.validated_model_spec import ordinary_statistical_spec_sha256

    spec_a = _base_spec()
    spec_b = dict(spec_a)
    spec_b["selected_state_space_config"] = {
        "fund_pull": 0.0,
        "flat_prior": False,
        "process_sd_per_sqrt_day": 0.8,
    }
    assert ordinary_statistical_spec_sha256(spec_a) != ordinary_statistical_spec_sha256(spec_b), (
        "Changing selected_state_space_config must change ordinary_statistical_spec_sha256"
    )


def test_changing_national_environment_config_changes_ordinary_spec_sha256():
    from midterms.validation.validated_model_spec import ordinary_statistical_spec_sha256

    spec_a = _base_spec()
    spec_b = dict(spec_a)
    spec_b["selected_national_environment_config"] = {
        "generic_ballot_enabled": False,
        "approval_enabled": True,
        "midterm_outparty_enabled": True,
        "income_enabled": True,
    }
    assert ordinary_statistical_spec_sha256(spec_a) != ordinary_statistical_spec_sha256(spec_b), (
        "Changing selected_national_environment_config must change ordinary_statistical_spec_sha256"
    )


def test_identical_configs_produce_identical_ordinary_spec_sha256():
    from midterms.validation.validated_model_spec import ordinary_statistical_spec_sha256

    spec_a = _base_spec()
    spec_b = _base_spec()
    assert ordinary_statistical_spec_sha256(spec_a) == ordinary_statistical_spec_sha256(spec_b)
