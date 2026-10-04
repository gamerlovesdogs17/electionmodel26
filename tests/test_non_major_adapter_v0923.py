"""Cheap synthetic and archive-backed checks for the v0.9.23 exception path."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from midterms.config import ARTIFACTS_DIR
from midterms.evidence.non_major_contract import probability_support_status
from midterms.evidence.outcome_identity import INDEPENDENT_DEM_CAUCUSES_BASIS
from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.non_major_adapter import (
    fit_non_major_adapter,
    merge_fit_results,
    modeled_candidate_margin,
    ordinary_model_snapshot,
)
from midterms.model.overlays import (
    rating_from_modeled_probability,
    ratings_table_from_forecasts,
)
from midterms.model.pymc_model import FitResult
from midterms.simulate.chamber import simulate_chamber
from midterms.validation.historical_evidence_equivalence import (
    build_historical_projection,
)
from midterms.validation.non_major_adapter import build_non_major_adapter_validation


def _race(**overrides):
    row = {
        "race_id": "synthetic-I", "state": "ZZ", "not_up": False,
        "held_by": "R", "prior_lean": -5.0, "incumbent_party": "R",
        "prior_source": "observed_presidential_relative_v1",
        "prior_production_eligible": True,
        "prior_snapshot_sha256": "a" * 64,
        "prior_provenance_sha256": "b" * 64,
        "is_open": False, "seat_class": "II", "ballot_status": "nominated",
        "election_phase": "general", "contest_structure": "non_major_party_vs_republican",
        "current_matchup_status": "reviewed_current",
        "modeled_candidate_id": "candidate:independent", "modeled_candidate_name": "Indigo Ash",
        "modeled_ballot_party": "I", "modeled_caucus": "D",
        "modeled_caucus_basis": INDEPENDENT_DEM_CAUCUSES_BASIS,
        "opposing_candidate_id": "candidate:republican", "opposing_candidate_name": "Riley Oak",
        "opposing_ballot_party": "R", "opposing_caucus": "R",
        "opposing_caucus_basis": "synthetic_major_party",
        "binary_score_eligible": False, "probability_model_supported": True,
    }
    row.update(overrides)
    return row


def _poll(**overrides):
    row = {
        "poll_id": "poll-1", "study_id": "study-1", "pollster_id": "Synthetic Polls",
        "race_id": "synthetic-I", "field_start": "2026-09-01",
        "field_end": "2026-09-03", "available_at": "2026-09-04",
        "sample_size": 600, "population": "LV", "partisan": False,
        "quality_weight": 1.0, "modeled_candidate_id": "poll:independent",
        "modeled_candidate_name": "Indigo Ash", "modeled_ballot_party": "I",
        "opposing_candidate_id": "poll:republican", "opposing_candidate_name": "Riley Oak",
        "opposing_ballot_party": "R", "modeled_share": 47.0,
        "opposing_share": 53.0, "modeled_margin": -6.0,
        "two_party_margin": np.nan, "margin_definition": "independent_minus_rep_two_candidate",
    }
    row.update(overrides)
    return row


def _base_fit(n: int = 2000) -> FitResult:
    rng = np.random.default_rng(13)
    common = rng.normal(size=n)
    draws = np.column_stack([
        2.0 * common + rng.normal(size=n),
        1.5 * common + rng.normal(size=n),
    ])
    return FitResult(
        race_ids=["ordinary-a", "ordinary-b"], states=["AA", "BB"],
        mean_margin=draws.mean(axis=0), sd_margin=draws.std(axis=0),
        draws_margin=draws, house_effects={}, diagnostics={}, method="synthetic-ordinary",
    )


def test_candidate_neutral_margin_and_no_legacy_margin() -> None:
    assert modeled_candidate_margin(47, 53) == -6.0
    poll = _poll()
    assert np.isnan(poll["two_party_margin"])
    assert poll["modeled_margin"] == -6.0


def test_independent_poll_keeps_legacy_two_party_margin_empty() -> None:
    poll = _poll(modeled_margin=4.5, modeled_candidate_margin=4.5)
    assert np.isnan(poll["two_party_margin"])
    assert poll["modeled_candidate_margin"] == 4.5


def test_independent_identity_and_caucus_remain_separate() -> None:
    race = _race()
    supported, status, _ = probability_support_status(race, n_compatible_polls=1)
    assert supported and status == "limited_supported"
    assert race["modeled_ballot_party"] == "I"
    assert race["modeled_caucus"] == "D"


def test_caucus_assumption_does_not_mutate_ballot_party() -> None:
    race = _race(modeled_caucus="D")
    assert race["modeled_ballot_party"] == "I"
    assert race["modeled_caucus_basis"] == INDEPENDENT_DEM_CAUCUSES_BASIS


def test_standard_dem_rep_and_alaska_do_not_enter_adapter() -> None:
    ordinary = _race(
        race_id="ordinary", contest_structure="binary_dem_vs_rep",
        modeled_ballot_party="D", modeled_candidate_name="Dana Elm",
        binary_score_eligible=True,
    )
    alaska = _race(
        race_id="alaska", contest_structure="ranked_choice_multiway",
        modeled_ballot_party="D", modeled_candidate_name="Avery Ice",
        binary_score_eligible=False, probability_model_supported=False,
    )
    fit = fit_non_major_adapter(
        pd.DataFrame([ordinary, alaska]), pd.DataFrame(),
        as_of="2026-10-03", n_draws=100, seed=1,
    )
    assert fit.race_ids == []


def test_compatible_poll_enters_and_nonmatching_poll_is_excluded() -> None:
    polls = pd.DataFrame([
        _poll(),
        _poll(poll_id="obsolete", modeled_candidate_name="Former Candidate", modeled_margin=30.0),
    ])
    fit = fit_non_major_adapter(
        pd.DataFrame([_race()]), polls,
        as_of="2026-10-03", n_draws=1000, seed=2,
    )
    assert fit.race_ids == ["synthetic-I"]
    record = fit.diagnostics["records"][0]
    assert record["poll_ids"] == ["poll-1"]
    assert record["n_candidate_compatible_polls"] == 1


def test_zero_poll_exception_uses_explicit_wide_structural_prior() -> None:
    fit = fit_non_major_adapter(
        pd.DataFrame([_race()]), pd.DataFrame(columns=pd.DataFrame([_poll()]).columns),
        as_of="2026-10-03", n_draws=100, seed=3,
    )
    assert fit.draws_margin.shape == (100, 1)
    record = fit.diagnostics["records"][0]
    assert record["support_status"] == "limited_supported_prior_only"
    assert record["prior_only"] is True
    assert record["n_candidate_compatible_polls"] == 0
    assert record["prior_location"] == -5.0
    assert record["predictive_sd"] > 30.0
    assert abs(float(fit.mean_margin[0])) > 1.0


def test_zero_poll_without_sourced_structural_prior_remains_withheld() -> None:
    race = _race(
        prior_source="synthetic_fixture",
        prior_production_eligible=False,
        prior_snapshot_sha256=None,
        prior_provenance_sha256=None,
    )
    fit = fit_non_major_adapter(
        pd.DataFrame([race]), pd.DataFrame(columns=pd.DataFrame([_poll()]).columns),
        as_of="2026-10-03", n_draws=100, seed=3,
    )
    assert fit.draws_margin.shape == (100, 0)
    assert fit.diagnostics["records"][0]["support_status"] == "withheld"


def test_merge_preserves_ordinary_draws_and_adds_correlated_exception() -> None:
    ordinary = _base_fit()
    exceptional = fit_non_major_adapter(
        pd.DataFrame([_race()]), pd.DataFrame([_poll()]),
        as_of="2026-10-03", n_draws=ordinary.draws_margin.shape[0],
        seed=4, base_fit=ordinary,
    )
    merged = merge_fit_results(ordinary, exceptional)
    assert np.array_equal(merged.draws_margin[:, :2], ordinary.draws_margin)
    ordinary_common = ordinary.draws_margin.mean(axis=1)
    assert np.corrcoef(ordinary_common, merged.draws_margin[:, 2])[0, 1] > 0.1


def test_ordinary_summary_statistics_are_unchanged_by_merge() -> None:
    ordinary = _base_fit()
    exceptional = fit_non_major_adapter(
        pd.DataFrame([_race()]), pd.DataFrame([_poll()]),
        as_of="2026-10-03", n_draws=ordinary.draws_margin.shape[0],
        seed=14, base_fit=ordinary,
    )
    merged = merge_fit_results(ordinary, exceptional)
    assert np.array_equal(merged.mean_margin[:2], ordinary.mean_margin)
    assert np.array_equal(merged.sd_margin[:2], ordinary.sd_margin)
    assert merged.race_ids[:2] == ordinary.race_ids


def test_candidate_neutral_output_has_no_p_dem_and_chamber_uses_caucus() -> None:
    rows = [
        {"race_id": f"held-r-{i}", "state": "ZZ", "not_up": True, "held_by": "R"}
        for i in range(99)
    ] + [_race()]
    fit = FitResult(
        race_ids=["synthetic-I"], states=["ZZ"], mean_margin=np.array([0.0]),
        sd_margin=np.array([1.0]), draws_margin=np.array([[1.0], [-1.0]]),
        house_effects={}, diagnostics={
            "target": "modeled_candidate_margin",
            "method": "limited_validation_exception_model-v1",
            "records": [{"race_id": "synthetic-I", "predictive_sd": 1.0}],
        }, method="limited_validation_exception_model-v1",
    )
    sim, summaries = simulate_chamber(fit, pd.DataFrame(rows))
    assert sim.seat_draws.tolist() == [1, 0]
    assert summaries[0]["p_modeled_candidate"] == 0.5
    assert summaries[0]["p_opposing_candidate"] == 0.5
    assert summaries[0]["modeled_candidate_id"] == "candidate:independent"
    assert summaries[0]["opposing_candidate_id"] == "candidate:republican"
    assert summaries[0]["modeled_ballot_party"] == "I"
    assert summaries[0]["modeled_caucus"] == "D"
    assert summaries[0]["method"] == "limited_validation_exception_model-v1"
    assert "uncertainty_metadata" in summaries[0]
    assert "p_dem" not in summaries[0]
    assert "dem_candidate" not in summaries[0]
    assert "dem_party" not in summaries[0]
    assert summaries[0]["rating"] == "Tossup"


def test_ordinary_output_retains_legacy_dem_probability_aliases() -> None:
    ordinary = _race(
        race_id="ordinary-D", modeled_ballot_party="D",
        contest_structure="binary_dem_vs_rep", binary_score_eligible=True,
        modeled_candidate_name="Dana Elm",
    )
    held = [
        {"race_id": f"held-r-{i}", "state": "ZZ", "not_up": True, "held_by": "R"}
        for i in range(99)
    ]
    fit = FitResult(
        race_ids=["ordinary-D"], states=["ZZ"], mean_margin=np.array([0.0]),
        sd_margin=np.array([1.0]), draws_margin=np.array([[1.0], [-1.0]]),
        house_effects={}, diagnostics={}, method="synthetic",
    )
    _sim, summaries = simulate_chamber(fit, pd.DataFrame([*held, ordinary]))
    assert summaries[0]["p_dem"] == summaries[0]["p_modeled_candidate"]
    assert summaries[0]["p_rep"] == summaries[0]["p_opposing_candidate"]
    assert summaries[0]["dem_party"] == "D"


def test_poll_dominates_weak_state_anchor() -> None:
    race = _race(prior_lean=-25.0)
    poll = _poll(modeled_margin=1.1, modeled_share=50.55, opposing_share=49.45)
    fit = fit_non_major_adapter(
        pd.DataFrame([race]), pd.DataFrame([poll]),
        as_of="2026-10-03", n_draws=4000, seed=17,
    )
    record = fit.diagnostics["records"][0]
    assert record["poll_weighted_location"] == pytest.approx(1.1)
    assert abs(record["posterior_location"] - 1.1) < abs(record["posterior_location"] + 25.0)
    assert record["posterior_location"] > -5.0


def test_candidate_neutral_rating_does_not_relabel_independent_as_democrat() -> None:
    assert rating_from_modeled_probability(
        0.70, modeled_ballot_party="I", opposing_ballot_party="R"
    ) == "Lean I"
    assert rating_from_modeled_probability(
        0.30, modeled_ballot_party="I", opposing_ballot_party="R"
    ) == "Lean R"
    table = ratings_table_from_forecasts([{
        "race_id": "synthetic-I",
        "state": "ZZ",
        "p_modeled_candidate": 0.70,
        "modeled_ballot_party": "I",
        "opposing_ballot_party": "R",
    }])
    assert table.iloc[0]["rating"] == "Lean I"


def test_adapter_diagnostics_identify_candidate_neutral_probability_target() -> None:
    fit = fit_non_major_adapter(
        pd.DataFrame([_race()]), pd.DataFrame([_poll()]),
        as_of="2026-10-03", n_draws=500, seed=15,
    )
    record = fit.diagnostics["records"][0]
    assert fit.diagnostics["target"] == "modeled_candidate_margin"
    assert record["modeled_candidate_id"] == "candidate:independent"
    assert record["opposing_candidate_id"] == "candidate:republican"


def test_ordinary_snapshot_is_additive_and_does_not_mutate_input() -> None:
    ordinary = _race(
        race_id="ordinary", contest_structure="binary_dem_vs_rep",
        modeled_ballot_party="D", binary_score_eligible=True,
    )
    races = pd.DataFrame([ordinary, _race()])
    polls = pd.DataFrame([
        _poll(race_id="ordinary", modeled_ballot_party="D"), _poll(),
    ])
    snapshot = EvidenceSnapshot(
        as_of=date(2026, 10, 3), election_id="senate-2026", polls=polls,
        races=races, results_known=pd.DataFrame(), snapshot_id="synthetic",
    )
    selected = ordinary_model_snapshot(snapshot)
    assert selected.races["race_id"].tolist() == ["ordinary"]
    assert snapshot.races["race_id"].tolist() == ["ordinary", "synthetic-I"]


def test_archive_backed_validation_is_limited_and_deterministic() -> None:
    first = build_non_major_adapter_validation()
    second = build_non_major_adapter_validation()
    assert first["artifact_sha256"] == second["artifact_sha256"]
    assert first["aggregate"]["n"] == 4
    assert first["aggregate"]["calibration_claim_allowed"] is False
    assert first["selected_prior_spec_id"] == "state_structural_very_large"
    assert set(first["prior_specification_comparison"]) == {
        "zero_centered_weak",
        "state_structural_very_large",
        "state_structural_extra_large",
    }
    assert first["leave_one_race_out_integrity"]["safe"] is True
    assert all(
        case["exceptional_outcome_used_to_set_prior"] is False
        for block in first["prior_specification_comparison"].values()
        for case in block["cases"]
    )
    assert all(
        source["sha256"]
        for source in first["source_lineage"].values()
    )
    statuses = {row["state"]: row["n_usable_polls"] for row in first["current_poll_audit"]}
    assert statuses == {"ID": 1, "MT": 6, "NE": 1, "SD": 0}


def test_common_shock_sensitivity_preserves_marginals_and_changes_dependence() -> None:
    report = build_non_major_adapter_validation()
    results = report["common_shock_sensitivity"]["results"]
    assert report["common_shock_sensitivity"]["retained_share"] == 0.2
    for race_id in results["0.2"]["p_modeled_candidate_by_race"]:
        probabilities = [
            results[key]["p_modeled_candidate_by_race"][race_id]
            for key in ("0.0", "0.2", "0.4")
        ]
        assert max(probabilities) - min(probabilities) < 0.04
    assert results["0.0"]["exceptional_seat_count_variance"] < (
        results["0.2"]["exceptional_seat_count_variance"]
    ) < results["0.4"]["exceptional_seat_count_variance"]


def test_current_nebraska_poll_moves_posterior_and_sd_is_wide_prior_only() -> None:
    report = build_non_major_adapter_validation()
    records = report["selected_current_adapter_records"]
    nebraska = records["senate-2026-NE"]
    assert nebraska["n_candidate_compatible_polls"] == 1
    assert nebraska["poll_weighted_location"] == pytest.approx(1.075)
    assert abs(
        nebraska["posterior_location"] - nebraska["poll_weighted_location"]
    ) < abs(nebraska["posterior_location"] - nebraska["prior_location"])
    south_dakota = report["zero_poll_behavior"]["state_structural_very_large"]
    assert south_dakota["support_status"] == "limited_supported_prior_only"
    assert south_dakota["prior_location"] != 0.0
    assert south_dakota["predictive_sd"] > 30.0
    assert south_dakota["interval_90_width"] > 90.0


def test_web_accessors_prioritize_candidate_neutral_fields() -> None:
    root = ARTIFACTS_DIR.parent.parent
    utilities = (root / "web" / "src" / "lib" / "utils.ts").read_text(encoding="utf-8")
    table = (root / "web" / "src" / "components" / "race-table.tsx").read_text(
        encoding="utf-8"
    )
    senate_map = (root / "web" / "src" / "components" / "senate-map.tsx").read_text(
        encoding="utf-8"
    )
    assert "race.p_modeled_candidate ?? race.p_dem" in utilities
    assert "race.p_opposing_candidate ?? race.p_rep" in utilities
    assert "r.modeled_ballot_party ?? r.dem_party" in table
    assert "race.modeled_ballot_party ?? race.dem_party" in senate_map


def test_formal_historical_evidence_projection_remains_equivalent() -> None:
    frozen = json.loads(
        (ARTIFACTS_DIR / "historical_evidence_equivalence_v0923.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {
        row["cutoff"]: row["after_semantic_sha256"]
        for row in frozen["formal_cutoffs"]
    }
    current = build_historical_projection()
    actual = {
        cutoff: row["semantic_sha256"]
        for cutoff, row in current["cutoffs"].items()
    }
    assert actual == expected


def test_current_coverage_separates_limited_withheld_and_unsupported() -> None:
    coverage = json.loads(
        (ARTIFACTS_DIR / "current_race_poll_coverage_v0923.json").read_text(
            encoding="utf-8"
        )
    )
    by_state = {row["state"]: row for row in coverage["races"]}
    assert all(
        by_state[state]["probability_model_support_status"] == "limited_supported"
        for state in ("ID", "MT", "NE")
    )
    assert by_state["SD"]["probability_model_support_status"] == "limited_supported_prior_only"
    assert by_state["SD"]["n_candidate_compatible_polls"] == 0
    assert by_state["AK"]["probability_model_support_status"] == "unsupported"
    assert by_state["AK"]["evidence_status"] == "pass"
