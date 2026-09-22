"""Synthetic tests for cycle-cross-fitted stack reliability."""

from __future__ import annotations

import copy
import hashlib
import json

import numpy as np
import pytest

from midterms.model.empirical_mixture import (
    weighted_mixture_mean,
    widen_mixture_draws,
)
from midterms.validation.acceptance_gates import evaluate_acceptance_gates
from midterms.validation.stack_reliability_crossfit import (
    build_crossfit_report,
    fit_training_plan,
    select_uncertainty_scale,
    split_outer_cycle,
    validate_crossfit_artifact,
)


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _synthetic_nested() -> dict:
    years = [2018, 2020, 2022, 2024]
    truths = {}
    draws = {"pymc": {}, "state_space": {}}
    for year_index, year in enumerate(years):
        for case_index in range(3):
            case = f"{year}:60:case_{case_index}"
            sign = 1.0 if (year_index + case_index) % 2 == 0 else -1.0
            truths[case] = sign * (2.0 + 0.2 * case_index)
            draws["pymc"][case] = list(np.linspace(-1.5, 2.5, 24) + sign * 0.45)
            draws["state_space"][case] = list(np.linspace(-2.5, 1.5, 24) + sign * 0.35)
    crps = {
        str(year): {
            "pymc": 1.00 + 0.02 * index,
            "state_space": 0.98 + 0.03 * index,
        }
        for index, year in enumerate(years)
    }
    return {
        "freeze_before_truth": True,
        "no_weight_remapping": True,
        "years": years,
        "lead_days": [60],
        "spine_label": "pymc",
        "stack_training_protocol": "synthetic_cycle_crossfit",
        "model_version": "synthetic",
        "oof_draws": draws,
        "oof_truths": truths,
        "frozen_draws_sha256": _sha(draws),
        "crps_by_fold": crps,
    }


def _stack_source() -> dict:
    return {
        "temperature": 0.75,
        "optimizer_seed": 17,
        "optimizer_max_draws": 16,
        "stacking_mode": "empirical_predictive_mixture_crps_v1",
        "prediction_sha256": "synthetic",
    }


def test_outer_truth_cannot_change_its_fitted_weights_or_scale():
    nested = _synthetic_nested()
    first = build_crossfit_report(nested, _stack_source(), scales=(1.0, 1.2, 1.4))
    changed = copy.deepcopy(nested)
    heldout_case = "2018:60:case_0"
    changed["oof_truths"][heldout_case] *= -3.0
    second = build_crossfit_report(changed, _stack_source(), scales=(1.0, 1.2, 1.4))

    first_fold = first["folds"]["2018"]
    second_fold = second["folds"]["2018"]
    assert first_fold["training_cycles"] == [2020, 2022, 2024]
    assert first_fold["stack_weights"] == pytest.approx(second_fold["stack_weights"])
    assert (
        first_fold["scale_selection"]["selected_scale"]
        == second_fold["scale_selection"]["selected_scale"]
    )
    assert first_fold["training_truth_sha256"] == second_fold["training_truth_sha256"]
    assert first_fold["raw"] != second_fold["raw"]


def test_training_plan_signature_has_no_heldout_truth_and_scale_is_deterministic():
    nested = _synthetic_nested()
    split = split_outer_cycle(nested, 2020)
    kwargs = {
        "training_cycles": split["training_cycles"],
        "training_crps_by_fold": split["training_crps_by_fold"],
        "training_draws": split["training_draws"],
        "training_truths": split["training_truths"],
        "spine": "pymc",
        "temperature": 0.75,
        "seed": 7,
        "max_draws": 16,
        "scales": (1.0, 1.1, 1.2),
    }
    first = fit_training_plan(**kwargs)
    second = fit_training_plan(**kwargs)
    assert first["stack_weights"] == second["stack_weights"]
    assert first["scale_selection"] == second["scale_selection"]
    assert first["heldout_truth_available_to_fit"] is False
    assert first["heldout_scores_available_to_fit"] is False


def test_widening_identity_and_mean_preservation():
    draws = {"a": [-2.0, 0.0, 1.0], "b": [1.0, 3.0, 5.0]}
    weights = {"a": 0.4, "b": 0.6}
    identity = widen_mixture_draws(draws, weights, scale=1.0)
    assert identity["a"] == pytest.approx(draws["a"])
    assert identity["b"] == pytest.approx(draws["b"])
    before = weighted_mixture_mean(draws, weights)
    widened = widen_mixture_draws(draws, weights, scale=1.8)
    after = weighted_mixture_mean(widened, weights)
    assert after == pytest.approx(before, abs=1e-12)


