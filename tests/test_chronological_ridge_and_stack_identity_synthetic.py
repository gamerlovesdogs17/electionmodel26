"""Synthetic guards for fold chronology and separately identified stack members."""

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from midterms.model.challengers import select_chronological_training_rows
from midterms.pipeline.run_forecast import _available_stack_weights
from midterms.model.pymc_model import FitResult
from midterms.validation.nested_component_loo import _freeze_from_fit
from midterms.validation import nested_component_loo as nested


def test_ridge_training_excludes_holdout_later_and_unavailable_truth():
    races = pd.DataFrame([
        {"race_id": "old-known", "election_id": "group-2018", "ballot_status": "nominated"},
        {"race_id": "old-late", "election_id": "group-2020", "ballot_status": "nominated"},
        {"race_id": "holdout", "election_id": "group-2022", "ballot_status": "nominated"},
        {"race_id": "future", "election_id": "group-2024", "ballot_status": "nominated"},
    ])
    results = pd.DataFrame([
        {"race_id": "old-known", "election_id": "group-2018", "available_at": "2018-12-01",
         "score_eligible": True, "margin_value": 1.0},
        {"race_id": "old-late", "election_id": "group-2020", "available_at": "2023-01-01",
         "score_eligible": True, "margin_value": 2.0},
        {"race_id": "holdout", "election_id": "group-2022", "available_at": "2022-01-01",
         "score_eligible": True, "margin_value": 3.0},
        {"race_id": "future", "election_id": "group-2024", "available_at": "2021-01-01",
         "score_eligible": True, "margin_value": 4.0},
    ])
    selected = select_chronological_training_rows(
        races, results, holdout_year=2022, as_of=date(2022, 9, 1),
    )
    assert selected["race_id"].tolist() == ["old-known"]


def test_ridge_training_requires_availability_metadata():
    with pytest.raises(ValueError, match="available_at"):
        select_chronological_training_rows(
            pd.DataFrame({"race_id": ["one"], "election_id": ["group-2018"]}),
            pd.DataFrame({"race_id": ["one"], "election_id": ["group-2018"]}),
            holdout_year=2022, as_of=date(2022, 9, 1),
        )


def test_positive_dynamic_mass_cannot_be_reassigned_to_static():
    draws = {"pymc": np.zeros((2, 1))}
    weights = {"pymc": 0.25, "pymc_dynamic": 0.75}
    with pytest.raises(ValueError, match="pymc_dynamic"):
        _available_stack_weights(weights, draws, require_publishable=True)
    assert _available_stack_weights(weights, draws, require_publishable=False) == {"pymc": 0.25}


def test_validation_pymc_freeze_requires_convergence():
    fit = FitResult(
        race_ids=["synthetic-one"], states=["ZZ"],
        mean_margin=np.array([0.0]), sd_margin=np.array([1.0]),
        draws_margin=np.array([[-1.0], [1.0]]), house_effects={},
        diagnostics={"convergence": {"available": True, "r_hat_max": 1.2,
                                     "ess_bulk_min_frac": 0.2}}, method="pymc_dynamic",
    )
    args = dict(component="pymc_dynamic", election_id="synthetic-2022",
                holdout_year=2022, lead_days=60, as_of=date(2022, 9, 1), seed=7)
    with pytest.raises(ValueError, match="R-hat/ESS"):
        _freeze_from_fit(fit, **args)
    fit.diagnostics["convergence"]["r_hat_max"] = 1.01
    frozen = _freeze_from_fit(fit, **args)
    assert frozen.component == "pymc_dynamic"
    assert frozen.draws_by_race["synthetic-one"] == [-1.0, 1.0]


def test_static_and_dynamic_are_frozen_as_separate_synthetic_candidates(monkeypatch):
    def fit(method):
        return FitResult(
            race_ids=["synthetic-one"], states=["ZZ"],
            mean_margin=np.array([0.0]), sd_margin=np.array([1.0]),
            draws_margin=np.array([[-1.0], [1.0]]), house_effects={},
            diagnostics={"convergence": {"available": True, "r_hat_max": 1.01,
                                         "ess_bulk_min_frac": 0.5}}, method=method,
        )

    monkeypatch.setattr(nested, "_fit_hierarchical", lambda *args, method, **kwargs: fit(
        "pymc" if method == "pymc" else "fast_hierarchical_t"))
    monkeypatch.setattr(nested, "fit_pymc_dynamic", lambda *args, **kwargs: fit("pymc_dynamic"))
    monkeypatch.setattr(nested, "fit_state_space", lambda *args, **kwargs: fit("state_space"))
    monkeypatch.setattr(nested, "fit_poll_only_state_space", lambda *args, **kwargs: fit("state_space"))
    monkeypatch.setattr(nested, "fit_ridge_fundamentals", lambda *args, **kwargs: fit("ridge_fundamentals"))
    monkeypatch.setattr(nested, "BASELINES", {})
    snapshot = SimpleNamespace(as_of=date(2022, 9, 1), polls=pd.DataFrame(),
                               snapshot_id="synthetic-snapshot", prior_snapshot_sha256="a" * 64,
                               presidential_source_sha256="b" * 64)
    frozen = nested.freeze_component_predictions(
        snapshot, election_id="synthetic-2022", holdout_year=2022, lead_days=60,
        hierarchical_method="pymc", n_draws=2, seed=7,
    )
    assert frozen["pymc"].status == "ok"
    assert frozen["pymc_dynamic"].status == "ok"
    assert frozen["pymc"].component != frozen["pymc_dynamic"].component
    assert frozen["pymc"].prior_snapshot_sha256 == "a" * 64

    def failed_dynamic(*args, **kwargs):
        raise RuntimeError("synthetic dynamic failure")

    monkeypatch.setattr(nested, "fit_pymc_dynamic", failed_dynamic)
    failed = nested.freeze_component_predictions(
        snapshot, election_id="synthetic-2022", holdout_year=2022, lead_days=60,
        hierarchical_method="pymc", n_draws=2, seed=7,
    )
    assert failed["pymc"].status == "ok"
    assert failed["pymc_dynamic"].status == "failed"
    assert "synthetic dynamic failure" in failed["pymc_dynamic"].error
