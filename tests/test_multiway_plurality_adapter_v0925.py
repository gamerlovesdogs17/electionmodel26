"""Unit tests for the generic multiway plurality adapter and chamber override."""

from __future__ import annotations

import numpy as np
import pandas as pd

from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.evidence.modeling_paths import (
    alaska_rcv_race_ids,
    assert_no_binary_contamination,
    binary_non_major_eligible_race_ids,
    multiway_plurality_race_ids,
)
from midterms.model.multiway_plurality import (
    MULTIWAY_CONTEST_STRUCTURE,
    classify_multiway_question,
    fit_multiway_plurality_race,
    historical_analog_support_report,
    merge_multiway_fits,
    multiway_probability_supported,
    shares_sum_to_one,
)
from midterms.model.pymc_model import FitResult
from midterms.simulate.chamber import simulate_chamber


def test_registry_path_partition_excludes_mt_from_binary() -> None:
    registry = load_current_candidate_registry()
    binary = binary_non_major_eligible_race_ids(registry)
    multiway = multiway_plurality_race_ids(registry)
    alaska = alaska_rcv_race_ids(registry)
    assert_no_binary_contamination(binary_race_ids=binary, multiway_race_ids=multiway)
    assert "senate-2026-MT" in multiway
    assert "senate-2026-MT" not in binary
    assert "senate-2026-NE" in binary
    assert "senate-2026-ID" in binary
    assert "senate-2026-SD" in binary
    assert "senate-2026-AK" in alaska
    assert "senate-2026-AK" not in binary
    assert "senate-2026-AK" not in multiway


def test_analog_report_does_not_activate_probabilities() -> None:
    report = historical_analog_support_report(n_analogs=158)
    assert report["activates_forecast_probabilities"] is False
    assert report["probability_model_support_status"] == "unsupported"
    assert report["structural_inventory_only"] is True


def test_multiway_still_unsupported_without_scorable_poll_validation() -> None:
    supported, status, reason = multiway_probability_supported()
    assert supported is False
    assert status == "unsupported"
    assert "not_historically_supported" in reason


def test_question_classification_preserves_multiway_vs_binary() -> None:
    ballot = ["a", "b", "c", "d"]
    assert classify_multiway_question(ballot, ballot_candidate_ids=ballot) == "full_field"
    assert (
        classify_multiway_question(["a", "b"], ballot_candidate_ids=ballot)
        == "binary_hypothetical_pairwise"
    )
    assert (
        classify_multiway_question(["a", "b", "c"], ballot_candidate_ids=ballot, principal_candidate_ids=["a", "b", "c"])
        == "full_principal_field_minor_omitted"
    )


def test_forced_mt_share_draws_sum_to_one_and_plurality_winners() -> None:
    registry = load_current_candidate_registry()
    mt = next(r for r in registry["races"] if r["race_id"] == "senate-2026-MT")
    assert mt["contest_structure"] == MULTIWAY_CONTEST_STRUCTURE
    # Bypass the historical-support gate for a cheap structural smoke fit.
    fitted = fit_multiway_plurality_race(
        mt,
        as_of="2026-10-05",
        n_draws=64,
        seed=7,
        require_supported=False,
    )
    assert fitted is not None
    assert shares_sum_to_one(fitted.share_draws)
    assert fitted.share_draws.shape == (64, len(fitted.candidate_ids))
    assert set(fitted.candidate_ids) >= {
        "senate-2026-MT:kurt-alme",
        "senate-2026-MT:alani-bankhead",
        "senate-2026-MT:seth-bodnar",
        "senate-2026-MT:kyle-austin",
    }
    probs = [
        float(np.mean(np.asarray(fitted.winner_candidate_ids, dtype=object) == cid))
        for cid in fitted.candidate_ids
    ]
    # Fail-closed / tied draws may leave residual; remaining mass is on candidates.
    assert all(np.isfinite(probs))
    assert sum(probs) <= 1.0 + 1e-9