def test_raw_and_calibrated_metrics_are_separate_and_nonproduction():
    report = build_crossfit_report(
        _synthetic_nested(), _stack_source(), scales=(1.0, 1.25),
    )
    assert report["raw_production_stack"]["calibration_applied"] is False
    assert report["raw_production_stack"]["g7_eligible"] is True
    assert report["experimental_calibrated_stack"]["calibration_applied"] is True
    assert report["experimental_calibrated_stack"]["production_adopted"] is False
    assert report["experimental_calibrated_stack"]["g7_eligible"] is False
    assert report["recommended_final_scale_if_adopted"]["status"] == "NOT_YET_PRODUCTION"


def test_crossfit_lineage_accepts_matching_sources_and_rejects_stale(tmp_path):
    nested = _synthetic_nested()
    stack = _stack_source()
    nested_path = tmp_path / "nested_component_loo.json"
    stack_path = tmp_path / "stack_weights_oof.json"
    nested_path.write_text(json.dumps(nested), encoding="utf-8")
    stack_path.write_text(json.dumps(stack), encoding="utf-8")
    lineage = {
        "nested_artifact_sha256": hashlib.sha256(nested_path.read_bytes()).hexdigest(),
        "stack_artifact_sha256": hashlib.sha256(stack_path.read_bytes()).hexdigest(),
    }
    report = build_crossfit_report(
        nested, stack, source_lineage=lineage, scales=(1.0, 1.2),
    )
    assert validate_crossfit_artifact(
        report, nested_path=nested_path, stack_path=stack_path,
    )["ok"] is True
    stack_path.write_text(json.dumps({**stack, "optimizer_seed": 99}), encoding="utf-8")
    check = validate_crossfit_artifact(
        report, nested_path=nested_path, stack_path=stack_path,
    )
    assert check["ok"] is False
    assert "stack artifact fingerprint mismatch" in check["failures"]


def test_g7_never_uses_experimental_calibration(monkeypatch, tmp_path):
    art = tmp_path / "artifacts"
    art.mkdir()
    nested = {
        "freeze_before_truth": True,
        "years": [2018, 2020],
        "crps_by_fold": {"2018": {"pymc": 1.0}, "2020": {"pymc": 1.0}},
        "g8_recommendations": {"state_space": {"recommend": "keep"}},
        "reliability": {
            "n": 100,
            "reliability": [{"bin_lo": 0.0, "bin_hi": 1.0, "n": 100, "mean_p": 0.5, "mean_y": 0.5}],
            "reliability_gate": {"calibration_claim_allowed": True, "n_overconfident": 0, "thin_sample": False},
        },
    }
    (art / "nested_component_loo.json").write_text(json.dumps(nested), encoding="utf-8")
    (art / "stack_weights_oof.json").write_text(json.dumps({
        "stack_weights": {"pymc": 1.0}, "reproduction": {"ok": True},
        "no_weight_remapping": True,
    }), encoding="utf-8")
    raw_gate = {
        "calibration_claim_allowed": False,
        "n_overconfident": 1,
        "thin_sample": False,
        "total_n": 100,
    }
    experimental_gate = {
        "calibration_claim_allowed": True,
        "n_overconfident": 0,
        "thin_sample": False,
        "total_n": 100,
    }
    crossfit = {
        "procedure": "cycle_cross_fitted_production_stack",
        "outer_cycles": [2018, 2020],
        "raw_production_stack": {
            "n": 100,
            "reliability": [{"bin_lo": 0.8, "bin_hi": 0.9, "n": 20, "mean_p": 0.85, "mean_y": 0.60}],
            "reliability_overconfidence": raw_gate,
            "calibration_applied": False,
            "g7_eligible": True,
        },
        "experimental_calibrated_stack": {
            "production_adopted": False,
            "g7_eligible": False,
            "reliability_overconfidence": experimental_gate,
        },
    }
    (art / "stack_reliability_crossfit_latest.json").write_text(
        json.dumps(crossfit), encoding="utf-8",
    )
    monkeypatch.setattr(
        "midterms.validation.stack_reliability_crossfit.validate_crossfit_artifact",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )
    report = evaluate_acceptance_gates(artifacts_dir=art, write=False)
    gate = report["gates"]["G7"]
    assert gate["status"] == "fail"
    assert gate["detail"]["source"] == "stack_reliability_crossfit.raw_production_stack"
    assert gate["detail"]["experimental_calibration_not_production"]["g7_eligible"] is False


def test_scale_selection_tie_prefers_one():
    draws = {"only": {"a": [0.0, 0.0], "b": [0.0, 0.0]}}
    truths = {"a": 0.0, "b": 0.0}
    result = select_uncertainty_scale(
        draws, truths, {"only": 1.0}, scales=(1.0, 1.2), seed=3, max_draws=2,
    )
    assert result["selected_scale"] == 1.0
