"""Synthetic chain diagnostics for environments where ArviZ DLLs are blocked."""

from types import SimpleNamespace

import numpy as np

from midterms.validation.numerical_quality import _numpy_rank_convergence


def _idata(draws):
    return SimpleNamespace(posterior={"synthetic_location": SimpleNamespace(values=draws)})


def test_independent_synthetic_chains_pass_rank_diagnostics():
    draws = np.random.default_rng(7).normal(size=(4, 500, 3))
    report = _numpy_rank_convergence(_idata(draws), var_name="synthetic_location")
    assert report["available"] is True
    assert report["backend"] == "numpy_rank_split_rhat_geyer_bulk_ess_v1"
    assert report["r_hat_max"] < 1.05
    assert report["ess_bulk_min_frac"] > 0.10


def test_separated_synthetic_chains_fail_rank_diagnostic():
    draws = np.random.default_rng(9).normal(size=(4, 500, 1))
    draws[0] += 4.0
    report = _numpy_rank_convergence(_idata(draws), var_name="synthetic_location")
    assert report["r_hat_max"] > 1.05
