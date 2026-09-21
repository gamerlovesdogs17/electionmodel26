"""Synthetic integration checks for the internal decomposition hook."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from midterms.validation.decomposition_hook import build_decomposition_rows


def _inputs():
    races = pd.DataFrame([{
        "race_id": "synthetic-one", "state": "ZZ", "prior_lean": 1.5,
        "not_up": False, "is_open": True, "incumbent_party": None,
        "fundraising_share": 0.5, "pres_approval": 0.0,
        "white_house_party": "D", "is_midterm": False,
    }])
    return dict(
        races=races, polls=pd.DataFrame(), as_of=date(2026, 1, 1),
        race_ids=["synthetic-one"], core_means=np.array([2.0]),
        core_sds=np.array([3.0]), final_means=np.array([2.75]),
        final_sds=np.array([3.2]),
        prior_provenance_by_state={"ZZ": {"method": "synthetic", "source_sha256": "a" * 64}},
        generic_ballot=0.0,
        overlay_shifts={"expert_overlay": np.array([0.5]),
                        "market_overlay": np.array([0.25])},
        error_budget={"terminal_nat_sd": 1.0, "terminal_race_sd": 2.0},
        exact_overlay_chain=True,
    )


def test_exact_overlay_chain_and_nonadditive_uncertainties():
    result = build_decomposition_rows(**_inputs())[0]
    assert result["final_location"]["value"] == pytest.approx(2.75)
    shifts = [term["value"] for term in result["terms"] if term["kind"] == "overlay_shift"]
    assert sum(shifts) == pytest.approx(0.75)
    assert any(term["name"] == "terminal_nat_sd" and term["kind"] == "uncertainty_component"
               for term in result["terms"])
    assert result["effective_sample_size"] is None
    assert result["observation_count"] == 0


def test_mismatched_exact_overlay_chain_fails():
    inputs = _inputs()
    inputs["final_means"] = np.array([3.0])
    with pytest.raises(ValueError, match="overlay shifts"):
        build_decomposition_rows(**inputs)


def test_joint_residual_is_labeled_nonlinear():
    inputs = _inputs()
    inputs["exact_overlay_chain"] = False
    inputs["final_means"] = np.array([3.0])
    result = build_decomposition_rows(**inputs)[0]
    assert any(term["kind"] == "nonlinear_joint_effect" for term in result["terms"])


def test_observed_poll_location_is_not_claimed_as_posterior_effect():
    inputs = _inputs()
    inputs["polls"] = pd.DataFrame([{
        "race_id": "synthetic-one", "two_party_margin": 4.0,
        "poll_id": "synthetic-poll", "study_id": "synthetic-study",
        "pollster_id": "synthetic-pollster",
        "field_end": "2025-12-30", "sample_size": 500,
        "quality_weight": 1.0, "house_effect_prior": 0.0,
        "extra_sd_prior": 2.0, "mode": "live", "population": "LV",
    }])
    result = build_decomposition_rows(**inputs)[0]
    assert result["observation_count"] == 1
    assert any(term["name"] == "weighted_observed_poll_location"
               and term["kind"] == "observation_location" for term in result["terms"])
