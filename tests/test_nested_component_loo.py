"""Audit P2.1: nested component LOO with freeze-before-truth."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.validation.nested_component_loo import (
    freeze_component_predictions,
    run_nested_component_loo,
    score_frozen_predictions,
)


def _toy_snap() -> EvidenceSnapshot:
    ed = date(2022, 11, 8)
    as_of = ed - timedelta(days=60)
    races = pd.DataFrame(
        [
            {
                "race_id": "senate-2022-PA",
                "election_id": "senate-2022",
                "office": "US_SENATE",
                "state": "PA",
                "seat_class": "III",
                "election_day": ed.isoformat(),
                "incumbent_party": "R",
                "is_open": True,
                "prior_lean": 1.0,
                "region": "northeast",
                "not_up": False,
                "held_by": None,
                "fundraising_share": 0.5,
                "pres_approval": -5.0,
                "white_house_party": "D",
                "is_midterm": True,
                "election_phase": "general",
                "runoff_of": None,
                "vacancy_reason": None,
                "ballot_status": "nominated",
                "effective_election_day": ed.isoformat(),
            },
            {
                "race_id": "senate-2022-OH",
                "election_id": "senate-2022",
                "office": "US_SENATE",
                "state": "OH",
                "seat_class": "III",
                "election_day": ed.isoformat(),
                "incumbent_party": "R",
                "is_open": True,
                "prior_lean": -6.0,
                "region": "midwest",
                "not_up": False,
                "held_by": None,
                "fundraising_share": 0.45,
                "pres_approval": -5.0,
                "white_house_party": "D",
                "is_midterm": True,
                "election_phase": "general",
                "runoff_of": None,
                "vacancy_reason": None,
                "ballot_status": "nominated",
                "effective_election_day": ed.isoformat(),
            },
        ]
    )
    polls = pd.DataFrame(
        [
            {
                "poll_id": "t1",
                "study_id": "s1",
                "release_version": 1,
                "pollster_id": "toy",
                "sponsor_id": "none",
                "race_id": "senate-2022-PA",
                "state": "PA",
                "election_id": "senate-2022",
                "field_start": (as_of - timedelta(days=10)).isoformat(),
                "field_end": (as_of - timedelta(days=5)).isoformat(),
                "sample_size": 600,
                "population": "LV",
                "mode": "Live",
                "two_party_margin": 2.0,
                "dem_candidate": "Fetterman",
                "rep_candidate": "Oz",
                "partisan": False,
                "quality_weight": 1.0,
                "house_effect_prior": 0.0,
                "extra_sd_prior": 2.0,
                "exclusion_status": "include",
            }
        ]
    )
    return EvidenceSnapshot(
        as_of=as_of,
        election_id="senate-2022",
        polls=polls,
        races=races,
        results_known=pd.DataFrame(),
        snapshot_id="toy-p21",
    )


def test_freeze_before_truth_no_results_needed():
    snap = _toy_snap()
    frozen = freeze_component_predictions(
        snap,
        election_id="senate-2022",
        holdout_year=2022,
        lead_days=60,
        hierarchical_method="fast",
        n_draws=80,
        seed=3,
    )
    assert "fast_hierarchical_t" in frozen
    assert frozen["fast_hierarchical_t"].status == "ok"
    assert frozen["fast_hierarchical_t"].race_ids
    # No remapping: pymc key absent when method=fast
    assert "pymc" not in frozen


def test_score_after_freeze():
    snap = _toy_snap()
    frozen = freeze_component_predictions(
        snap,
        election_id="senate-2022",
        holdout_year=2022,
        lead_days=60,
        hierarchical_method="fast",
        n_draws=40,
        seed=5,
    )
    results = pd.DataFrame(
        {
            "race_id": ["senate-2022-PA", "senate-2022-OH"],
            "two_party_margin": [5.0, -3.0],
        }
    )
    scored = score_frozen_predictions(frozen, results)
    assert scored["fast_hierarchical_t"]["status"] == "ok"
    assert scored["fast_hierarchical_t"]["n"] >= 1


def test_nested_loo_smoke(tmp_path):
    out = tmp_path / "nested_component_loo.json"
    report = run_nested_component_loo(
        years=(2022,),
        lead_days=(60,),
        hierarchical_method="fast",
        n_draws=100,
        seed=9,
        out_path=out,
    )
    assert report["audit_item"] == "P2.1"
    assert report["freeze_before_truth"] is True
    assert report["no_weight_remapping"] is True
    assert "2022" in report["crps_by_fold"]
    assert "g8_recommendations" in report
    assert report.get("path")
    assert out.exists()
