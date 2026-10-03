"""Cheap tests for v0.9.22 positive structural challengers and selection."""

from __future__ import annotations

from copy import deepcopy

import pytest

from midterms.model.poll_structure import PollStructureConfig, add_optional_poll_effects
from midterms.model.pymc_model import _noncentered_normal, _noncentered_student_t
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


def test_study_effect_uses_equivalent_noncentered_parameterization():
    class Symbol:
        __array_priority__ = 1000

        def __mul__(self, other):
            return Symbol()

        __rmul__ = __mul__

        def __radd__(self, other):
            return Symbol()

        def __getitem__(self, item):
            return Symbol()

    class FakePM:
        def __init__(self):
            self.normal_calls = []
            self.deterministics = []

        def HalfNormal(self, name, scale):
            return Symbol()

        def Normal(self, name, mu, sigma, dims):
            self.normal_calls.append((name, mu, sigma, dims))
            return Symbol()

        def Deterministic(self, name, value, dims):
            self.deterministics.append((name, dims))
            return Symbol()

    pm = FakePM()
    _, active = add_optional_poll_effects(
        pm,
        {"poll_y": [0.0, 1.0], "study_ids": ["a", "b"], "poll_study": [0, 1],
         "sponsor_ids": [], "questionnaire_ids": []},
        PollStructureConfig(study_effect=True),
    )
    assert pm.normal_calls == [("study_raw", 0.0, 1.0, "study")]
    assert pm.deterministics == [("study_eff", "study")]
    assert active["study"] == "shared_latent_deviation_noncentered"


def test_core_scale_mixtures_use_equivalent_noncentered_parameterization():
    class Symbol:
        def __init__(self, value):
            self.value = value

        def __mul__(self, other):
            return Symbol(("mul", self.value, getattr(other, "value", other)))

        __rmul__ = __mul__

        def __add__(self, other):
            return Symbol(("add", self.value, getattr(other, "value", other)))

        def __radd__(self, other):
            return Symbol(("add", getattr(other, "value", other), self.value))

    class FakePM:
        def __init__(self):
            self.calls = []
            self.deterministics = []

        def Normal(self, name, **kwargs):
            self.calls.append(("Normal", name, kwargs))
            return Symbol(name)

        def StudentT(self, name, **kwargs):
            self.calls.append(("StudentT", name, kwargs))
            return Symbol(name)

        def Deterministic(self, name, value, **kwargs):
            self.deterministics.append((name, value.value, kwargs))
            return Symbol(name)

    pm = FakePM()
    sigma = Symbol("sigma")
    _noncentered_student_t(pm, "local", nu=5, sigma=sigma, dims="race")
    _noncentered_normal(pm, "mode_eff", mu=2.0, sigma=sigma, dims="mode")

    assert pm.calls == [
        ("StudentT", "local_raw", {"nu": 5, "mu": 0.0, "sigma": 1.0, "dims": "race"}),
        ("Normal", "mode_eff_raw", {"mu": 0.0, "sigma": 1.0, "dims": "mode"}),
    ]
    assert pm.deterministics[0] == (
        "local", ("mul", "sigma", "local_raw"), {"dims": "race"},
    )
    assert pm.deterministics[1] == (
        "mode_eff", ("add", 2.0, ("mul", "sigma", "mode_eff_raw")), {"dims": "mode"},
    )


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
