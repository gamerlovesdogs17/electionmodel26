"""Audit P1.3: layered terminal + similarity covariance."""

from __future__ import annotations

import numpy as np
import pandas as pd

from midterms.model.similarity import similarity_matrix, race_feature_matrix
from midterms.model.terminal import (
    active_scales,
    add_terminal_layers,
    error_budget_block,
)


def _toy_races(n: int = 6) -> pd.DataFrame:
    regions = ["northeast", "midwest", "south", "west"]
    rows = []
    for i in range(n):
        rows.append(
            {
                "race_id": f"senate-2022-X{i}",
                "state": ["PA", "OH", "GA", "AZ", "NV", "WI"][i % 6],
                "region": regions[i % len(regions)],
                "prior_lean": float(i - 3),
                "is_open": i % 2 == 0,
                "fundraising_share": 0.45 + 0.02 * i,
            }
        )
    return pd.DataFrame(rows)


def test_similarity_matrix_psd():
    races = _toy_races()
    X = race_feature_matrix(races)
    R = similarity_matrix(X, length_scale=1.75)
    eig = np.linalg.eigvalsh(R)
    assert float(eig.min()) > -1e-8
    assert R.shape == (len(races), len(races))


def test_terminal_layers_induce_correlation():
    races = _toy_races()
    rng = np.random.default_rng(0)
    base = np.zeros((2000, len(races)))
    layered = add_terminal_layers(
        base,
        races,
        rng,
        terminal_nat_sd=0.0,
        terminal_race_sd=0.0,
        sim_scale=2.0,
        length_scale=1.5,
    )
    cov = np.cov(layered, rowvar=False)
    off = cov[np.triu_indices(len(races), k=1)]
    # Similarity should create positive off-diagonal mass on average
    assert float(np.mean(off)) > 0.05

    indep = add_terminal_layers(
        base,
        races,
        np.random.default_rng(1),
        terminal_nat_sd=0.0,
        terminal_race_sd=2.0,
        sim_scale=0.0,
    )
    cov_i = np.cov(indep, rowvar=False)
    off_i = cov_i[np.triu_indices(len(races), k=1)]
    assert abs(float(np.mean(off_i))) < abs(float(np.mean(off)))


def test_error_budget_block_has_layers():
    bud = error_budget_block()
    assert bud["terminal_layers"] == "national+race+similarity"
    assert "terminal_nat_sd" in bud
    assert bud["terminal_rss"] > 0


def test_covariance_calibration_smoke(tmp_path):
    from midterms.validation.covariance_calibration import run_covariance_calibration

    report = run_covariance_calibration(
        years=(2022,),
        lead_days=(60,),
        n_draws=200,
        max_configs=3,
        apply_defaults=False,
        seed=7,
        out_path=tmp_path / "covariance_calibration_smoke.json",
    )
    assert report["audit_item"] == "P1.3"
    assert "baseline" in report
    assert "gate_passed" in report
    assert report["baseline"]["n_folds"] >= 1
    assert report.get("path")
    # Defaults unchanged when apply_defaults=False
    assert abs(active_scales()["terminal_nat_sd"] - 1.8) < 1e-9
