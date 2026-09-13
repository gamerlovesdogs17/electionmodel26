"""Official ballot 2014/2016 + G10 independent rebuild helpers."""

from __future__ import annotations

from midterms.evidence.official_ballot import CLASS_II, contested_contests
from midterms.evidence.results_archive import CERTIFIED_MARGINS
from midterms.ops.reproducibility import compare_forecast_artifacts
from midterms.validation.chamber_reconcile import GATE_YEARS, reconcile_cycle


def test_2014_includes_class_iii_specials():
    contests = contested_contests(2014)
    assert len(contests) == 36
    regs = {c["state"] for c in contests if c["kind"] == "regular"}
    assert regs == set(CLASS_II)
    specials = {c["race_id"] for c in contests if c["kind"] == "special"}
    assert specials == {
        "senate-2014-HI-special",
        "senate-2014-OK-special",
        "senate-2014-SC-special",
    }
    margins = CERTIFIED_MARGINS["senate-2014"]
    for c in contests:
        key = c["race_id"].replace("senate-2014-", "")
        assert key in margins


def test_2016_certified_margins_cover_class_iii():
    contests = contested_contests(2016)
    assert len(contests) == 34
    margins = CERTIFIED_MARGINS["senate-2016"]
    assert "AK" in margins and "KS" in margins
    assert margins["CA"] < 50  # not jungle +100 artifact
    assert margins["AZ"] > -40  # not MEDSL bad cell


def test_gate_years_include_2014_2016():
    assert GATE_YEARS[0] == 2014
    assert 2016 in GATE_YEARS


def test_reconcile_2014_and_2016():
    from midterms.evidence.official_ballot import merge_official_into_races, write_official_ballot_store
    from midterms.evidence.results_archive import merge_certified_into_results, write_results_archive
    from midterms.evidence.warehouse import Warehouse

    write_results_archive()
    write_official_ballot_store()
    wh = Warehouse(ensure_fixtures=False)
    races = merge_official_into_races(wh.races)
    results = merge_certified_into_results(wh.results)
    r14 = reconcile_cycle(2014, races=races, results=results)
    r16 = reconcile_cycle(2016, races=races, results=results)
    assert r14["ok"] is True, r14
    assert r14["realized_dem_seats"] == 46
    assert r16["ok"] is True, r16
    assert r16["realized_dem_seats"] == 48


def test_compare_forecast_artifacts_tolerance():
    sealed = {
        "chamber": {"p_dem_majority": 0.50, "expected_dem_seats": 50.0},
        "races": [{"race_id": "a", "p_dem": 0.40}, {"race_id": "b", "p_dem": 0.60}],
    }
    close = {
        "chamber": {"p_dem_majority": 0.505, "expected_dem_seats": 50.05},
        "races": [{"race_id": "a", "p_dem": 0.41}, {"race_id": "b", "p_dem": 0.59}],
    }
    far = {
        "chamber": {"p_dem_majority": 0.60, "expected_dem_seats": 52.0},
        "races": [{"race_id": "a", "p_dem": 0.40}, {"race_id": "b", "p_dem": 0.60}],
    }
    assert compare_forecast_artifacts(sealed, close)["ok"] is True
    assert compare_forecast_artifacts(sealed, far)["ok"] is False
