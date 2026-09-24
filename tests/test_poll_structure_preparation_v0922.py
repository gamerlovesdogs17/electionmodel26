"""Cheap tests for v0.9.22 positive structural challengers and selection."""

from __future__ import annotations

from copy import deepcopy

import pytest

from midterms.model.poll_structure import PollStructureConfig
from midterms.validation.poll_structure_selection import (
    BASE_STRUCTURE,
    POLL_STRUCTURE_CANDIDATES,
    crossfit_structure_selection,
    select_structure,
)
from midterms.validation.stack_weights import EXCLUDE_FROM_STACK
from midterms.validation.structural_ablations import ablation_by_id, same_family_fit_spec


@pytest.mark.parametrize(
    ("identifier", "feature"),
    [
        ("hier_plus_study_effect", "study_effect"),
        ("hier_plus_sponsor_effect", "sponsor_effect"),
        ("hier_plus_questionnaire_effect", "questionnaire_effect"),
    ],
)
def test_positive_challenger_changes_exactly_one_field(identifier: str, feature: str):
    spec = same_family_fit_spec(
        base_method="pymc", base_seed=21,
        ablation=ablation_by_id(identifier),
        reference_poll_structure=PollStructureConfig(),
        reference_fit_config={"draws": 800, "tune": 800, "chains": 2},
    )
    assert spec["eligible"] is True
    assert spec["changed_features"] == [feature]
    assert spec["reference_config"]["poll_structure"][feature] is False
    assert spec["challenger_config"]["poll_structure"][feature] is True
    for other in {"study_effect", "sponsor_effect", "questionnaire_effect"} - {feature}:
        assert spec["reference_config"]["poll_structure"][other] is False
        assert spec["challenger_config"]["poll_structure"][other] is False
    assert spec["same_model_family"] is True
    assert spec["same_seed_policy"] is True


def test_study_addition_only_disables_documented_heuristic_downweight():
    spec = same_family_fit_spec(
        base_method="pymc", base_seed=4,
        ablation=ablation_by_id("hier_plus_study_effect"),
        reference_poll_structure=PollStructureConfig(),
    )
    assert spec["reference_config"]["poll_structure"]["effective_heuristic_study_downweight"] is True
    assert spec["challenger_config"]["poll_structure"]["effective_heuristic_study_downweight"] is False
    assert spec["changed_features"] == ["study_effect"]


def test_positive_challengers_are_never_stack_candidates():
    assert set(POLL_STRUCTURE_CANDIDATES[1:]) <= EXCLUDE_FROM_STACK


def _scores() -> dict[str, dict[str, float]]:
    return {
        "2018": {BASE_STRUCTURE: 3.0, "hier_plus_study_effect": 2.7,
                 "hier_plus_sponsor_effect": 3.2, "hier_plus_questionnaire_effect": 3.1},
        "2020": {BASE_STRUCTURE: 3.0, "hier_plus_study_effect": 2.8,
                 "hier_plus_sponsor_effect": 3.1, "hier_plus_questionnaire_effect": 3.2},
        "2022": {BASE_STRUCTURE: 3.0, "hier_plus_study_effect": 2.9,
                 "hier_plus_sponsor_effect": 3.2, "hier_plus_questionnaire_effect": 3.1},
        "2024": {BASE_STRUCTURE: 3.0, "hier_plus_study_effect": 2.6,
                 "hier_plus_sponsor_effect": 3.1, "hier_plus_questionnaire_effect": 3.2},
    }


def test_heldout_cycle_cannot_influence_its_structural_choice():
    original = crossfit_structure_selection(
        _scores(), source_nested_sha256="a" * 64, source_frozen_draws_sha256="b" * 64,
    )
    changed = deepcopy(_scores())
    changed["2024"]["hier_plus_study_effect"] = 99.0
    mutated = crossfit_structure_selection(
        changed, source_nested_sha256="a" * 64, source_frozen_draws_sha256="b" * 64,
    )
    original_fold = next(row for row in original["outer_folds"] if row["outer_heldout_cycle"] == "2024")
    mutated_fold = next(row for row in mutated["outer_folds"] if row["outer_heldout_cycle"] == "2024")
    assert original_fold["selected_structure"] == mutated_fold["selected_structure"]
    assert original_fold["selection"] == mutated_fold["selection"]
    assert original_fold["heldout_score"] != mutated_fold["heldout_score"]
    assert original_fold["heldout_truth_used_for_selection"] is False


def test_tied_or_weak_structure_evidence_chooses_base():
    tied = {
        "2018": {candidate: 1.0 for candidate in POLL_STRUCTURE_CANDIDATES},
        "2020": {candidate: 1.0 for candidate in POLL_STRUCTURE_CANDIDATES},
    }
    selected = select_structure(tied)
    assert selected["selected_structure"] == BASE_STRUCTURE
    assert selected["tie_policy"] == "base"


def test_crossfit_lineage_binds_frozen_oof_source():
    report = crossfit_structure_selection(
        _scores(), source_nested_sha256="1" * 64, source_frozen_draws_sha256="2" * 64,
    )
    assert report["source_nested_loo_sha256"] == "1" * 64
    assert report["source_frozen_draws_sha256"] == "2" * 64
    assert report["final_production_candidate_recommendation"]["untouched_fifth_cycle"] is False
    assert report["multi_term_interactions"] == "intentionally_deferred"
