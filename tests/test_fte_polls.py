"""FTE historical Senate poll ingest (VoteHub has no archive endpoint)."""

from __future__ import annotations

from midterms.evidence.fte_polls import ingest_fte_historical_into_warehouse, normalize_fte_senate_polls


def test_fte_ingest_smoke():
    man = ingest_fte_historical_into_warehouse()
    assert man.get("ok") is True
    assert man.get("n_fte_polls", 0) > 50
    assert "senate-2018" in man.get("elections", []) or "senate-2020" in man.get("elections", [])
