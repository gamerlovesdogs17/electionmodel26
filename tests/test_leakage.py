"""Leakage canary and as-of correctness."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from midterms.evidence.fixtures import build_fixtures
from midterms.evidence.warehouse import Warehouse


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    build_fixtures()
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
