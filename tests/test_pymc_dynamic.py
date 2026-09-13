"""Dynamic weekly PyMC path (audit P1.1)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.pymc_model import _prepare_weekly_path, fit_pymc_dynamic


def _toy_snapshot() -> EvidenceSnapshot:
    ed = date(2022, 11, 8)
    as_of = ed - timedelta(days=45)
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
    polls = []
    for rid, st, margins in (
        ("senate-2022-PA", "PA", [2.0, 1.0, 3.0]),
        ("senate-2022-OH", "OH", [-4.0, -5.0, -3.0]),
    ):
        for i, m in enumerate(margins):
            fe = as_of - timedelta(days=30 - 10 * i)
            polls.append(
                {
                    "poll_id": f"t-{rid}-{i}",
                    "study_id": f"s-{i}",
                    "release_version": 1,
                    "pollster_id": "toy-pollster",
                    "sponsor_id": "none",
                    "source_url": "https://example.test/poll",
                    "raw_hash": "x",
                    "field_start": fe.isoformat(),
                    "field_end": fe.isoformat(),
                    "published_at": fe.isoformat(),
                    "corrected_at": None,
                    "retrieved_at": as_of.isoformat(),
                    "valid_from": fe.isoformat(),
                    "valid_to": None,
                    "available_at": fe.isoformat(),
                    "event_time": fe.isoformat(),
                    "election_id": "senate-2022",
                    "office": "US_SENATE",
                    "state": st,
                    "race_id": rid,
                    "population": "LV",
                    "sample_size": 800,
                    "mode": "live",
                    "dem_share": 50 + m / 2,
                    "rep_share": 50 - m / 2,
                    "undecided": 0.0,
                    "other_share": 0.0,
                    "two_party_margin": m,
                    "partisan": False,
                    "exclusion_status": "include",
                    "exclusion_reason": None,
                    "parser_version": "toy",
                    "normalized_at": as_of.isoformat(),
                    "supersedes": None,
                    "geography_version_id": "state-usps-v1",
                    "candidate_set_version": "toy",
                    "question_id": "q",
                    "frame": None,
                    "recruitment": None,
                    "language": "en",
                    "design_effect": 1.0,
                    "leaners_included": True,
                    "multiway": False,
                    "questionnaire_hash": None,
                    "dem_candidate_id": None,
                    "dem_candidate_name": "D",
                    "rep_candidate_id": None,
                    "rep_candidate_name": "R",
                    "matchup_id": "D|R",
                    "hypothetical": False,
                    "contest_kind": "regular",
                    "seat_name": "Class III",
                    "election_stage": "general",
                }
            )
    return EvidenceSnapshot(
        as_of=as_of,
        election_id="senate-2022",
        polls=pd.DataFrame(polls),
        races=races,
        results_known=pd.DataFrame(),
        snapshot_id="toy-dynamic",
    )


def test_weekly_path_assigns_poll_weeks():
    snap = _toy_snapshot()
    prep = _prepare_weekly_path(snap)
    assert prep["n_weeks"] >= 2
    assert len(prep["poll_week"]) == len(prep["poll_y"]) == 6
    assert prep["poll_week"].min() >= 0
    assert prep["poll_week"].max() < prep["n_weeks"]
    assert prep["ed_week"] == prep["n_weeks"] - 1


def test_fit_pymc_dynamic_smoke():
    snap = _toy_snapshot()
    fit = fit_pymc_dynamic(snap, draws=40, tune=40, chains=1, seed=1)
    assert fit.method == "pymc_dynamic"
    assert fit.diagnostics.get("latent_path") == "weekly_random_walk"
    assert fit.draws_margin.shape[1] == 2
    assert np.all(np.isfinite(fit.mean_margin))