def test_chamber_unknown_caucus_conserves_seats_and_is_not_silent_r() -> None:
    rng = np.random.default_rng(0)
    n_draws = 40
    # 34 ordinary races + 1 multiway with mixed D/R/unknown winners.
    ordinary = rng.normal(size=(n_draws, 34))
    multiway_margin = np.zeros((n_draws, 1))
    draws = np.column_stack([ordinary, multiway_margin])
    race_ids = [f"senate-2026-X{i:02d}" for i in range(34)] + ["senate-2026-MT"]
    states = ["XX"] * 34 + ["MT"]
    winner_ids = (
        ["senate-2026-MT:kurt-alme"] * 10
        + ["senate-2026-MT:seth-bodnar"] * 10
        + ["senate-2026-MT:kyle-austin"] * 20
    )
    fail_closed = [False] * 20 + [True] * 20
    fit = FitResult(
        race_ids=race_ids,
        states=states,
        mean_margin=draws.mean(axis=0),
        sd_margin=draws.std(axis=0),
        draws_margin=draws,
        house_effects={},
        diagnostics={
            "multiway_plurality_adapter": {
                "race_id": "senate-2026-MT",
                "candidate_ids": [
                    "senate-2026-MT:kurt-alme",
                    "senate-2026-MT:seth-bodnar",
                    "senate-2026-MT:kyle-austin",
                ],
                "candidate_names": ["Kurt Alme", "Seth Bodnar", "Kyle Austin"],
                "ballot_parties": ["R", "I", "L"],
                "caucuses": ["R", "D", None],
                "winner_candidate_ids": winner_ids,
                "fail_closed_draws": fail_closed,
                "share_mean": [0.4, 0.4, 0.2],
                "p_unknown_caucus": 0.5,
                "method": "limited_validation_multiway_plurality_model-v1",
                "uncertainty": {"support_status": "limited_supported", "p_dem_caucus": 0.25, "p_rep_caucus": 0.25},
            }
        },
        method="test",
    )
    rows = []
    for rid, st in zip(race_ids, states):
        rows.append({
            "race_id": rid,
            "state": st,
            "not_up": False,
            "held_by": None,
            "binary_score_eligible": rid != "senate-2026-MT",
            "probability_model_supported": True,
            "modeled_ballot_party": "D",
            "modeled_caucus": "D",
            "opposing_caucus": "R",
            "modeled_caucus_basis": "test",
            "opposing_caucus_basis": "test",
            "seat_class": "II",
            "election_phase": "general",
            "is_open": True,
            "prior_lean": 0.0,
            "incumbent_party": None,
            "contest_structure": "multiway_plurality" if rid.endswith("MT") else "two_party_dem_vs_rep",
        })
    # Pad held seats to 100.
    for i in range(65):
        rows.append({
            "race_id": f"held-{i}",
            "state": "ZZ",
            "not_up": True,
            "held_by": "D" if i < 33 else "R",
            "held_caucus": "D" if i < 33 else "R",
            "held_caucus_basis": "test",
            "binary_score_eligible": True,
            "probability_model_supported": True,
            "seat_class": "I",
            "election_phase": "general",
            "is_open": False,
            "prior_lean": 0.0,
            "incumbent_party": "D" if i < 33 else "R",
            "contest_structure": "two_party_dem_vs_rep",
            "modeled_ballot_party": "D",
            "modeled_caucus": "D",
            "opposing_caucus": "R",
            "modeled_caucus_basis": "test",
            "opposing_caucus_basis": "test",
        })
    races = pd.DataFrame(rows)
    sim, summaries = simulate_chamber(fit, races)
    assert sim.held_dem + sim.held_rep + len(fit.race_ids) == 100
    assert sim.unresolved_seat_draws is not None
    assert float(sim.expected_unresolved_seats) == 0.5
    assert abs(sim.p_dem_majority + sim.p_rep_majority + sim.p_unresolved_control - 1.0) < 1e-9
    mt_summary = next(s for s in summaries if s["race_id"] == "senate-2026-MT")
    assert mt_summary["modeling_path"] == "multiway_plurality_adapter"
    assert mt_summary["p_dem"] is None and mt_summary["p_rep"] is None
    assert mt_summary["p_modeled_candidate"] is None
    mass = sum(float(c["p_win"]) for c in mt_summary["candidate_probabilities"])
    assert abs(mass - 1.0) < 1e-6 or mass <= 1.0 + 1e-9
