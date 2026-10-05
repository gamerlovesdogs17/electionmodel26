from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from midterms.evidence.alaska_rcv import (
    RACE_ID,
    classify_pairwise_polls,
    load_alaska_rcv_evidence,
    prepare_alaska_rcv_evidence,
)
from midterms.evidence.non_major_contract import probability_support_status
from midterms.model.alaska_rcv_adapter import (
    ADAPTER_SPEC_VERSION,
    as_fit_result,
    fit_alaska_rcv_adapter,
    merge_alaska_fit,
    tabulate_irv,
)
from midterms.model.pymc_model import FitResult
from midterms.model.scenarios import run_scenarios
from midterms.simulate.chamber import simulate_chamber
from midterms.validation.alaska_rcv import build_alaska_rcv_validation


def test_official_field_keeps_four_candidates_and_two_sullivans_separate() -> None:
    evidence = load_alaska_rcv_evidence()
    ids = [candidate["candidate_id"] for candidate in evidence["candidate_field"]]
    assert len(ids) == len(set(ids)) == 4
    assert f"{RACE_ID}:dan-s-sullivan" in ids
    assert f"{RACE_ID}:daniel-j-sullivan-jr" in ids
    assert ids.index(f"{RACE_ID}:dan-s-sullivan") != ids.index(f"{RACE_ID}:daniel-j-sullivan-jr")
    assert {row["name"] for row in evidence["certified_write_ins"]} == {
        "Sidney “Sid” Hill", "Heather McElwain",
    }
    assert all(row["caucus"] is None for row in evidence["certified_write_ins"])


def test_pairwise_poll_maps_only_to_incumbent_and_never_first_choice() -> None:
    polls = pd.DataFrame([
        {"race_id": RACE_ID, "poll_id": "good", "modeled_candidate_name": "Mary Peltola", "opposing_candidate_name": "Dan Sullivan"},
        {"race_id": RACE_ID, "poll_id": "junior", "modeled_candidate_name": "Mary Peltola", "opposing_candidate_name": "Daniel J. Sullivan Jr."},
        {"race_id": RACE_ID, "poll_id": "typo", "modeled_candidate_name": "Mary Peltola", "opposing_candidate_name": "Dan Sullvian"},
    ])
    result = classify_pairwise_polls(polls)
    assert result["poll_id"].tolist() == ["good"]
    assert result.iloc[0]["pairwise_candidate_b_id"] == f"{RACE_ID}:dan-s-sullivan"
    assert result.iloc[0]["is_first_choice_measurement"] is False or not bool(result.iloc[0]["is_first_choice_measurement"])


def test_multiway_source_poll_remains_labeled_as_pairwise_projection() -> None:
    polls = pd.DataFrame([
        {"race_id": RACE_ID, "poll_id": "binary", "modeled_candidate_name": "Mary Peltola", "opposing_candidate_name": "Dan Sullivan", "multiway": False},
        {"race_id": RACE_ID, "poll_id": "multi", "modeled_candidate_name": "Mary Peltola", "opposing_candidate_name": "Dan Sullivan", "multiway": True},
    ])
    result = classify_pairwise_polls(polls).set_index("poll_id")
    assert result.loc["binary", "measurement_type"] == "binary_pairwise_normalized"
    assert result.loc["multi", "measurement_type"] == "multiway_question_normalized_pairwise_projection"
    assert not result["is_first_choice_measurement"].any()


def test_primary_is_separate_official_evidence() -> None:
    evidence = load_alaska_rcv_evidence()
    rows = {row["candidate_id"]: row for row in evidence["primary_results"]}
    assert evidence["primary_date"] == "2026-08-18"
    assert evidence["primary_total_votes"] == 166004
    assert rows[f"{RACE_ID}:mary-peltola"]["votes"] == 82244
    assert evidence["poll_measurement_contract"].endswith("never first_choice")


