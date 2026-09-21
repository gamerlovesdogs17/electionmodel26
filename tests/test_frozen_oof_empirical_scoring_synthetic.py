"""Synthetic-only checks for scoring sealed outer-fold distributions."""

import hashlib
import json
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from midterms.baselines.score import discrete_crps
from midterms.validation.nested_component_loo import (
    FrozenPrediction, _draws_fingerprint, rescore_frozen_oof_draws,
    score_frozen_predictions,
)
from midterms.model.pymc_model import FitResult


def test_frozen_prediction_crps_uses_draw_distribution() -> None:
    frozen = FrozenPrediction(
        component="synthetic_a", election_id="synthetic", holdout_year=2000,
        lead_days=30, as_of="2000-10-01", race_ids=["case-1"], means=[0.0],
        sds=[1.0], method="synthetic_a", n_draws=4, seed=1, status="ok",
        draws_by_race={"case-1": [-2.0, -1.0, 1.0, 2.0]},
    )
    truth = pd.DataFrame([{
        "race_id": "case-1", "score_eligible": True, "margin_value": 0.0,
    }])
    score = score_frozen_predictions({"synthetic_a": frozen}, truth)["synthetic_a"]
    assert score["crps_method"] == "exact_empirical_predictive_draws"
    assert score["crps"] == pytest.approx(discrete_crps([-2, -1, 1, 2], 0.0))
    assert score["crps"] != pytest.approx(score["crps_gaussian_diagnostic"])


def test_rescore_requires_unchanged_draw_archive() -> None:
    draws = {"synthetic_a": {"2000:30:case-1": [-2.0, -1.0, 1.0, 2.0]}}
    report = {
        "years": [2000], "lead_days": [30], "spine_label": "synthetic_a",
        "oof_draws": draws, "frozen_draws_sha256": _draws_fingerprint(draws),
        "oof_truths": {"2000:30:case-1": 0.0},
        "by_fold": {"2000": {"leads": {"30": {
            "synthetic_a": {"status": "ok", "n": 1, "crps": 9.0},
        }}}},
    }
    scored = rescore_frozen_oof_draws(report)
    assert scored["oof_crps_method"] == "exact_empirical_predictive_draws"
    assert scored["crps_by_fold"]["2000"]["synthetic_a"] == pytest.approx(
        discrete_crps([-2, -1, 1, 2], 0.0)
    )
    assert scored["by_fold"]["2000"]["leads"]["30"]["synthetic_a"]["crps_gaussian_diagnostic"] == 9.0
    draws["synthetic_a"]["2000:30:case-1"][0] = -3.0
    with pytest.raises(ValueError, match="changed"):
        rescore_frozen_oof_draws(report)


def test_inference_repair_freezes_before_truth_access(tmp_path, monkeypatch) -> None:
    from midterms.validation import nested_component_loo as module

    events: list[str] = []
    race_id = "synthetic-2000-AA"
    index_path = tmp_path / "nested_frozen.json"
    index = {"entries": [{
        "component": "pymc_dynamic", "holdout_year": 2000, "lead_days": 30,
        "as_of": "2000-10-08", "status": "failed", "seed": 7,
    }]}
    index_path.write_text(json.dumps(index))
    report_path = tmp_path / "nested.json"
    empty_draws: dict[str, dict[str, list[float]]] = {}
    report = {
        "years": [2000], "lead_days": [30], "spine_label": "pymc_dynamic",
        "oof_draws": empty_draws, "frozen_draws_sha256": _draws_fingerprint(empty_draws),
        "frozen_index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        "oof_truths": {f"2000:30:{race_id}": 1.0},
        "oof_means": {}, "oof_sds": {},
        "prior_snapshot_sha256_by_fold_lead": {"2000": {"30": "prior-sha"}},
        "presidential_source_sha256_by_fold_lead": {"2000": {"30": "source-sha"}},
        "failures": [{"year": 2000, "lead": 30, "component": "pymc_dynamic"}],
        "by_fold": {"2000": {"leads": {"30": {
            "pymc_dynamic": {"status": "failed", "n": 0},
        }}}},
    }
    report_path.write_text(json.dumps(report))

    class SyntheticWarehouse:
        def __init__(self) -> None:
            self.races = pd.DataFrame([{
                "election_id": "senate-2000", "election_day": "2000-11-07",
            }])

        def build_as_of(self, as_of, election_id):
            assert as_of == date(2000, 10, 8)
            assert election_id == "senate-2000"
            return SimpleNamespace(
                as_of=as_of, polls=pd.DataFrame(), snapshot_id="synthetic-snapshot",
                prior_snapshot_sha256="prior-sha",
                presidential_source_sha256="source-sha",
            )

        @property
        def results(self):
            events.append("truth_read")
            return pd.DataFrame([{
                "election_id": "senate-2000", "race_id": race_id,
                "score_eligible": True, "margin_value": 1.0,
            }])

    def synthetic_fit(snap, *, draws, tune, chains, seed, generic_ballot):
        events.append("fit")
        assert (draws, tune, chains, seed, generic_ballot) == (2000, 2000, 4, 7, 0.0)
        return FitResult(
            race_ids=[race_id], states=["AA"], mean_margin=np.array([0.0]),
            sd_margin=np.array([1.0]),
            draws_margin=np.array([[-1.0], [0.0], [1.0], [2.0]]),
            house_effects={}, method="pymc_dynamic",
            diagnostics={"draws": 2000, "tune": 2000, "chains": 4,
                         "convergence": {"available": True, "r_hat_max": 1.0,
                                         "ess_bulk_min_frac": 0.2}},
        )

    monkeypatch.setattr(module, "Warehouse", SyntheticWarehouse)
    monkeypatch.setattr(module, "fit_pymc_dynamic", synthetic_fit)
    original_freeze = module._freeze_from_fit

    def observed_freeze(*args, **kwargs):
        frozen = original_freeze(*args, **kwargs)
        events.append("freeze")
        return frozen

    monkeypatch.setattr(module, "_freeze_from_fit", observed_freeze)
    result = module.repair_failed_oof_inference(
        year=2000, lead_days=30, component="pymc_dynamic", out_path=report_path,
    )
    assert events == ["fit", "freeze", "truth_read"]
    assert result["remaining_failures"] == 0
    assert json.loads(report_path.read_text())["oof_crps_method"] == "exact_empirical_predictive_draws"
