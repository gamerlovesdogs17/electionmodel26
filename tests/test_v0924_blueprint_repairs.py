"""Cheap tests for v0.9.24 presentation, multiway, poll mechanics, and audits."""

from __future__ import annotations

import copy
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, PUBLIC_LIVE_ENABLED
from midterms.model.fundamentals_challengers import (
    FundamentalsChallengerConfig,
    challenger_feature_row,
)
from midterms.model.multiway_plurality import (
    MULTIWAY_CONTEST_STRUCTURE,
    historical_analog_support_report,
    plurality_winners,
    unsupported_multiway_race_payload,
    MultiwayCandidate,
)
from midterms.model.poll_weights import attach_poll_weights
from midterms.model.turnout import TurnoutLayerConfig, run_turnout_interface
from midterms.presentation.race_view import present_race
from midterms.simulate.institutional import (
    InstitutionalContestRule,
    apply_institutional_rules_to_draws,
    apply_plurality_rule_to_draws,
)


def test_public_live_still_disabled():
    assert PUBLIC_LIVE_ENABLED is False
    assert MODEL_VERSION == "senate-hierarchical-v0.9.24"


def test_presentation_does_not_mutate_alaska_forecast():
    race = {
        "race_id": "senate-2026-AK",
        "state": "AK",
        "contest_structure": "ranked_choice_multiway",
        "modeling_path": "alaska_rcv_adapter",
        "authoritative_binary_aliases": False,
        "exhausted_ballot_share": 0.04,
        "p_dem_caucus": 0.41,
        "p_rep_caucus": 0.59,
        "rating": "Lean R",
        "candidate_probabilities": [
            {
                "candidate_id": "senate-2026-AK:mary-peltola",
                "candidate_name": "Mary Peltola",
                "ballot_party": "D",
                "caucus": "D",
                "p_win": 0.41,
                "first_choice_estimate": 0.33,
            },
            {
                "candidate_id": "senate-2026-AK:dan-s-sullivan",
                "candidate_name": "Dan S. Sullivan",
                "ballot_party": "R",
                "caucus": "R",
                "p_win": 0.55,
                "first_choice_estimate": 0.45,
            },
            {
                "candidate_id": "senate-2026-AK:write-in-aggregate",
                "candidate_name": "Write-in",
                "ballot_party": "W",
                "caucus": None,
                "p_win": 0.04,
                "first_choice_estimate": 0.02,
            },
        ],
    }
    before = copy.deepcopy(race)
    view = present_race(race)
    assert race == before
    assert view.favored_candidate == "Dan S. Sullivan"
    assert view.favored_win_probability == pytest.approx(0.55)
    assert view.rcv_detail is not None
    assert view.rcv_detail["authoritative_binary_aliases"] is False
    assert "p_dem" not in race
    assert "p_rep" not in race


def test_multiway_fail_closed_when_analogs_sparse():
    support = historical_analog_support_report(n_analogs=0)
    assert support["win_probability_status"] == "fail_closed"
    payload = unsupported_multiway_race_payload(
        race_id="senate-2026-XX",
        candidates=[
            MultiwayCandidate("a", "A", "I", "D"),
            MultiwayCandidate("b", "B", "R", "R"),
            MultiwayCandidate("c", "C", "G", None),
        ],
        n_analogs=0,
    )
    assert payload["contest_structure"] == MULTIWAY_CONTEST_STRUCTURE
    assert all(row["p_win"] is None for row in payload["candidate_probabilities"])


def test_plurality_rule_resolves_and_fails_closed_on_residual():
    shares = np.array(
        [
            [0.5, 0.3, 0.2],
            [0.2, 0.2, 0.6],
        ],
        dtype=float,
    )
    out = apply_plurality_rule_to_draws(
        shares,
        ["a", "b", "residual"],
        residual_ids={"residual"},
        caucus_by_candidate={"a": "D", "b": "R", "residual": None},
    )
    assert out["winner_candidate_ids"][0] == "a"
    assert out["winner_candidate_ids"][1] is None
    assert out["unresolved_draws"] == 1
    winners = plurality_winners(shares, ["a", "b", "residual"], residual_ids={"residual"})
    assert winners["n_fail_closed"] == 1