def test_irv_eliminates_lowest_preserves_mass_and_exhausts() -> None:
    first = np.array([0.42, 0.38, 0.20])
    # Candidate 2 transfers 40/30 with 30 exhausted; candidate 1 then transfers.
    matrix = np.array([
        [0.0, 0.7, 0.1, 0.2],
        [0.6, 0.0, 0.1, 0.3],
        [0.4, 0.3, 0.0, 0.3],
    ])
    result = tabulate_irv(first, matrix)
    assert result.elimination_order[0] == 2
    assert result.exhausted == pytest.approx(0.06)
    assert result.final_support.sum() + result.exhausted == pytest.approx(1.0)
    continuing = result.final_support.sum()
    assert result.final_support[result.winner_index] > continuing / 2


def test_transfer_uncertainty_is_probabilistic_not_deterministic_party_flow() -> None:
    polls = pd.read_parquet("data/normalized/polls.parquet")
    fit = fit_alaska_rcv_adapter(polls, as_of="2026-10-03", n_draws=250, seed=44)
    matrix = np.asarray(fit.diagnostics["mean_transfer_matrix_first_100"])
    # Incumbent and junior are both Republicans, yet eliminated R ballots have
    # positive mass for multiple destinations and exhaustion.
    assert np.count_nonzero(matrix[0] > 0.001) >= 3
    assert matrix[0, -1] > 0
    assert len({tuple(order) for order in fit.elimination_orders}) > 1


def test_write_in_winner_without_caucus_fails_closed() -> None:
    polls = pd.DataFrame()
    with pytest.raises(ValueError, match="write-in winner"):
        fit_alaska_rcv_adapter(
            polls, as_of="2026-10-03", n_draws=25, seed=2,
            first_choice_override=np.array([0.001, 0.001, 0.001, 0.001, 0.996]),
        )


def _held_rows() -> list[dict]:
    return [
        {"race_id": f"held-{index}", "state": "ZZ", "not_up": True,
         "held_by": "D" if index < 45 else "R", "held_caucus": None,
         "held_caucus_basis": None}
        for index in range(99)
    ]


def test_candidate_winner_draws_drive_caucus_accounting_and_candidate_output() -> None:
    polls = pd.read_parquet("data/normalized/polls.parquet")
    result = fit_alaska_rcv_adapter(polls, as_of="2026-10-03", n_draws=300, seed=77)
    fit = as_fit_result(result)
    race = {
        "race_id": RACE_ID, "state": "AK", "not_up": False, "held_by": "R",
        "binary_score_eligible": False, "probability_model_supported": True,
        "probability_model_support_reason": "limited_validation_alaska_rcv_model",
        "modeled_ballot_party": "D", "modeled_caucus": "D", "modeled_caucus_basis": "major_party_ballot_affiliation",
        "opposing_caucus": "R", "opposing_caucus_basis": "major_party_ballot_affiliation",
        "contest_structure": "ranked_choice_multiway", "ballot_status": "nominated",
    }
    sim, summaries = simulate_chamber(fit, pd.DataFrame([*_held_rows(), race]))
    summary = summaries[0]
    assert np.array_equal(sim.race_win[:, 0].astype(bool), result.chamber_dem_win)
    assert len(summary["candidate_probabilities"]) == 5
    assert sum(row["p_win"] for row in summary["candidate_probabilities"]) == pytest.approx(1.0)
    assert summary["p_dem"] is None and summary["p_rep"] is None
    assert summary["authoritative_binary_aliases"] is False
    assert summary["p_dem_caucus"] == pytest.approx(result.chamber_dem_win.mean())

    # The generic encoded margin is not authoritative.  Even if it is changed,
    # chamber accounting reconstructs the same seat draw from candidate winners.
    corrupted = FitResult(
        fit.race_ids, fit.states, np.array([99.0]), np.array([0.0]),
        np.full_like(fit.draws_margin, 99.0), fit.house_effects, fit.diagnostics, fit.method,
    )
    corrupted_sim, _ = simulate_chamber(corrupted, pd.DataFrame([*_held_rows(), race]))
    assert np.array_equal(corrupted_sim.race_win, sim.race_win)


