"""Personal incumbency and candidate-specific finance integrity tests."""

from __future__ import annotations

import pandas as pd
import pytest

from midterms.evidence.fec import fec_name_matches, select_unique_party_candidate
from midterms.model.fundamentals import feature_row
from midterms.model.personal_incumbency import personal_incumbency_signed


def _row(**kwargs):
    base = {
        "prior_lean": 0.0,
        "fundraising_share": 0.5,
        "is_open": False,
        "incumbent_party": "R",
        "held_by": "R",
        "white_house_party": "R",
        "pres_approval": 0.0,
        "is_midterm": True,
    }
    base.update(kwargs)
    return pd.Series(base)


def test_incumbent_democrat_renominated_gets_plus_incumbency():
    row = _row(
        incumbent_party="D",
        held_by="D",
        modeled_candidate_is_incumbent=True,
        opposing_candidate_is_incumbent=False,
    )
    assert personal_incumbency_signed(row) == 1.0
    assert feature_row(row)["incumbency"] == 1.0


def test_incumbent_republican_renominated_gets_minus_incumbency():
    row = _row(
        modeled_candidate_is_incumbent=False,
        opposing_candidate_is_incumbent=True,
    )
    assert personal_incumbency_signed(row) == -1.0
    assert feature_row(row)["incumbency"] == -1.0


def test_incumbent_defeated_in_primary_gets_zero():
    # Seat still "held" by R party metadata, but nominee is not the incumbent.
    row = _row(
        is_open=False,
        incumbent_party="R",
        held_by="R",
        modeled_candidate_is_incumbent=False,
        opposing_candidate_is_incumbent=False,
        sitting_senator_name="John Cornyn",
    )
    assert personal_incumbency_signed(row) == 0.0
    assert feature_row(row)["incumbency"] == 0.0


def test_open_seat_gets_zero_incumbency():
    row = _row(
        is_open=True,
        incumbent_party=None,
        held_by="R",
        modeled_candidate_is_incumbent=False,
        opposing_candidate_is_incumbent=False,
    )
    assert feature_row(row)["incumbency"] == 0.0


def test_same_party_retains_seat_but_candidate_changes_zero():
    row = _row(
        is_open=False,
        incumbent_party="R",
        held_by="R",
        modeled_candidate_is_incumbent=False,
        opposing_candidate_is_incumbent=False,
        sitting_senator_name="Bill Cassidy",
        opposing_candidate_name="Julia Letlow",
    )
    assert feature_row(row)["incumbency"] == 0.0


def test_tx_2026_paxton_receives_no_cornyn_incumbency_bonus():
    from midterms.evidence.current_candidates import load_current_candidate_registry

    registry = load_current_candidate_registry()
    tx = next(r for r in registry["races"] if r["state"] == "TX")
    assert tx["opposing_candidate_name"] == "Ken Paxton"
    assert tx["sitting_senator_name"] == "John Cornyn"
    assert tx["modeled_candidate_is_incumbent"] is False
    assert tx["opposing_candidate_is_incumbent"] is False
    row = _row(
        race_id="senate-2026-TX",
        state="TX",
        is_open=False,
        incumbent_party="R",
        held_by="R",
        **{
            k: tx[k]
            for k in (
                "modeled_candidate_is_incumbent",
                "opposing_candidate_is_incumbent",
                "sitting_senator_name",
                "modeled_candidate_name",
                "opposing_candidate_name",
            )
        },
    )
    # Legacy party encoding would have been -1; personal encoding is 0.
    assert feature_row(row)["incumbency"] == 0.0


def test_historical_without_personal_flags_fails_closed_to_zero():
    row = _row(is_open=False, incumbent_party="D", held_by="D")
    # Explicitly omit personal flags.
    assert "modeled_candidate_is_incumbent" not in row.index
    assert personal_incumbency_signed(row) == 0.0
    assert feature_row(row)["incumbency"] == 0.0


def test_fec_name_match_order_insensitive():
    assert fec_name_matches("PAXTON, KEN", "Ken Paxton")
    assert fec_name_matches("CORNYN, JOHN", "John Cornyn")
    assert not fec_name_matches("CORNYN, JOHN", "Ken Paxton")


def test_select_unique_party_candidate_refuses_max_receipt_surrogate():
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "S0TX0001",
                "candidate_name": "CORNYN, JOHN",
                "party": "REP",
                "state": "TX",
                "receipts": 10_000_000,
            },
            {
                "candidate_id": "S0TX0002",
                "candidate_name": "PAXTON, KEN",
                "party": "REP",
                "state": "TX",
                "receipts": 1_000_000,
            },
        ]
    )
    matched = select_unique_party_candidate(
        frame, party="REP", ticket_name="Ken Paxton"
    )
    assert matched is not None
    assert matched["candidate_id"] == "S0TX0002"
    assert select_unique_party_candidate(frame, party="REP", ticket_name=None) is None