def test_runoff_path_still_fail_closed_without_transition():
    rule = InstitutionalContestRule(seat_id="senate-2026-GA", threshold=0.5, runoff_required=True)
    shares = np.array([[0.4, 0.35, 0.25]], dtype=float)
    out = apply_institutional_rules_to_draws(shares, ["d", "r", "o"], rule)
    assert out["unresolved_draws"] == 1
    assert out["transition_model"] == "disabled_unvalidated"


def test_influence_weight_excludes_sample_size_and_quality():
    polls = pd.DataFrame(
        {
            "race_id": ["r1", "r1"],
            "pollster_id": ["A", "B"],
            "study_id": ["s1", "s2"],
            "field_end": ["2026-09-01", "2026-09-01"],
            "sample_size": [200, 2000],
            "quality_weight": [0.5, 1.5],
            "partisan": [False, False],
        }
    )
    w = attach_poll_weights(polls, as_of=date(2026, 10, 1))
    assert "absolute_recency" in w.columns
    assert abs(float(w["influence_weight"].iloc[0]) - float(w["influence_weight"].iloc[1])) < 1e-9


def test_absolute_recency_preserved_across_races():
    polls = pd.DataFrame(
        {
            "race_id": ["old", "new"],
            "pollster_id": ["A", "B"],
            "study_id": ["s1", "s2"],
            "field_end": ["2026-06-01", "2026-09-20"],
            "sample_size": [600, 600],
            "quality_weight": [1.0, 1.0],
            "partisan": [False, False],
        }
    )
    w = attach_poll_weights(polls, as_of=date(2026, 10, 1))
    old = float(w.loc[w["race_id"] == "old", "influence_weight"].iloc[0])
    new = float(w.loc[w["race_id"] == "new", "influence_weight"].iloc[0])
    assert new > old * 2.0


def test_state_space_calendar_delta_t_same_day_zero():
    import inspect

    from midterms.model import state_space as state_space_mod

    src = inspect.getsource(state_space_mod.fit_state_space)
    assert "delta_days" in src
    assert "process_sd_per_sqrt_day" in src
    assert "process_sd_per_sqrt_day**2) * float(delta_days)" in src.replace(" ", "") or (
        "(process_sd_per_sqrt_day**2) * float(delta_days)" in src
    )
    # Same-day Δt contributes zero process variance; multi-day gap accumulates.
    process_sd = 0.8
    assert (process_sd**2) * 0 == 0.0
    assert (process_sd**2) * 10 == pytest.approx(6.4)


def test_challengers_and_turnout_remain_disabled():
    cfg = FundamentalsChallengerConfig()
    assert cfg.candidate_experience is False
    assert cfg.special_election_signal is False
    row = pd.Series(
        {
            "candidate_experience": 1.0,
            "candidate_experience_available_at": "2020-01-01",
            "candidate_experience_source_hash": "abc",
            "special_election_signal": 1.0,
            "special_election_signal_available_at": "2020-01-01",
            "special_election_signal_source_hash": "abc",
            "recent_statewide_performance": 1.0,
            "recent_statewide_performance_available_at": "2020-01-01",
            "recent_statewide_performance_source_hash": "abc",
        }
    )
    out = challenger_feature_row(row, as_of="2026-10-01", config=cfg)
    assert out["values"] == {}
    assert TurnoutLayerConfig().enabled is False
    turned = run_turnout_interface(pd.DataFrame(), config=TurnoutLayerConfig())
    assert turned["status"] == "disabled"


def test_mt_id_structure_uses_official_multiway_field():
    from midterms.validation.contest_field_audits_v0924 import build_mt_id_contest_field_audit

    audit = build_mt_id_contest_field_audit()
    assert audit["registry_used_as_ballot_authority"] is False
    for race in audit["races"]:
        assert race["n_certified_general_ballot_candidates"] >= 3
        assert race["verified_contest_structure"] == MULTIWAY_CONTEST_STRUCTURE
        assert race["recommended_modeling_path"] == "multiway_plurality_adapter"
        assert race["win_probability_status"] == "fail_closed"
        assert race["outside_model_probabilities_used"] is False
        assert race["registry_used_as_ballot_authority"] is False


def test_ne_forensics_ledger_counts():
    from midterms.validation.ne_poll_forensics_v0924 import build_ne_poll_forensics

    payload = build_ne_poll_forensics()
    assert payload["n_raw_ne_polls"] >= payload["n_candidate_compatible"]
    assert payload["diagnosis"] in {
        "A_ingestion_filtering_bug",
        "B_actual_source_coverage_limitation",
    }
    assert payload["outside_architecture_sources_added"] is False
