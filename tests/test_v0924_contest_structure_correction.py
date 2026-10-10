"""Corrective v0.9.24 tests: non-circular ballot fields and multiway fail-closed."""

from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from midterms.config import MODEL_VERSION, PUBLIC_LIVE_ENABLED
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.evidence.non_major_contract import (
    non_major_identity_supported,
    probability_support_status,
)
from midterms.model.multiway_plurality import (
    MULTIWAY_CONTEST_STRUCTURE,
    historical_analog_support_report,
    plurality_winners,
    unsupported_multiway_race_payload,
    MultiwayCandidate,
)
from midterms.model.poll_weights import attach_poll_weights
from midterms.presentation.race_view import present_race
from midterms.validation.contest_field_audits_v0924 import (
    build_mt_id_contest_field_audit,
    build_mt_id_poll_inclusion_audit,
)
from midterms.validation.official_ballot_fields import (
    certified_candidates,
    load_official_ballot_fields,
)
from midterms.validation.nonmajor_contest_structure_audit_v0924 import (
    build_nonmajor_contest_structure_audit,
)


def test_lineage_still_v0924_research_only():
    assert MODEL_VERSION == "senate-hierarchical-v0.9.25"
    assert PUBLIC_LIVE_ENABLED is False


def test_official_ballot_fields_are_not_registry_circular():
    payload = load_official_ballot_fields()
    assert payload["registry_used_as_ballot_authority"] is False
    assert "current_candidates_2026_registry" in payload["disallowed_authorities"]
    for state in ("MT", "ID", "NE", "SD"):
        race = payload["races"][state]
        assert "source_url" in race["authority"]
        assert race["authority"].get("issuer")
        assert all(
            c["status"] == "certified_general_ballot" for c in race["candidates"]
        )


def test_mt_official_field_is_four_way_plurality():
    names = {c["candidate_name"] for c in certified_candidates("MT")}
    assert names == {"Kurt Alme", "Kyle Austin", "Alani Bankhead", "Seth Bodnar"}
    parties = {
        c["candidate_name"]: c["ballot_party"] for c in certified_candidates("MT")
    }
    assert parties["Alani Bankhead"] == "D"
    assert parties["Kyle Austin"] == "L"
    audit = build_mt_id_contest_field_audit()
    mt = next(r for r in audit["races"] if r["state"] == "MT")
    assert mt["registry_used_as_ballot_authority"] is False
    assert mt["verified_contest_structure"] == MULTIWAY_CONTEST_STRUCTURE
    assert mt["prior_binary_probabilities_invalid"] is True
    assert mt["win_probability_status"] == "fail_closed"


def test_id_official_field_is_four_way_ballot_principal_binary():
    names = {c["candidate_name"] for c in certified_candidates("ID")}
    assert names == {"Todd Achilles", "Natalie Fleming", "Matt Loesby", "Jim Risch"}
    audit = build_mt_id_contest_field_audit()
    idaho = next(r for r in audit["races"] if r["state"] == "ID")
    # Four ballot lines, but historical principal-binary-with-minors criterion applies.
    assert idaho["n_certified_general_ballot_candidates"] == 4
    assert idaho["verified_contest_structure"] == "principal_binary_with_minor_residual"
    assert idaho["recommended_modeling_path"] == "candidate_neutral_binary_with_minor_residual"


def test_registry_matches_official_multiway_and_sd_binary():
    reg = load_current_candidate_registry()
    by = {r["state"]: r for r in reg["races"]}
    assert by["MT"]["contest_structure"] == MULTIWAY_CONTEST_STRUCTURE
    assert by["MT"]["probability_model_support_status"] == "unsupported"
    assert by["MT"]["exceptional_probability_model_supported"] is False
    assert not non_major_identity_supported(by["MT"])
    for state in ("ID", "NE"):
        assert by[state]["contest_structure"] == "principal_binary_with_minor_residual"
        assert by[state]["probability_model_support_status"] == "limited_supported"
        assert by[state]["exceptional_probability_model_supported"] is True
        assert non_major_identity_supported(by[state])
    assert by["SD"]["contest_structure"] == "non_major_party_vs_republican"
    assert by["SD"]["probability_model_support_status"] == "limited_supported"
    assert non_major_identity_supported(by["SD"])


