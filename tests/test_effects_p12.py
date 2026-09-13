"""Audit P1.2: hierarchical effects + nested fundamentals shrinkage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from midterms.model.effects import MODE_PRIOR, encode_mode_pop, mode_category, population_category
from midterms.model.fundamentals import (
    PRIOR_COEF,
    COEF,
    build_design,
    fundamentals_mean,
    ridge_toward_prior,
    set_coefs,
)


def test_mode_pop_categories():
    assert mode_category("Live Phone") == "live"
    assert mode_category("IVR/Online") == "ivr"
    assert population_category("LV") == "LV"
    assert population_category("Registered Voters") == "RV"
    assert MODE_PRIOR["ivr"] == -0.4


def test_encode_mode_pop_aligns_with_polls():
    polls = pd.DataFrame(
        {
            "mode": ["Live", "IVR", "Online"],
            "population": ["LV", "RV", "A"],
        }
    )
    mode_idx, pop_idx, mode_prior, pop_prior = encode_mode_pop(polls)
    assert list(mode_idx) == [0, 1, 2]
    assert list(pop_idx) == [0, 1, 2]
    assert len(mode_prior) == 4
    assert len(pop_prior) == 3


def test_ridge_shrinks_toward_prior_with_few_rows():
    # Single observation → strong pull to prior
    X = np.array([[5.0, 2.0, 1.0, 0.0, -10.0, 1.0, 1.0]])
    y = np.array([10.0])
    names = list(PRIOR_COEF.keys())
    est = ridge_toward_prior(X, y, feature_names=names, ridge_lambda=100.0)
    assert est["prior_lean"] == 1.0
    for k in ("generic_ballot", "incumbency", "fundraising_logit"):
        assert abs(est[k] - PRIOR_COEF[k]) < abs(est[k] - 50.0)


def test_set_coefs_keeps_prior_lean_fixed():
    old = set_coefs({"prior_lean": 2.0, "incumbency": 3.5})
    assert COEF["prior_lean"] == 1.0
    assert COEF["incumbency"] == 3.5
    set_coefs(old)
    assert COEF["incumbency"] == PRIOR_COEF["incumbency"]


def test_fundamentals_mean_uses_working_coefs():
    races = pd.DataFrame(
        [
            {
                "race_id": "senate-2022-PA",
                "not_up": False,
                "prior_lean": 0.0,
                "incumbent_party": "R",
                "is_open": False,
                "fundraising_share": 0.5,
                "pres_approval": 0.0,
                "white_house_party": "D",
                "is_midterm": True,
                "election_day": "2022-11-08",
            }
        ]
    )
    base = fundamentals_mean(races, generic_ballot=0.0)
    old = set_coefs({**PRIOR_COEF, "midterm_outparty": 0.0, "incumbency": 0.0})
    try:
        zeroed = fundamentals_mean(races, generic_ballot=0.0)
        assert float(zeroed.iloc[0]) == 0.0
        assert float(base.iloc[0]) != 0.0
    finally:
        set_coefs(old)


def test_build_design_and_estimate_nested_smoke():
    races = pd.DataFrame(
        [
            {
                "race_id": "a",
                "not_up": False,
                "prior_lean": 5.0,
                "incumbent_party": "D",
                "is_open": False,
                "fundraising_share": 0.55,
                "pres_approval": -5.0,
                "white_house_party": "R",
                "is_midterm": True,
                "election_day": "2018-11-06",
            },
            {
                "race_id": "b",
                "not_up": False,
                "prior_lean": -4.0,
                "incumbent_party": "R",
                "is_open": True,
                "fundraising_share": 0.4,
                "pres_approval": -5.0,
                "white_house_party": "R",
                "is_midterm": True,
                "election_day": "2018-11-06",
            },
        ]
    )
    results = pd.DataFrame(
        {"race_id": ["a", "b"], "two_party_margin": [8.0, -6.0]}
    )
    X, y, names = build_design(races, results, generic_ballot=2.0)
    assert X.shape == (2, len(names))
    assert len(y) == 2
    est = ridge_toward_prior(X, y, feature_names=names, ridge_lambda=10.0)
    assert set(est) == set(PRIOR_COEF)
