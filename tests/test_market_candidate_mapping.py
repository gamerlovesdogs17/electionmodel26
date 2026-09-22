"""Candidate identity, multi-contract normalization, and ambiguity safeguards."""

from __future__ import annotations

import pandas as pd

from midterms.evidence.markets import map_race_event


def _contract(event: str, suffix: str, title: str, price: float):
    return {
        "ticker": f"{event}-{suffix}", "event_ticker": event,
        "title": title, "last_price_dollars": price,
        "outcome_type": "candidate_win", "mutually_exclusive": True,
        "event_exhaustive": True, "contract_scope": "candidate",
        "candidate_id": suffix,
    }


def test_ordinary_two_candidate_event():
    event = "SENATEXX-26"
    row, error = map_race_event(
        race_id="senate-2026-XX", event_ticker=event,
        markets=[_contract(event, "D", "Alex Demo", 0.4), _contract(event, "R", "Robin Example", 0.6)],
        ticket={"dem_name": "Alex Demo", "dem_party": "D", "rep_name": "Robin Example"},
    )
    assert error is None
    assert row["modeled_candidate_ticker"] == f"{event}-D"
    assert row["normalization_contracts"] == 2


def test_named_independent_contract_over_literal_party_contract():
    event = "SENATENE-26"
    row, error = map_race_event(
        race_id="senate-2026-NE", event_ticker=event,
        markets=[
            _contract(event, "DOSB", "Dan Osborn", 0.4),
            _contract(event, "R", "Republican candidate", 0.5),
            _contract(event, "D", "Democratic candidate", 0.1),
        ],
        ticket={"dem_name": "Dan Osborn", "dem_party": "I", "rep_name": "Pete Ricketts"},
    )
    assert error is None
    assert row["modeled_candidate_ticker"] == f"{event}-DOSB"
    assert row["modeled_candidate_party"] == "I"
    assert row["normalization_contracts"] == 3


def test_ambiguous_independent_event_is_disabled():
    event = "SENATEXX-26"
    row, error = map_race_event(
        race_id="senate-2026-XX", event_ticker=event,
        markets=[_contract(event, "IND", "Independent", 0.4), _contract(event, "R", "Republican", 0.6)],
        ticket={"dem_name": "Alex Example", "dem_party": "I", "rep_name": "Robin Example"},
    )
    assert row is None
    assert "absent or ambiguous" in error


def test_multi_contract_event_uses_all_priced_mass():
    event = "SENATEXX-26"
    row, error = map_race_event(
        race_id="senate-2026-XX", event_ticker=event,
        markets=[_contract(event, "D", "Alex Demo", 0.3), _contract(event, "R", "Robin Example", 0.5), _contract(event, "IND", "Other contender", 0.2)],
        ticket={"dem_name": "Alex Demo", "dem_party": "D", "rep_name": "Robin Example"},
    )
    assert error is None
    assert row["normalization_contracts"] == 3
    assert abs(sum(c["p_normalized"] for c in row["candidate_contracts"]) - 1.0) < 1e-12


def test_legacy_parser_store_is_not_reused(tmp_path, monkeypatch):
    from midterms.evidence import markets as mod

    pd.DataFrame([{"race_id": "example", "parser_version": "kalshi-v1", "p_dem": 0.5}]).to_parquet(tmp_path / "markets.parquet")
    monkeypatch.setattr(mod, "NORMALIZED_DIR", tmp_path)
    assert mod.load_race_markets().empty
