"""Synthetic candidate-contract, identity, decomposition, and lineage tests."""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from midterms.evidence import markets as market_module
from midterms.evidence.markets import audit_race_event, map_race_event, write_mapping_audit_store
from midterms.evidence.outcome_identity import (
    CandidateOutcomeIdentity, ContestIdentity, identity_from_ticket, require_explicit_caucus,
    require_binary_chamber_compatibility,
)
from midterms.validation.artifact_lineage import ArtifactIdentity, check_lineage
from midterms.validation.decomposition_schema import Decomposition, DiagnosticTerm


def _contract(event, suffix, title, price):
    return {
        "ticker": f"{event}-{suffix}", "event_ticker": event, "title": title,
        "last_price_dollars": str(price), "outcome_type": "candidate_win",
        "mutually_exclusive": True, "event_exhaustive": True,
        "contract_scope": "candidate", "candidate_id": suffix,
    }


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
    assert audit["normalized_event_probabilities"] is None


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
    monkeypatch.setattr(market_module, "MANIFESTS_DIR", tmp_path)
    audit_meta = write_mapping_audit_store(pd.DataFrame(), [], path=tmp_path / "markets_mapping_audit.json")
    pd.DataFrame([
        {"race_id": "synthetic-a", "parser_version": "old-parser", "available_at": "2015-01-01",
         "overlay_enabled": True, "mapping_audit": "{}"},
        {"race_id": "synthetic-b", "parser_version": market_module.PARSER_VERSION,
         "available_at": "2015-01-01", "overlay_enabled": True, "mapping_audit": "{}"},
    ]).to_parquet(tmp_path / "markets.parquet", index=False)
    (tmp_path / "markets_kalshi.json").write_text(json.dumps({
        "parser_version": market_module.PARSER_VERSION, "mapping_audit": audit_meta,
        "normalized_races_sha256": hashlib.sha256((tmp_path / "markets.parquet").read_bytes()).hexdigest(),
    }))
    loaded = market_module.load_race_markets(as_of="2015-01-02")
    assert loaded.empty  # A parser string alone cannot validate an incomplete store.
    audit_bytes = (tmp_path / "markets_mapping_audit.json").read_bytes()
    (tmp_path / "markets_mapping_audit.json").write_text("tampered")
    assert market_module.load_race_markets().empty
    (tmp_path / "markets_mapping_audit.json").write_bytes(audit_bytes)
    (tmp_path / "markets.parquet").write_bytes((tmp_path / "markets.parquet").read_bytes() + b"tampered")
    assert market_module.load_race_markets().empty


def test_mapping_audit_storage_records_disabled_event(tmp_path):
    event = "SYNTHETIC-FOUR"
    ticket = {"dem_name": "Avery Cedar", "dem_party": "I", "rep_name": "Blair Birch"}
    disabled = audit_race_event(
        race_id="synthetic-four", event_ticker=event,
        markets=[_contract(event, "D", "Generic label", 0.2),
                 _contract(event, "R", "Blair Birch", 0.6)], ticket=ticket,
    )
    meta = write_mapping_audit_store(pd.DataFrame(), [{"mapping_audit": disabled}],
                                     path=tmp_path / "audit.json")
    stored = json.loads((tmp_path / "audit.json").read_text())
    assert meta["n_disabled"] == 1
    assert stored["events"][0]["matched_modeled_contract_id"] is None
    assert stored["events"][0]["disable_reason"]


def test_stale_control_store_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(market_module, "NORMALIZED_DIR", tmp_path)
    monkeypatch.setattr(market_module, "MANIFESTS_DIR", tmp_path)
    content = '{"synthetic": "ok"}'
    (tmp_path / "markets_control.json").write_text(content)
    (tmp_path / "markets_kalshi.json").write_text(json.dumps({
        "parser_version": "old-parser",
        "normalized_control_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }))
    assert market_module.load_control_market() == {}
    (tmp_path / "markets_kalshi.json").write_text(json.dumps({
        "parser_version": market_module.PARSER_VERSION,
        "normalized_control_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }))
    assert market_module.load_control_market() == {}  # Full audit/byte lineage is required.
    (tmp_path / "markets_control.json").write_text("tampered")
    assert market_module.load_control_market() == {}


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


def test_ticket_identity_requires_independent_caucus_basis():
    ticket = {"dem_name": "Avery Cedar", "dem_party": "I", "rep_name": "Blair Birch",
              "modeled_caucus": None, "modeled_caucus_basis": None,
              "opposing_caucus": "caucus_b", "opposing_caucus_basis": "declared_assumption"}
    contest = identity_from_ticket("synthetic-contest", ticket)
    assert not contest.two_party_margin_eligible
    with pytest.raises(ValueError, match="unknown"):
        contest.caucus_for_candidate(contest.contenders[0].candidate_id)
    race = pd.DataFrame([{"race_id": "synthetic-contest", "not_up": False,
                          "modeled_ballot_party": "I",
                          "modeled_caucus": None, "modeled_caucus_basis": None,
                          "opposing_caucus": "caucus_b",
                          "opposing_caucus_basis": "declared_assumption"}])
    with pytest.raises(ValueError, match="explicit modeled caucus"):
        require_explicit_caucus(race, ["synthetic-contest"])
    race.loc[0, "modeled_caucus"] = "caucus_a"
    race.loc[0, "modeled_caucus_basis"] = "explicit_model_assumption"
    require_explicit_caucus(race, ["synthetic-contest"])
    race.loc[0, "modeled_caucus"] = "D"
    race.loc[0, "opposing_caucus"] = "R"
    require_binary_chamber_compatibility(race)
    race.loc[0, "modeled_caucus"] = "caucus_b"
    with pytest.raises(ValueError, match="supported explicit caucus mapping"):
        require_binary_chamber_compatibility(race)


def test_decomposition_only_checks_declared_exact_overlay_arithmetic():
    def term(name, value, kind):
        return DiagnosticTerm(name, value, kind, "synthetic")
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
    with_prior = ArtifactIdentity("run_x", "v1", "snap_x", "e" * 64, "f" * 64, "a" * 64)
    stale_prior = {**with_prior.__dict__, "prior_sha256": "b" * 64}
    assert not check_lineage(with_prior, {"artifact": stale_prior})["ok"]
