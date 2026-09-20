"""Synthetic tests for distributional mixture fitting; no project outcomes used."""

from __future__ import annotations

import hashlib
import json

import pytest

from midterms.model.empirical_mixture import empirical_crps, fit_predictive_mixture
from midterms.validation.stack_weights import (
    fit_stack_weights_from_nested_loo,
    load_oof_stack_weights,
    reproduce_weights,
    verify_reproducible,
)


def test_one_predictive_distribution_is_clearly_superior():
    draws = {
        "exact": {"case_a": [0.0, 0.0], "case_b": [2.0, 2.0]},
        "shifted": {"case_a": [4.0, 4.0], "case_b": [6.0, 6.0]},
    }
    fit = fit_predictive_mixture(draws, {"case_a": 0.0, "case_b": 2.0})
    assert fit["weights"]["exact"] == pytest.approx(1.0, abs=1e-8)
    assert fit["objective_crps"] == pytest.approx(0.0, abs=1e-8)


def test_mixture_beats_either_component():
    draws = {
        "left": {"case_a": [-1.0, -1.0], "case_b": [-1.0, -1.0]},
        "right": {"case_a": [1.0, 1.0], "case_b": [1.0, 1.0]},
    }
    fit = fit_predictive_mixture(draws, {"case_a": 0.0, "case_b": 0.0})
    assert fit["weights"]["left"] == pytest.approx(0.5, abs=1e-8)
    assert fit["weights"]["right"] == pytest.approx(0.5, abs=1e-8)
    assert fit["objective_crps"] == pytest.approx(0.5, abs=1e-8)
    assert fit["objective_crps"] < min(fit["individual_crps"].values())


def test_identical_distributions_and_deterministic_sampling():
    assert empirical_crps([-1.0, 1.0], 0.0) == pytest.approx(0.5)
    values = [float(i - 250) / 100.0 for i in range(500)]
    draws = {"first": {"a": values, "b": values},
             "second": {"a": list(reversed(values)), "b": list(reversed(values))}}
    truth = {"a": 0.0, "b": 0.0}
    one = fit_predictive_mixture(draws, truth, seed=17, max_draws=32)
    two = fit_predictive_mixture(draws, truth, seed=17, max_draws=32)
    assert one["weights"] == two["weights"]
    assert one["prediction_sha256"] == two["prediction_sha256"]
    assert one["objective_crps"] == pytest.approx(next(iter(one["individual_crps"].values())))
    assert len(set(one["individual_crps"].values())) == 1
    assert abs(sum(one["weights"].values()) - 1.0) < 1e-9
    assert all(weight >= 0 for weight in one["weights"].values())


def test_stack_artifact_reproduces_from_synthetic_frozen_draws(tmp_path):
    nested = tmp_path / "synthetic_nested.json"
    out = tmp_path / "synthetic_stack.json"
    synthetic_draws = {
        "first": {"a": [-1.0, -1.0], "b": [-1.0, -1.0]},
        "second": {"a": [1.0, 1.0], "b": [1.0, 1.0]},
    }
    draw_hash = hashlib.sha256(json.dumps(
        synthetic_draws, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    nested.write_text(json.dumps({
        "crps_by_fold": {"group_a": {"first": 1.0, "second": 1.0},
                         "group_b": {"first": 1.0, "second": 1.0}},
        "oof_draws": synthetic_draws,
        "frozen_draws_sha256": draw_hash,
        "oof_truths": {"a": 0.0, "b": 0.0},
        "spine_label": "first", "hierarchical_method": "synthetic",
        "years": ["group_a", "group_b"],
    }), encoding="utf-8")
    fitted = fit_stack_weights_from_nested_loo(nested_path=nested, out_path=out)
    assert fitted["reproduction"]["ok"] is True
    assert fitted["stacking_mode"] == "empirical_predictive_mixture_crps_v1"
    assert reproduce_weights(fitted) == pytest.approx(fitted["stack_weights"], abs=1e-9)
    loaded, _ = load_oof_stack_weights(path=out)
    assert loaded == pytest.approx(fitted["stack_weights"], abs=1e-9)
    nested_payload = json.loads(nested.read_text(encoding="utf-8"))
    nested_payload["oof_draws"]["first"]["a"][0] = -2.0
    nested.write_text(json.dumps(nested_payload), encoding="utf-8")
    assert verify_reproducible(fitted)["ok"] is False
    nested_payload["oof_draws"]["first"]["a"][0] = -1.0
    nested.write_text(json.dumps(nested_payload), encoding="utf-8")
    payload = json.loads(out.read_text(encoding="utf-8"))
    payload["matrix_sha256"] = "stale"
    assert verify_reproducible(payload)["ok"] is False
    old = {"stacking_mode": "mean_only", "stack_weights": {"first": 1.0}}
    out.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="stale stack"):
        load_oof_stack_weights(path=out)