def test_generic_scenarios_do_not_mutate_rcv_candidate_outcomes() -> None:
    polls = pd.read_parquet("data/normalized/polls.parquet")
    fit = as_fit_result(fit_alaska_rcv_adapter(polls, as_of="2026-10-03", n_draws=80, seed=91))
    race = {
        "race_id": RACE_ID, "state": "AK", "not_up": False, "held_by": "R",
        "binary_score_eligible": False, "probability_model_supported": True,
        "modeled_ballot_party": "D", "modeled_caucus": "D", "modeled_caucus_basis": "major_party_ballot_affiliation",
        "opposing_caucus": "R", "opposing_caucus_basis": "major_party_ballot_affiliation",
        "contest_structure": "ranked_choice_multiway", "ballot_status": "nominated",
    }
    scenarios = run_scenarios(fit, pd.DataFrame([*_held_rows(), race]))
    expected = scenarios["baseline"]["expected_dem_seats"]
    assert scenarios["national_dem_miss_m3"]["expected_dem_seats"] == pytest.approx(expected)
    assert scenarios["national_rep_miss_m3"]["expected_dem_seats"] == pytest.approx(expected)
    assert scenarios["reduced_poll_quality"]["expected_dem_seats"] == pytest.approx(expected)


def test_merge_preserves_ordinary_and_existing_exception_columns() -> None:
    base_draws = np.arange(18, dtype=float).reshape(6, 3)
    base = FitResult(["ordinary", "id", "ne"], ["AA", "ID", "NE"], base_draws.mean(0), base_draws.std(0), base_draws.copy(), {}, {"exceptional_adapter": {"method": "existing"}}, "base")
    alaska = FitResult([RACE_ID], ["AK"], np.array([0.0]), np.array([1.0]), np.ones((6, 1)), {}, {"method": "ak", "race_id": RACE_ID}, "ak")
    merged = merge_alaska_fit(base, alaska)
    assert np.array_equal(merged.draws_margin[:, :3], base_draws)
    assert merged.diagnostics["exceptional_adapter"] == {"method": "existing"}


def test_support_contract_is_limited_warning_not_binary_score() -> None:
    supported, status, reason = probability_support_status(
        {"race_id": RACE_ID, "contest_structure": "ranked_choice_multiway"},
        n_compatible_polls=16,
    )
    assert supported is True
    assert status == "limited_supported"
    assert reason == "limited_validation_alaska_rcv_model"


def test_historical_validation_excludes_each_held_out_transfer_truth() -> None:
    report = build_alaska_rcv_validation(n_draws=80)
    assert report["calibration_claim_allowed"] is False
    assert report["ordinary_oof_touched"] is False
    assert all(row["held_out_from_transfer_fit"] for row in report["historical_cases"])
    assert all(row["probability_or_calibration_claim"] is False for row in report["historical_cases"])
    assert report["aggregate"]["mass_conservation_all"] is True


def test_source_manifest_and_semantic_hash_are_deterministic() -> None:
    before = load_alaska_rcv_evidence()["semantic_sha256"]
    manifest_path = Path("data/manifests/alaska_rcv_sources.json")
    manifest_before = manifest_path.read_bytes()
    after = prepare_alaska_rcv_evidence()["semantic_sha256"]
    assert after == before
    assert manifest_path.read_bytes() == manifest_before
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["sources"]) == 7


