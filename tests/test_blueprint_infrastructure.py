"""Cheap synthetic tests for blueprint capability infrastructure."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from midterms.evidence.candidate_timeline import apply_candidate_timeline
from midterms.evidence.demography import demographic_snapshot_metadata
from midterms.evidence.economics import (
    VINTAGE_CLASS_ALFRED, VINTAGE_CLASS_FRED_LATEST, VINTAGE_CLASS_WORLD_BANK,
    classify_economic_vintages,
)
from midterms.evidence.freshness import classify_freshness
from midterms.evidence.markets import validate_candidate_contract_family
from midterms.evidence.outcome_identity import attach_2026_ticket_identities
from midterms.model.fundamentals_challengers import (
    FundamentalsChallengerConfig, challenger_feature_row,
)
from midterms.model.poll_structure import PollStructureConfig, encode_poll_structure
from midterms.model.turnout import TurnoutLayerConfig, run_turnout_interface
from midterms.simulate.institutional import (
    InstitutionalContestRule, apply_institutional_rules_to_draws,
    validate_joint_seat_accounting,
)
from midterms.validation.bayesian_diagnostics import (
    gaussian_location_sbc, posterior_predictive_checks, prior_predictive_diagnostics,
)
from midterms.validation.metrics import score_joint_draws, seat_count_crps
from midterms.validation.overlay_validation import publication_overlay_policy
from midterms.validation.structural_ablations import (
    ablation_by_id, same_family_fit_spec,
)
from midterms.ops.reproducibility import environment_lock, repository_relative_path
from midterms.config import ROOT


def test_prior_ppc_and_sbc_are_deterministic_and_detect_absurd_prior():
    prior = {"mu_final": np.array([[0.0, 90.0], [-80.0, 1.0]])}
    diag = prior_predictive_diagnostics(prior, max_extreme_fraction=0.1)
    assert diag["ok"] is False
    predictive = np.array([[0.0, 1.0], [0.2, 1.2], [-0.2, 0.8]])
    ppc = posterior_predictive_checks(
        np.array([0.0, 1.0]), predictive,
        groups={"study": np.array(["s1", "s1"])},
    )
    assert ppc["groups"]["study"][0]["n"] == 2
    assert gaussian_location_sbc(seed=9) == gaussian_location_sbc(seed=9)


def test_poll_structure_stable_missing_safe_and_study_downweight_interaction():
    polls = pd.DataFrame({
        "poll_id": ["a", "b", "c"], "sponsor_id": ["z", None, "a"],
        "questionnaire_hash": [None, "q", "q"], "study_id": ["shared", "shared", None],
    })
    a = encode_poll_structure(polls, PollStructureConfig(study_effect=True))
    b = encode_poll_structure(polls.sample(frac=1, random_state=1).sort_index(), PollStructureConfig(study_effect=True))
    assert a["sponsor_ids"] == b["sponsor_ids"]
    assert a["study_idx"][0] == a["study_idx"][1]
    assert a["study_idx"][2] != a["study_idx"][0]
    assert a["config"].use_heuristic_study_downweight is False


def test_structural_ablation_preserves_family_and_seed():
    spec = same_family_fit_spec(
        base_method="pymc", base_seed=42,
        ablation=ablation_by_id("hier_no_similarity"),
    )
    assert spec["method"] == "pymc"
    assert spec["seed"] == 42
    assert spec["fit_overrides"] == {"include_similarity": False}


def test_candidate_timeline_hides_future_and_applies_withdrawal_only_when_known():
    races = pd.DataFrame([{
        "race_id": "r1", "not_up": False, "ballot_status": "nominated",
        "modeled_candidate_id": "future-current-row",
    }])
    timeline = pd.DataFrame([
        {"event_id": "a", "race_id": "r1", "candidate_id": "c1", "modeled_side": "modeled",
         "event_type": "nomination", "effective_at": "2020-01-01", "available_at": "2020-01-02",
         "ballot_party": "I", "caucus_affiliation": None, "caucus_basis": None},
        {"event_id": "b", "race_id": "r1", "candidate_id": "c1", "modeled_side": "modeled",
         "event_type": "withdrawal", "effective_at": "2020-02-01", "available_at": "2020-02-03"},
        {"event_id": "c", "race_id": "r1", "candidate_id": "c2", "modeled_side": "modeled",
         "event_type": "replacement", "effective_at": "2020-02-05", "available_at": "2020-02-06",
         "ballot_party": "I"},
    ])
    early, meta = apply_candidate_timeline(races, timeline, as_of="2020-02-02")
    assert early.loc[0, "ballot_status"] == "nominated"
    assert early.loc[0, "modeled_ballot_party"] == "I"
    assert pd.isna(early.loc[0, "modeled_caucus"])
    late, _ = apply_candidate_timeline(races, timeline, as_of="2020-02-03")
    assert late.loc[0, "ballot_status"] == "withdrawn"
    assert late.loc[0, "modeled_candidate_id"] == "c1"
    replacement, _ = apply_candidate_timeline(races, timeline, as_of="2020-02-06")
    assert replacement.loc[0, "modeled_candidate_id"] == "c2"
    assert meta["snapshot_sha256"]

    before, _ = apply_candidate_timeline(races, timeline, as_of="2019-12-31")
    assert pd.isna(before.loc[0, "modeled_candidate_id"])


def test_current_ticket_fallback_cannot_overwrite_point_in_time_identity():
    races = pd.DataFrame([{
        "election_id": "senate-2026", "race_id": "senate-2026-NE",
        "state": "NE", "not_up": False,
        "candidate_timeline_status": "point_in_time",
        "modeled_candidate_id": "historically-known-candidate",
        "modeled_ballot_party": "I",
    }])
    attached = attach_2026_ticket_identities(races)
    assert attached.loc[0, "modeled_candidate_id"] == "historically-known-candidate"
    assert attached.loc[0, "modeled_ballot_party"] == "I"


def test_demographic_and_economic_vintage_classification_is_fail_closed():
    demo = demographic_snapshot_metadata("2016-10-01", source_year=2018, available_at=None)
    assert demo["future_source"] is True
    assert demo["production_eligible"] is False
    rows = classify_economic_vintages(pd.DataFrame([
        {"series_id": "x", "status": "alfred_realtime"},
        {"series_id": "x", "status": "fred_public_csv"},
        {"series_id": "WB_X", "status": "worldbank_api"},
    ]))
    assert rows["vintage_class"].tolist() == [
        VINTAGE_CLASS_ALFRED, VINTAGE_CLASS_FRED_LATEST, VINTAGE_CLASS_WORLD_BANK,
    ]
    assert rows["historical_replay_eligible"].tolist() == [True, False, False]


def _semantic_contract(event: str, candidate: str, price: float) -> dict:
    return {
        "ticker": f"{event}-{candidate}", "event_ticker": event,
        "candidate_id": candidate, "outcome_type": "candidate_win",
        "mutually_exclusive": True, "event_exhaustive": True,
        "contract_scope": "candidate", "last_price_dollars": price,
    }


def test_market_semantics_require_exhaustive_candidate_family():
    event = "SYNTH"
    valid = [_semantic_contract(event, "A", .4), _semantic_contract(event, "B", .6)]
    assert validate_candidate_contract_family(event, valid)["ok"] is True
    multi = valid + [_semantic_contract(event, "C", .1)]
    assert validate_candidate_contract_family(event, multi)["ok"] is True
    unrelated = [*valid, {**_semantic_contract(event, "X", .2), "contract_scope": "turnout"}]
    assert validate_candidate_contract_family(event, unrelated)["ok"] is False
    duplicate = [valid[0], {**valid[1], "candidate_id": "A"}]
    assert validate_candidate_contract_family(event, duplicate)["ok"] is False
    unsafe = [{**row, "event_exhaustive": False} for row in valid]
    assert validate_candidate_contract_family(event, unsafe)["ok"] is False


def test_institutional_draw_layer_links_runoff_and_never_double_counts_seat():
    first = np.array([[.55, .35, .10], [.40, .38, .22]])
    runoff = np.array([[.55, .45, 0], [.45, .55, 0]])
    rule = InstitutionalContestRule(seat_id="seat-1")
    result = apply_institutional_rules_to_draws(
        first, ["a", "b", "c"], rule, runoff_shares=runoff,
        joint_draw_ids=np.array([100, 101]),
    )
    assert result["winner_candidate_ids"] == ["a", "b"]
    assert result["runoff_required"] == [False, True]
    assert result["joint_draw_ids"] == [100, 101]
    accounting = validate_joint_seat_accounting([result], held_seats=99)
    assert accounting["ok"] is True
    with pytest.raises(ValueError):
        validate_joint_seat_accounting([result, result], held_seats=98)


def test_turnout_is_explicitly_auxiliary_and_disabled_by_default():
    status = run_turnout_interface(pd.DataFrame())
    assert status["status"] == "disabled"
    assert status["primary_simulation_affected"] is False
    with pytest.raises(ValueError):
        run_turnout_interface(pd.DataFrame(), config=TurnoutLayerConfig(enabled=True))


def test_joint_scores_preserve_order_and_are_deterministic():
    draws = np.array([[0., 1.], [1., 0.], [0.5, 0.5]])
    truth = np.array([0.25, 0.75])
    a = score_joint_draws(
        draws, truth, race_ids=["r1", "r2"], observed_race_ids=["r1", "r2"],
        seat_draws=np.array([50, 51, 51]), observed_seats=51, seed=2,
    )
    b = score_joint_draws(
        draws, truth, race_ids=["r1", "r2"], observed_race_ids=["r1", "r2"],
        seat_draws=np.array([50, 51, 51]), observed_seats=51, seed=2,
    )
    assert a == b
    assert a["seat_count_crps"] == pytest.approx(seat_count_crps(np.array([50, 51, 51]), 51))
    with pytest.raises(ValueError):
        score_joint_draws(draws, truth, race_ids=["r1", "r2"], observed_race_ids=["r2", "r1"])


def test_overlay_policy_defaults_to_core_only_and_freshness_distinguishes_failures(tmp_path):
    policy = publication_overlay_policy(
        rating_weight=.1, market_weight=.1, control_weight=.1,
        path=tmp_path / "missing.json",
    )
    assert policy["use_ratings"] is False
    assert policy["use_race_markets"] is False
    assert classify_freshness(
        "polls", checked_at="2026-01-10", retrieved_at="2026-01-09",
        observed_at="2026-01-08",
    )["status"] == "fresh"
    assert classify_freshness(
        "polls", checked_at="2026-01-10", refresh_status="timeout",
    )["status"] == "refresh_failed"
    assert classify_freshness(
        "polls", checked_at="2026-01-10", source_available=False,
    )["status"] == "unavailable"


def test_fundamentals_challenger_is_off_and_provenance_gated():
    row = pd.Series({
        "candidate_experience": 1.0,
        "candidate_experience_available_at": "2020-01-01",
        "candidate_experience_source_hash": "abc",
    })
    off = challenger_feature_row(row, as_of="2020-02-01")
    assert off["values"] == {}
    on = challenger_feature_row(
        row, as_of="2020-02-01",
        config=FundamentalsChallengerConfig(candidate_experience=True),
    )
    assert on["production_eligible"] is True
    future = challenger_feature_row(
        row, as_of="2019-02-01",
        config=FundamentalsChallengerConfig(candidate_experience=True),
    )
    assert future["production_eligible"] is False


def test_environment_lock_and_paths_are_portable():
    lock = environment_lock()
    assert lock["schema_version"] == "environment-lock-v2"
    assert {"pymc", "arviz", "pytensor"}.issubset(lock["packages"])
    assert lock["dependency_lock_sha256"]
    assert repository_relative_path(ROOT / "pyproject.toml") == "pyproject.toml"
    with pytest.raises(ValueError):
        repository_relative_path(ROOT.parent / "outside.txt")