def test_poll_observed_name_alone_is_not_ballot_qualified():
    # Policy: a poll-observed name that is not on the certified ballot must be
    # classified as outdated_candidate_field and excluded (no fake multiway
    # renormalization). Do not require any particular live VoteHub matchup
    # (e.g. historical Reilly Neill rows) to remain in the warehouse forever.
    from midterms.validation.contest_field_audits_v0924 import _classify_poll

    certified = certified_candidates("MT")
    official_names = {c["candidate_name"] for c in certified}
    official_slugs = {str(c["candidate_id"]).split(":")[-1] for c in certified}
    assert "Reilly Neill" not in official_names
    assert official_slugs, "MT certified ballot field must be non-empty"
    synthetic = {"reilly-neill", next(iter(sorted(official_slugs)))}
    assert _classify_poll(synthetic, official_slugs) == "outdated_candidate_field"

    polls = build_mt_id_poll_inclusion_audit()
    outdated = [
        row
        for row in polls["rows"]
        if row["state"] == "MT" and row["poll_type"] == "outdated_candidate_field"
    ]
    assert all(row["included"] is False for row in outdated)
    assert all(row["share_renormalized_to_fake_multiway"] is False for row in outdated)


def test_binary_bodnar_alme_polls_excluded_under_multiway():
    polls = build_mt_id_poll_inclusion_audit()
    binary = [
        row
        for row in polls["rows"]
        if row["state"] == "MT" and row["poll_type"] == "explicit_binary_matchup"
    ]
    assert binary
    assert all(row["included"] is False for row in binary)
    assert polls["n_included"] == 0


def test_omitted_official_candidate_detected_as_registry_error():
    # Synthetic: pretend registry lost Kyle Austin.
    from midterms.validation import contest_field_audits_v0924 as mod

    real = load_current_candidate_registry()
    mt = next(r for r in real["races"] if r["state"] == "MT")
    kept = [
        c
        for c in mt["ballot_candidates"]
        if "kyle-austin" not in str(c.get("candidate_id"))
    ]
    assert len(kept) == len(mt["ballot_candidates"]) - 1

    class _Fake:
        def __getitem__(self, key):
            if key == "races":
                clone = copy.deepcopy(mt)
                clone["ballot_candidates"] = kept
                clone["contest_structure"] = "non_major_party_vs_republican"
                return [clone]
            raise KeyError(key)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod, "load_current_candidate_registry", lambda: {"races": _Fake()["races"]})
    try:
        audit = mod.build_mt_id_contest_field_audit()
        mt_audit = next(r for r in audit["races"] if r["state"] == "MT")
        assert "kyle-austin" in mt_audit["registry_errors"]["official_candidates_omitted_from_registry"]
        assert mt_audit["registry_errors"]["circular_binary_assumption_detected"] is True
    finally:
        monkey.undo()


def test_ne_sd_structure_gate():
    gate = build_nonmajor_contest_structure_audit()["decision_gate"]
    assert gate["NE"]["verified_structure"] == "principal_binary_with_minor_residual"
    assert gate["NE"]["validation_level"] == "limited_supported_principal_binary"
    assert gate["ID"]["verified_structure"] == "principal_binary_with_minor_residual"
    assert gate["MT"]["verified_structure"] == MULTIWAY_CONTEST_STRUCTURE
    assert gate["SD"]["verified_structure"] == "non_major_party_vs_republican"
    assert gate["SD"]["statistical_path"] == "binary_non_major_adapter"