def test_alaska_evidence_loads_when_checkout_uses_lf_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CI Linux checkouts are LF; Windows may seal CRLF. Semantic hash must still pass."""
    import midterms.evidence.alaska_rcv as alaska
    import midterms.evidence.non_major_contract as contract

    src_norm = Path("data/normalized/alaska_rcv_2026.json")
    src_man = Path("data/manifests/alaska_rcv_sources.json")
    payload = json.loads(src_norm.read_text(encoding="utf-8"))
    # Force CRLF on disk while keeping semantic content identical to sealed evidence.
    crlf_norm = tmp_path / "alaska_rcv_2026.json"
    crlf_norm.write_bytes(
        (json.dumps(payload, indent=2) + "\n").replace("\n", "\r\n").encode("utf-8")
    )
    man = json.loads(src_man.read_text(encoding="utf-8"))
    # Manifest still carries the repository LF byte digest.
    man_path = tmp_path / "alaska_rcv_sources.json"
    man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(alaska, "NORMALIZED_PATH", crlf_norm)
    monkeypatch.setattr(alaska, "MANIFEST_PATH", man_path)

    evidence = alaska.load_alaska_rcv_evidence()
    assert evidence["semantic_sha256"] == man["normalized_semantic_sha256"]
    # Byte digest may differ under EOL rewrite; support status must remain green.
    assert alaska._sha(crlf_norm) != man.get("normalized_sha256")
    supported, status, reason = contract.probability_support_status(
        {"race_id": RACE_ID, "contest_structure": "ranked_choice_multiway"},
        n_compatible_polls=16,
    )
    assert supported is True
    assert status == "limited_supported"
    assert reason == "limited_validation_alaska_rcv_model"


def _pairwise_location_stub(p_peltola: float, sd: float = 0.02) -> dict:
    return {
        "p_peltola": float(p_peltola),
        "sd": float(sd),
        "n": 1,
        "enop": 1.0,
        "poll_ids": ["pairwise-stub"],
        "measurement_contract": "directional stub",
        "measurement_type_counts": {"stub": 1},
    }


def _peltola_win_prob(
    monkeypatch: pytest.MonkeyPatch,
    *,
    p_peltola: float,
    sd: float = 0.02,
    n_draws: int = 2500,
    seed: int = 20261005,
) -> float:
    import midterms.model.alaska_rcv_adapter as adapter

    monkeypatch.setattr(
        adapter,
        "_poll_pairwise_location",
        lambda polls, cutoff: _pairwise_location_stub(p_peltola, sd),
    )
    fit = fit_alaska_rcv_adapter(
        pd.DataFrame(),
        as_of="2026-10-03",
        n_draws=n_draws,
        seed=seed,
        common_shock_strength=0.0,
    )
    peltola_idx = fit.candidate_ids.index(f"{RACE_ID}:mary-peltola")
    return float(np.mean(fit.winner_indices == peltola_idx))


def test_pairwise_directional_vs_neutral_moves_peltola_win_prob(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pairwise mean below/above 50% must move Peltola win probability directionally."""
    assert ADAPTER_SPEC_VERSION == "alaska-rcv-adapter-v2"
    p40 = _peltola_win_prob(monkeypatch, p_peltola=0.40)
    p50 = _peltola_win_prob(monkeypatch, p_peltola=0.50)
    p60 = _peltola_win_prob(monkeypatch, p_peltola=0.60)
    assert p40 < p50 < p60


def test_pairwise_uncertainty_only_does_not_reverse_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same means with larger pairwise SD must not reverse the directional ordering."""
    tight = {
        0.40: _peltola_win_prob(monkeypatch, p_peltola=0.40, sd=0.02, seed=11),
        0.50: _peltola_win_prob(monkeypatch, p_peltola=0.50, sd=0.02, seed=11),
        0.60: _peltola_win_prob(monkeypatch, p_peltola=0.60, sd=0.02, seed=11),
    }
    wide = {
        0.40: _peltola_win_prob(monkeypatch, p_peltola=0.40, sd=0.12, seed=11),
        0.50: _peltola_win_prob(monkeypatch, p_peltola=0.50, sd=0.12, seed=11),
        0.60: _peltola_win_prob(monkeypatch, p_peltola=0.60, sd=0.12, seed=11),
    }
    assert tight[0.40] < tight[0.50] < tight[0.60]
    assert wide[0.40] < wide[0.50] < wide[0.60]
