"""Audit P2.3: MCSE / convergence / deterministic replay."""

from __future__ import annotations

import numpy as np

from midterms.validation.numerical_quality import (
    MCSE_CONTROL_MAX,
    MIN_SIM_DRAWS,
    chamber_mcse,
    deterministic_replay_check,
    evaluate_numerical_quality,
    mcse_bernoulli,
    race_win_mcse,
)


def test_mcse_bernoulli_scales_with_n():
    assert mcse_bernoulli(0.5, 100) > mcse_bernoulli(0.5, 10_000)
    assert abs(mcse_bernoulli(0.5, 2500) - 0.01) < 1e-4


def test_chamber_and_race_mcse():
    rng = np.random.default_rng(0)
    seats = rng.normal(52, 2, size=3000)
    seats = np.clip(np.round(seats), 40, 60)
    ch = chamber_mcse(seats, majority_threshold=51)
    assert ch["n_draws"] == 3000
    assert ch["mcse_p_dem_control"] <= MCSE_CONTROL_MAX
    draws = rng.normal(0, 5, size=(3000, 8))
    race = race_win_mcse(draws)
    assert race["n_races"] == 8
    assert race["max_mcse_p_dem"] < 0.02


def test_evaluate_numerical_quality_passes_with_enough_draws():
    rng = np.random.default_rng(1)
    seats = rng.integers(48, 56, size=MIN_SIM_DRAWS)
    draws = rng.normal(1.0, 4.0, size=(MIN_SIM_DRAWS, 5))
    report = evaluate_numerical_quality(
        seat_draws=seats,
        draws_margin=draws,
        n_posterior_samples=8000,
        draws=2000,
        tune=2000,
        chains=4,
        convergence={
            "available": True,
            "r_hat_max": 1.01,
            "ess_bulk_min": 400,
            "ess_bulk_min_frac": 0.2,
            "sampler_health": {
                "available": True,
                "divergence_available": True,
                "n_divergent": 0,
                "divergence_fraction": 0.0,
                "tree_depth_available": True,
                "n_tree_depth_hits": 0,
                "bfmi_available": True,
                "bfmi_min": 0.8,
            },
        },
        seed=7,
        publishable=True,
    )
    assert report["audit_item"] == "P2.3"
    assert report["ok"] is True
    assert report["deterministic_replay"]["ok"] is True


def test_publication_floor_rejects_demo_sampling_even_with_good_convergence():
    report = evaluate_numerical_quality(
        n_posterior_samples=1600,
        draws=800,
        tune=800,
        chains=2,
        convergence={"available": True, "r_hat_max": 1.01, "ess_bulk_min_frac": 0.2},
        publishable=True,
    )
    assert report["ok"] is False
    assert report["thresholds"]["min_posterior_samples"] == 8000
    assert {c["name"] for c in report["checks"] if not c["ok"]} >= {
        "min_posterior_samples", "production_draws", "production_tune", "production_chains"
    }


def test_evaluate_fails_when_too_few_draws_publishable():
    seats = np.ones(100)
    report = evaluate_numerical_quality(
        seat_draws=seats,
        draws_margin=np.zeros((100, 3)),
        n_posterior_samples=100,
        publishable=True,
    )
    assert report["ok"] is False
    assert any("sim draws" in a or "MCSE" in a or "posterior" in a for a in report["alerts"])


def test_sampler_health_missing_or_divergent_fails_only_publication_profile():
    common = {
        "available": True, "r_hat_max": 1.01,
        "ess_bulk_min": 500, "ess_bulk_min_frac": 0.2,
    }
    missing = evaluate_numerical_quality(
        convergence=common, n_posterior_samples=8000,
        draws=2000, tune=2000, chains=4, publishable=True,
    )
    assert missing["ok"] is False
    assert any(c["name"] == "divergences_reported" and not c["ok"] for c in missing["checks"])
    development = evaluate_numerical_quality(convergence=common, publishable=False)
    assert development["ok"] is True
    divergent = evaluate_numerical_quality(
        convergence={**common, "sampler_health": {
            "divergence_available": True, "n_divergent": 2,
            "divergence_fraction": 0.001,
        }},
        n_posterior_samples=8000, draws=2000, tune=2000, chains=4,
        publishable=True,
    )
    assert divergent["ok"] is False
    assert any(c["name"] == "divergences" and not c["ok"] for c in divergent["checks"])