def test_multiway_mechanics_and_no_fake_half():
    shares = np.array([[0.4, 0.35, 0.15, 0.1]], dtype=float)
    out = plurality_winners(shares, ["a", "b", "c", "d"])
    assert out["winner_candidate_ids"][0] == "a"
    assert abs(shares.sum(axis=1)[0] - 1.0) < 1e-9
    payload = unsupported_multiway_race_payload(
        race_id="senate-2026-MT",
        candidates=[
            MultiwayCandidate("senate-2026-MT:seth-bodnar", "Seth Bodnar", "I", "D"),
            MultiwayCandidate("senate-2026-MT:kurt-alme", "Kurt Alme", "R", "R"),
            MultiwayCandidate("senate-2026-MT:alani-bankhead", "Alani Bankhead", "D", "D"),
            MultiwayCandidate("senate-2026-MT:kyle-austin", "Kyle Austin", "L", None),
        ],
        n_analogs=0,
    )
    assert payload["win_probability_status"] == "fail_closed"
    assert all(row["p_win"] is None for row in payload["candidate_probabilities"])
    assert 0.5 not in [row["p_win"] for row in payload["candidate_probabilities"]]
    support = historical_analog_support_report(n_analogs=0)
    assert support["probability_model_support_status"] == "unsupported"


def test_probability_support_status_multiway_unsupported():
    ok, status, reason = probability_support_status(
        {"contest_structure": "multiway_plurality", "race_id": "senate-2026-MT"},
        n_compatible_polls=10,
    )
    assert ok is False
    assert status == "unsupported"
    assert "multiway" in reason


def test_presentation_withholds_multiway_and_lists_candidates():
    race = {
        "race_id": "senate-2026-MT",
        "state": "MT",
        "contest_structure": "multiway_plurality",
        "modeling_path": "multiway_plurality_adapter",
        "probability_model_support_status": "unsupported",
        "win_probability_status": "fail_closed",
        "p_modeled_candidate": 0.559,
        "rating": "Tossup",
        "ballot_candidates": [
            {"candidate_id": "a", "candidate_name": "Seth Bodnar", "ballot_party": "I"},
            {"candidate_id": "b", "candidate_name": "Kurt Alme", "ballot_party": "R"},
            {"candidate_id": "c", "candidate_name": "Alani Bankhead", "ballot_party": "D"},
            {"candidate_id": "d", "candidate_name": "Kyle Austin", "ballot_party": "L"},
        ],
    }
    before = copy.deepcopy(race)
    view = present_race(race)
    assert race == before
    assert view.unsupported_probability is True
    assert view.favored_win_probability is None
    assert "multiway" in (view.unsupported_message or "").lower()
    assert len(view.candidates) == 4
    assert all(c.p_win is None for c in view.candidates)


def test_absolute_recency_and_no_n_quality_double_count():
    from datetime import date

    polls = pd.DataFrame(
        {
            "race_id": ["old", "new", "big_n", "small_n"],
            "pollster_id": ["A", "B", "C", "D"],
            "study_id": ["s1", "s2", "s3", "s4"],
            "field_end": ["2026-06-23", "2026-09-21", "2026-09-21", "2026-09-21"],
            "sample_size": [600, 600, 5000, 200],
            "quality_weight": [1.0, 1.0, 1.5, 0.5],
            "partisan": [False, False, False, False],
        }
    )
    w = attach_poll_weights(polls, as_of=date(2026, 10, 1))
    old = float(w.loc[w.race_id == "old", "influence_weight"].iloc[0])
    new = float(w.loc[w.race_id == "new", "influence_weight"].iloc[0])
    assert new > old * 2
    big = float(w.loc[w.race_id == "big_n", "influence_weight"].iloc[0])
    small = float(w.loc[w.race_id == "small_n", "influence_weight"].iloc[0])
    assert abs(big - small) < 1e-9


def test_calendar_delta_t_source_contract():
    import inspect

    from midterms.model import state_space as ss

    src = inspect.getsource(ss.fit_state_space)
    assert "delta_days" in src
    assert "process_variance_for_days" in src
    assert ss.process_variance_for_days(7) == pytest.approx(7 * 0.8**2)
