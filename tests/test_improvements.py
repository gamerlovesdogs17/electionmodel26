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
    base = attach_poll_weights(snap.polls, as_of=snap.as_of)
    enop0 = global_enop(base)

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
    enop1 = global_enop(flooded_w)
    # Information must not scale linearly with 25 clones
    assert enop1 < enop0 + 8


def test_fundamentals_use_fundraising_and_midterm():
    wh = Warehouse()
    snap = wh.build_as_of("2022-09-01", "senate-2022")
    mu = fundamentals_mean(snap.races, generic_ballot=-2.0)
    assert len(mu) == (~snap.races["not_up"]).sum()
    assert np.isfinite(mu.to_numpy()).all()


def test_cycle_replay_scores_hierarchical():
    report = replay_cycle(
        2022, lead_days=(60, 30), n_draws=400, seed=3, allow_synthetic=True
    )
    assert "fast_hierarchical_t" in report["aggregate"]
    assert report["aggregate"]["fast_hierarchical_t"].get("n_leads", 0) >= 1
    assert "stack_weights" in report
    assert abs(sum(report["stack_weights"].values()) - 1.0) < 1e-6


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


def test_state_space_handles_nan_sample_size():
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
