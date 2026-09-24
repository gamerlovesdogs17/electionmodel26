"""Leakage canary and as-of correctness."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from midterms.evidence.warehouse import Warehouse


@pytest.fixture(scope="module")
def warehouse():
    # Use the production warehouse as-is. Never call build_fixtures() here —
    # that clobbers VoteHub/FTE live polls with synthetic 2026 rows.
    return Warehouse(ensure_fixtures=False)


def test_as_of_rejects_future_polls(warehouse: Warehouse):
    as_of = date(2022, 9, 1)
    snap = warehouse.build_as_of(as_of, "senate-2022")
    assert len(snap.polls) > 0
    avail = pd.to_datetime(snap.polls["available_at"]).dt.date
    assert (avail <= as_of).all()


def test_leakage_canary_injection(warehouse: Warehouse):
    as_of = date(2022, 9, 1)
    tainted = warehouse.inject_future_poll_for_canary("senate-2022", as_of)
    future = tainted[tainted["poll_id"] == "CANARY-FUTURE-POLL"].iloc[0]
    assert date.fromisoformat(str(future["available_at"])) > as_of

    # Manual as-of filter must drop the canary
    avail = pd.to_datetime(tainted["available_at"]).dt.date
    filtered = tainted[avail <= as_of]
    assert "CANARY-FUTURE-POLL" not in set(filtered["poll_id"])


def test_results_not_used_before_certification(warehouse: Warehouse):
    as_of = date(2022, 11, 8)  # election week — before synthetic certification lag
    snap = warehouse.build_as_of(as_of, "senate-2022")
    # Certified results available_at is ED+21 in fixtures
    assert len(snap.results_known) == 0 or (
        pd.to_datetime(snap.results_known["available_at"]).dt.date <= as_of
    ).all()


def test_snapshot_id_stable(warehouse: Warehouse):
    a = warehouse.build_as_of("2022-09-01", "senate-2022")
    b = warehouse.build_as_of("2022-09-01", "senate-2022")
    assert a.snapshot_id == b.snapshot_id


def test_future_unavailable_poll_does_not_change_snapshot_id(warehouse: Warehouse):
    as_of = date(2022, 9, 1)
    baseline = warehouse.build_as_of(as_of, "senate-2022")
    original = warehouse.polls
    try:
        warehouse.polls = warehouse.inject_future_poll_for_canary("senate-2022", as_of)
        mutated = warehouse.build_as_of(as_of, "senate-2022")
    finally:
        warehouse.polls = original
    assert mutated.snapshot_id == baseline.snapshot_id
