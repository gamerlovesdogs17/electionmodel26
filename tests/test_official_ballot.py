"""Official ballot + chamber reconciliation (audit P0.1–P0.2)."""

from __future__ import annotations

from midterms.evidence.official_ballot import (
    CLASS_III,
    CYCLE_META,
    contested_contests,
    write_official_ballot_store,
)
from midterms.evidence.results_archive import write_results_archive
from midterms.validation.chamber_reconcile import reconcile_cycle, reconcile_all_cycles


def test_2022_is_class_iii_not_class_ii():
    contests = contested_contests(2022)
    states = {c["state"] for c in contests if c["kind"] == "regular"}
    assert states == set(CLASS_III)
    assert "PA" in states and "OH" in states and "AZ" in states
    assert "DE" not in states  # Class I/II, not III
    assert any(c["race_id"] == "senate-2022-OK-special" for c in contests)
    assert any(c["race_id"] == "senate-2022-CA-unexpired" for c in contests)
    assert len(contests) == 36


def test_reconcile_2022_after_official_store():
    write_results_archive()
    write_official_ballot_store()
    # Force warehouse to see official races
    from midterms.evidence.warehouse import Warehouse

    wh = Warehouse(ensure_fixtures=False)
    from midterms.evidence.official_ballot import merge_official_into_races

    races = merge_official_into_races(wh.races)
    rep = reconcile_cycle(2022, races=races, results=wh.results)
    assert rep["ok"] is True, rep.get("reasons")
    assert rep["realized_dem_seats"] == 51
    assert rep["dem_control"] is True
    assert rep["n_contested_expected"] == 36


def test_reconcile_core_cycles():
    write_results_archive()
    write_official_ballot_store()
    summary = reconcile_all_cycles(years=(2018, 2020, 2022, 2024))
    assert summary["ok"] is True, summary.get("failures")
    assert "2022" in summary["by_cycle"]
