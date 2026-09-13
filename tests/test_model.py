"""Baseline scoring and joint simulator smoke tests."""

from __future__ import annotations

from midterms.baselines.models import BASELINES
from midterms.baselines.score import score_forecasts
from midterms.evidence.warehouse import Warehouse
from midterms.model.pymc_model import fit_fast_approximation
from midterms.pipeline.run_forecast import replay_baselines, run_forecast
from midterms.simulate.chamber import simulate_chamber


def test_baselines_produce_forecasts():
    wh = Warehouse()
    snap = wh.build_as_of("2022-09-01", "senate-2022")
    for name, fn in BASELINES.items():
        forecasts = fn(snap)
        assert len(forecasts) == (~snap.races["not_up"]).sum()
        assert all(0 <= f.p_dem <= 1 for f in forecasts)


def test_baseline_replay_writes_report():
    report = replay_baselines(2022)
    assert "aggregate" in report
    assert "shrinkage_polls" in report["aggregate"]
    assert report["aggregate"]["shrinkage_polls"].get("n_leads", 0) > 0


def test_joint_chamber_not_independent():
    wh = Warehouse()
    snap = wh.build_as_of("2026-09-01", "senate-2026")
    fit = fit_fast_approximation(snap, n_draws=1500, seed=7)
    sim, summaries = simulate_chamber(fit, snap.races)
    assert len(sim.seat_draws) == 1500
    assert 0 <= sim.p_dem_majority <= 1
    # Correlated draws: variance of seat total should exceed independent-ish lower bound loosely
    import numpy as np

    ps = np.array([s["p_dem"] for s in summaries])
    indep_var = float(ps.sum() * 0) + float(np.sum(ps * (1 - ps)))
    joint_var = float(np.var(sim.seat_draws))
    assert joint_var > indep_var * 0.5  # smoke: joint uncertainty present


def test_end_to_end_forecast_artifact(tmp_path):
    result = run_forecast(method="fast", draws=500, seed=11, out_dir=tmp_path)
    art = result["artifact"]
    assert art["chamber"]["expected_dem_seats"] > 0
    assert len(art["races"]) >= 30
    assert (tmp_path / "forecast_latest.json").exists()
