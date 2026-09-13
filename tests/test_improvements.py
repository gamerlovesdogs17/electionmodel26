"""ENOP / poll weights, fundamentals, cycle replay, and ensemble tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from midterms.evidence.warehouse import Warehouse
from midterms.model.ensemble import softmax_neg_scores, stack_margin_draws
from midterms.model.fundamentals import fundamentals_mean
from midterms.model.poll_weights import attach_poll_weights, global_enop
from midterms.model.pymc_model import fit_fast_approximation
from midterms.pipeline.run_forecast import run_forecast
from midterms.validation.cycle_replay import replay_cycle


def test_expert_store_freshness_flags_stale(tmp_path, monkeypatch):
    from midterms.evidence import expert_ratings as er

    monkeypatch.setattr(er, "NORMALIZED_DIR", tmp_path)
    monkeypatch.setattr(er, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(er, "MANIFESTS_DIR", tmp_path / "manifests")
    (tmp_path / "raw" / "external").mkdir(parents=True)
    (tmp_path / "manifests").mkdir()
    df = pd.DataFrame(
        [
            {
                "election_id": "senate-2026",
                "state": "GA",
                "race_id": "senate-2026-GA",
                "rating": "Lean D",
                "implied_margin": 4.5,
                "source": "curated_research_snapshot",
                "available_at": "2026-08-01",
                "retrieved_at": "2026-08-01T00:00:00+00:00",
                "parser_version": "expert-ratings-v1",
            }
        ]
    )
    path = tmp_path / "expert_ratings.parquet"
    df.to_parquet(path, index=False)
    assert not er._expert_store_is_fresh(
        path, available_at="2026-09-13", prefer_wikipedia=True, max_age_hours=168.0
    )


def test_mode_pop_not_in_influence_weights():
    """Mode/population are likelihood offsets — must not also scale influence weights."""
    polls = pd.DataFrame(
        {
            "poll_id": ["a", "b"],
            "race_id": ["r1", "r1"],
            "pollster_id": ["P1", "P1"],
            "study_id": ["s1", "s2"],
            "field_end": ["2022-08-01", "2022-08-01"],
            "sample_size": [600, 600],
            "quality_weight": [1.0, 1.0],
            "partisan": [False, False],
            "mode": ["Live Phone", "IVR"],
            "population": ["LV", "A"],
            "two_party_margin": [2.0, 2.0],
        }
    )
    w = attach_poll_weights(polls, as_of=pd.Timestamp("2022-09-01").date())
    assert abs(float(w["influence_weight"].iloc[0]) - float(w["influence_weight"].iloc[1])) < 1e-9


def test_enop_grows_sublinearly_under_pollster_flood():
    wh = Warehouse()
    snap = wh.build_as_of("2022-09-01", "senate-2022")
    assert len(snap.polls) > 0

    # Flood: duplicate one race's polls many times from a single pollster/study
    race_id = snap.polls["race_id"].iloc[0]
    seed_row = snap.polls[snap.polls["race_id"] == race_id].iloc[0]
    clones = []
    for i in range(25):
        row = seed_row.copy()
        row["poll_id"] = f"FLOOD-{i}"
        row["study_id"] = "FLOOD-STUDY"
        row["pollster_id"] = "FloodPollster"
        clones.append(row)
    flooded = pd.concat([snap.polls, pd.DataFrame(clones)], ignore_index=True)
    flooded_w = attach_poll_weights(flooded, as_of=snap.as_of)
    race_w = flooded_w[flooded_w["race_id"] == race_id]
    flood_w = race_w[race_w["pollster_id"] == "FloodPollster"]["influence_weight"]
    other_w = race_w[race_w["pollster_id"] != "FloodPollster"]["influence_weight"]
    flood_share = float(flood_w.sum() / max(float(race_w["influence_weight"].sum()), 1e-9))
    # Uncapped flood would be ~25/26 ≈ 0.96; pollster+study caps must cut that
    assert flood_share < 0.90
    assert flood_share < (25.0 / 26.0) - 0.05
    if len(other_w):
        assert float(flood_w.max()) <= float(other_w.max()) * 3.0


def test_fundamentals_use_fundraising_and_midterm():
    wh = Warehouse()
    snap = wh.build_as_of("2022-09-01", "senate-2022")
    mu = fundamentals_mean(snap.races, generic_ballot=-2.0)
    assert len(mu) == (~snap.races["not_up"]).sum()
    assert np.isfinite(mu.to_numpy()).all()


def test_cycle_replay_scores_hierarchical():
    # CI uses fast; production OOS default is pymc
    report = replay_cycle(
        2022,
        lead_days=(60, 30),
        n_draws=400,
        seed=3,
        allow_synthetic=True,
        hierarchical_method="fast",
        include_fast_challenger=False,
    )
    assert report["spine_key"] == "fast_hierarchical_t"
    assert "fast_hierarchical_t" in report["aggregate"]
    assert report["aggregate"]["fast_hierarchical_t"].get("n_leads", 0) >= 1
    assert "stack_weights" in report
    assert abs(sum(report["stack_weights"].values()) - 1.0) < 1e-6


def test_cycle_replay_default_spine_is_pymc():
    import inspect
    from midterms.validation.cycle_replay import replay_cycle as rc

    assert inspect.signature(rc).parameters["hierarchical_method"].default == "pymc"


def test_ensemble_stack_mixture():
    w = softmax_neg_scores({"a": 2.0, "b": 4.0, "c": 3.0})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["a"] > w["b"]
    draws = {
        "a": np.zeros((100, 3)),
        "b": np.ones((100, 3)),
    }
    out = stack_margin_draws(draws, {"a": 0.5, "b": 0.5}, rng=np.random.default_rng(0))
    assert out.shape == (100, 3)
    assert 0.2 < out.mean() < 0.8


def test_stack_margin_draws_replaces_nan_components():
    good = np.full((40, 2), 3.0)
    bad = np.full((40, 2), np.nan)
    out = stack_margin_draws(
        {"good": good, "bad": bad},
        {"good": 0.4, "bad": 0.6},
        rng=np.random.default_rng(0),
    )
    assert np.isfinite(out).all()
    assert np.allclose(out, 3.0)


def test_generic_ballot_aggregate_not_single_poll():
    from midterms.evidence.ingest import generic_ballot_aggregate, generic_ballot_latest

    agg = generic_ballot_aggregate(as_of="2026-09-13")
    assert agg is not None
    assert agg["n_polls"] >= 2
    assert abs(float(agg["margin"])) < 12.5  # Winsorized headline
    # Latest single-poll path now aliases aggregate
    assert generic_ballot_latest(as_of="2026-09-13") == agg["margin"]


def test_control_calibration_moves_toward_target():
    from midterms.model.overlays import calibrate_draws_to_control

    rng = np.random.default_rng(1)
    draws = rng.normal(0.5, 6.0, size=(2500, 10))
    out, meta = calibrate_draws_to_control(
        draws, held_dem=47, control_p_dem=0.35, weight=0.9, n_steps=10
    )
    assert meta["enabled"] is True
    assert meta["p_after"] < meta["p_before"]
    assert abs(meta["p_after"] - meta["aim_p_dem"]) < 0.08
    assert meta["shift_pp"] < 0
    assert np.isfinite(out).all()


def test_peer_gate_rejects_null_margins():
    from midterms.validation.peer_gate import score_against_peers

    art = {
        "method": "ensemble_stack+overlays",
        "diagnostics": {"core_method": "pymc"},
        "generic_ballot": 2.0,
        "races": [{"race_id": "x", "mean_margin": None, "sd_margin": None, "p_dem": 0.5}],
        "peer_comparison": {
            "control_p_dem": {"ours": 0.55, "kalshi": 0.5, "ddhq": 0.48},
            "races": [
                {"ours": 0.55, "kalshi": 0.52, "ddhq": 0.5},
                {"ours": 0.6, "kalshi": 0.58, "ddhq": 0.55},
                {"ours": 0.4, "kalshi": 0.45, "ddhq": 0.42},
            ],
        },
    }
    rep = score_against_peers(art)
    assert rep["ok"] is False
    assert any("null margins" in r for r in rep["reasons"])



def test_align_weights_maps_fast_to_pymc():
    from midterms.model.ensemble import align_weights_to_spine, weights_from_oof_scores

    w = align_weights_to_spine({"fast_hierarchical_t": 0.4, "state_space": 0.6}, spine="pymc")
    assert "fast_hierarchical_t" not in w
    assert abs(w["pymc"] - 0.4) < 1e-9
    assert abs(w["state_space"] - 0.6) < 1e-9
    # Fold-pure: excluding a fold must not peek at its scores
    folds = {
        "2018": {"pymc": 4.0, "state_space": 5.0},
        "2020": {"pymc": 3.0, "state_space": 6.0},
        "2022": {"pymc": 2.0, "state_space": 7.0},
    }
    loo = weights_from_oof_scores(folds, exclude_fold="2022")
    all_f = weights_from_oof_scores(folds)
    assert loo != all_f
    assert abs(sum(loo.values()) - 1.0) < 1e-9


def test_control_calibrate_default_off():
    import inspect
    from midterms.pipeline.run_forecast import run_forecast

    sig = inspect.signature(run_forecast)
    assert sig.parameters["control_calibrate"].default is False


def test_peer_gate_control_gap_is_soft():
    from midterms.validation.peer_gate import score_against_peers

    art = {
        "method": "ensemble_stack+overlays",
        "diagnostics": {"spine_method": "pymc"},
        "generic_ballot": 2.0,
        "races": [
            {"race_id": "a", "mean_margin": 1.0, "sd_margin": 3.0, "p_dem": 0.55},
            {"race_id": "b", "mean_margin": -1.0, "sd_margin": 3.0, "p_dem": 0.45},
            {"race_id": "c", "mean_margin": 0.0, "sd_margin": 3.0, "p_dem": 0.5},
        ],
        "peer_comparison": {
            "control_p_dem": {"ours": 0.85, "kalshi": 0.48, "ddhq": 0.5},
            "races": [
                {"ours": 0.55, "kalshi": 0.52, "ddhq": 0.5},
                {"ours": 0.6, "kalshi": 0.58, "ddhq": 0.55},
                {"ours": 0.4, "kalshi": 0.45, "ddhq": 0.42},
            ],
        },
    }
    rep = score_against_peers(art)
    assert rep["control_soft"] is True
    assert rep["control_ok"] is False
    assert rep["ok"] is True  # soft control gap must not hard-fail
    assert any("informational" in r for r in rep["reasons"])


def test_pymc_error_budget_morris_split():
    from midterms.evidence.warehouse import Warehouse
    from midterms.model.pymc_model import fit_fast_approximation

    snap = Warehouse(ensure_fixtures=False).build_as_of("2026-09-13", "senate-2026")
    fit = fit_fast_approximation(snap, n_draws=80, seed=7)
    bud = fit.diagnostics["error_budget"]
    assert "future_movement_sd" in bud
    assert "terminal_error_sd" in bud
    assert bud["terminal_error_sd"] == 2.5
    # Future movement contracts toward ED
    snap_near = Warehouse(ensure_fixtures=False).build_as_of("2026-10-27", "senate-2026")
    fit_near = fit_fast_approximation(snap_near, n_draws=40, seed=7)
    assert fit_near.diagnostics["error_budget"]["future_movement_sd"] < bud["future_movement_sd"]


def test_fec_amendment_chain_prefers_latest_coverage():
    import pandas as pd
    from midterms.evidence.fec import shares_from_totals

    totals = pd.DataFrame(
        [
            {
                "candidate_id": "D1",
                "party": "DEM",
                "state": "TX",
                "receipts": 1.0,
                "disbursements": 0.0,
                "cash_on_hand_end_period": 0.0,
                "coverage_end_date": "2026-06-01",
                "available_at": "2026-06-01",
            },
            {
                "candidate_id": "D1",
                "party": "DEM",
                "state": "TX",
                "receipts": 9.0,
                "disbursements": 0.0,
                "cash_on_hand_end_period": 0.0,
                "coverage_end_date": "2026-09-01",
                "available_at": "2026-09-01",
            },
            {
                "candidate_id": "R1",
                "party": "REP",
                "state": "TX",
                "receipts": 1.0,
                "disbursements": 0.0,
                "cash_on_hand_end_period": 0.0,
                "coverage_end_date": "2026-06-01",
                "available_at": "2026-06-01",
            },
            {
                "candidate_id": "R1",
                "party": "REP",
                "state": "TX",
                "receipts": 1.0,
                "disbursements": 0.0,
                "cash_on_hand_end_period": 0.0,
                "coverage_end_date": "2026-09-01",
                "available_at": "2026-09-01",
            },
        ]
    )
    mid = shares_from_totals(totals, "senate-2026", 2026, as_of="2026-07-01")
    late = shares_from_totals(totals, "senate-2026", 2026, as_of="2026-09-13")
    assert float(mid.loc[mid["state"] == "TX", "fundraising_share"].iloc[0]) == 0.5
    assert float(late.loc[late["state"] == "TX", "fundraising_share"].iloc[0]) == 0.9
    assert ">" in str(late.loc[late["state"] == "TX", "amendment_chain"].iloc[0])
    from midterms.model.state_space import _safe_sample_size, fit_state_space
    from midterms.evidence.warehouse import Warehouse

    assert _safe_sample_size(float("nan")) == 500.0
    assert _safe_sample_size(None) == 500.0
    assert _safe_sample_size(20) == 50.0
    wh = Warehouse(ensure_fixtures=False)
    snap = wh.build_as_of("2026-09-13", "senate-2026")
    fit = fit_state_space(snap, n_draws=60, seed=2, generic_ballot=-1.0)
    assert np.isfinite(fit.mean_margin).all()
    assert np.isfinite(fit.draws_margin).all()
    for rid in ("senate-2026-AK", "senate-2026-VA", "senate-2026-OH"):
        assert rid in fit.race_ids


def test_forecast_ensemble_artifact(tmp_path):
    result = run_forecast(
        method="fast", draws=300, seed=19, ensemble=True, out_dir=tmp_path
    )
    art = result["artifact"]
    assert art["method"] in {"ensemble_stack", "fast_hierarchical_t"} or art["method"].startswith(
        "ensemble_stack"
    ) or art["method"].startswith("fast_hierarchical_t")
    assert "enop_global" in art["diagnostics"]
    assert len(art["races"]) >= 30
