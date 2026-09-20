"""Synthetic candidate-contract, identity, decomposition, and lineage tests."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from midterms.evidence import markets as market_module
from midterms.evidence.markets import audit_race_event, map_race_event
from midterms.evidence.outcome_identity import CandidateOutcomeIdentity, ContestIdentity
from midterms.validation.artifact_lineage import ArtifactIdentity, check_lineage
from midterms.validation.decomposition_schema import Decomposition, DiagnosticTerm


def _contract(event, suffix, title, price):
    return {"ticker": f"{event}-{suffix}", "title": title,
            "last_price_dollars": str(price)}


def test_named_nonmajor_contract_overrides_generic_party_marker():
    event = "SYNTHETIC-ONE"
    ticket = {"dem_name": "Avery Cedar", "dem_party": "I", "rep_name": "Blair Birch",
              "modeled_candidate_id": "cedar", "republican_candidate_id": "birch"}
    contracts = [
        _contract(event, "ACED", "Avery Cedar outcome", 0.3),
        _contract(event, "R", "Blair Birch outcome", 0.4),
        _contract(event, "D", "Casey Maple outcome", 0.2),
    ]
    mapped, reason = map_race_event(
        race_id="synthetic-contest", event_ticker=event, markets=contracts, ticket=ticket,
    )
    assert reason is None
    assert mapped["modeled_candidate_ticker"] == f"{event}-ACED"
    assert mapped["market_mapping"] == "named_candidate"
    assert mapped["normalization_contracts"] == 3
    assert sum(c["p_normalized"] for c in mapped["candidate_contracts"]) == pytest.approx(1.0)
    audit = audit_race_event(
        race_id="synthetic-contest", event_ticker=event, markets=contracts, ticket=ticket,
    )
    assert audit["overlay_enabled"] is True
    assert audit["modeled_entity_id"] == "cedar"
    assert audit["matched_modeled_contract_id"] == f"{event}-ACED"
    assert len(audit["raw_contract_prices"]) == 3


def test_ordinary_two_contract_event_and_ambiguity_disable():
    event = "SYNTHETIC-TWO"
    ticket = {"dem_name": "Avery Cedar", "dem_party": "D", "rep_name": "Blair Birch"}
    ordinary = [_contract(event, "D", "Generic party contract", 0.4),
                _contract(event, "R", "Generic party contract", 0.5)]
    mapped, reason = map_race_event(
        race_id="synthetic-contest", event_ticker=event, markets=ordinary, ticket=ticket,
    )
    assert reason is None
    assert mapped["market_mapping"] == "party_contract_with_identity_registry"
    ambiguous = [_contract(event, "ACED", "Avery Cedar outcome", 0.3),
                 _contract(event, "ACED2", "Avery Cedar outcome", 0.2),
                 _contract(event, "R", "Blair Birch outcome", 0.4)]
    audit = audit_race_event(
        race_id="synthetic-contest", event_ticker=event, markets=ambiguous, ticket=ticket,
    )
    assert audit["overlay_enabled"] is False
    assert audit["ambiguous_or_unsafe"] is True
    assert audit["disable_reason"]
    assert audit["matched_modeled_contract_id"] is None
    assert sum(audit["normalized_event_probabilities"].values()) == pytest.approx(1.0)


def test_other_ballot_label_requires_named_contract():
    event = "SYNTHETIC-THREE"
    ticket = {"dem_name": "Avery Cedar", "dem_party": "G", "rep_name": "Blair Birch"}
    contracts = [_contract(event, "ACED", "Avery Cedar outcome", 0.3),
                 _contract(event, "R", "Blair Birch outcome", 0.6)]
    mapped, reason = map_race_event(
        race_id="synthetic-contest", event_ticker=event, markets=contracts, ticket=ticket,
    )
    assert reason is None
    assert mapped["modeled_candidate_ticker"] == f"{event}-ACED"
    contracts[0] = _contract(event, "G", "Generic party contract", 0.3)
    unsafe = audit_race_event(
        race_id="synthetic-contest", event_ticker=event, markets=contracts, ticket=ticket,
    )
    assert unsafe["overlay_enabled"] is False


def test_stale_parser_rows_are_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(market_module, "NORMALIZED_DIR", tmp_path)
    pd.DataFrame([
        {"race_id": "synthetic-a", "parser_version": "old-parser", "available_at": "2015-01-01"},
        {"race_id": "synthetic-b", "parser_version": market_module.PARSER_VERSION,
         "available_at": "2015-01-01"},
    ]).to_parquet(tmp_path / "markets.parquet", index=False)
    loaded = market_module.load_race_markets(as_of="2015-01-02")
    assert list(loaded["race_id"]) == ["synthetic-b"]


def test_candidate_party_and_caucus_are_separate():
    independent = CandidateOutcomeIdentity("cedar", "I", "modeled")
    opponent = CandidateOutcomeIdentity("birch", "R", "opposing", "caucus_b", "declared_assumption")
    contest = ContestIdentity("synthetic-contest", (independent, opponent))
    assert contest.ballot_party_for_candidate("cedar") == "I"
    with pytest.raises(ValueError, match="unknown"):
        contest.caucus_for_candidate("cedar")
    with pytest.raises(ValueError, match="two-party margin"):
        ContestIdentity("synthetic-contest", (independent, opponent), two_party_margin_eligible=True)
    declared = CandidateOutcomeIdentity("cedar", "I", "modeled", "caucus_a", "explicit_model_assumption")
    assert ContestIdentity("synthetic-contest", (declared, opponent)).caucus_for_candidate("cedar") == "caucus_a"
    with pytest.raises(ValueError, match="occur together"):
        CandidateOutcomeIdentity("cedar", "I", "modeled", "caucus_a")


def test_decomposition_only_checks_declared_exact_overlay_arithmetic():
    term = lambda name, value, kind: DiagnosticTerm(name, value, kind, "synthetic")
    report = Decomposition(
        subject_id="synthetic-subject",
        base_prior=term("base", 1.0, "additive_location"),
        prior_provenance={"method": "synthetic"},
        fundamentals_anchor=term("anchor", 2.0, "posterior_location"),
        stacked_core_location=term("core", 3.0, "posterior_location"),
        final_location=term("final", 4.5, "posterior_location"),
        final_scale=term("scale", 2.0, "uncertainty_component"),
        terms=(term("overlay", 1.5, "overlay_shift"),
               term("joint", 99.0, "nonlinear_joint_effect")),
        effective_sample_size=4.0, exact_overlay_chain=True,
    )
    assert json.loads(json.dumps(report.as_dict()))["final_location"]["value"] == 4.5
    bad = Decomposition(**{**report.__dict__, "final_location": term("final", 4.0, "posterior_location")})
    with pytest.raises(ValueError, match="overlay shifts"):
        bad.validate()


def test_lineage_rejects_stale_or_cross_run_identity():
    identity = ArtifactIdentity("run_x", "v1", "snap_x", "e" * 64, "f" * 64)
    matching = identity.__dict__.copy()
    assert check_lineage(identity, {"artifact_a": matching, "artifact_b": matching})["ok"] is True
    stale = {**matching, "snapshot_id": "old_snap"}
    result = check_lineage(identity, {"artifact_a": matching, "artifact_b": stale})
    assert result["ok"] is False
    assert result["failures"] == ["artifact_b.snapshot_id differs from expected identity"]
    assert len(result["expected_identity_sha256"]) == 64
