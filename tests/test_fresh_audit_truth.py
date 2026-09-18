"""Fresh audit R-01…R-06 / R-12 canaries."""

from __future__ import annotations

import pytest

from midterms.config import PUBLIC_LIVE_ENABLED
from midterms.evidence.eligibility import assert_publishable, audit_evidence
from midterms.evidence.official_ledger import LEDGER_PATH, load_expectations, load_ledger
from midterms.ops.public_publish import assert_public_ready
from midterms.validation.chamber_reconcile import reconcile_cycle


def test_ledger_and_expectations_are_separate_files():
    assert LEDGER_PATH.exists()
    ledger = load_ledger()
    exp = load_expectations()
    assert "cycles" in ledger and "cycles" in exp
    # Independence: expectations list race IDs; ledger has vote counts
    for year in ("2018", "2020", "2024"):
        assert year in ledger["cycles"] and year in exp["cycles"]
        contests = ledger["cycles"][year]["contests"]
        assert all(c["dem_votes"] > 0 or c["rep_votes"] > 0 for c in contests)
        assert exp["cycles"][year]["n_contested_expected"] == len(contests)


def test_specials_present_2018_2020_2024():
    ledger = load_ledger()
    ids = {c["race_id"] for c in ledger["cycles"]["2018"]["contests"]}
    assert "senate-2018-MN-special" in ids and "senate-2018-MS-special" in ids
    assert len(ids) == 35
    ids20 = {c["race_id"] for c in ledger["cycles"]["2020"]["contests"]}
    assert "senate-2020-AZ-special" in ids20 and "senate-2020-GA-special" in ids20
    ids24 = {c["race_id"] for c in ledger["cycles"]["2024"]["contests"]}
    assert "senate-2024-CA-unexpired" in ids24 and "senate-2024-NE-unexpired" in ids24


def test_canaries_oh_2018_az_2024():
    r18 = reconcile_cycle(2018)
    assert r18["ok"] is True, r18.get("reasons")
    r24 = reconcile_cycle(2024)
    assert r24["ok"] is True, r24.get("reasons")
    # Exact canary vote counts
    from midterms.evidence.warehouse import Warehouse

    wh = Warehouse(ensure_fixtures=False)
    oh = wh.results[wh.results["race_id"] == "senate-2018-OH"].iloc[0]
    assert int(oh["dem_votes"]) == 2_358_508
    assert int(oh["rep_votes"]) == 2_057_559
    assert float(oh["two_party_margin"]) > 6.5
    az = wh.results[wh.results["race_id"] == "senate-2024-AZ"].iloc[0]
    assert int(az["dem_votes"]) == 1_676_335
    assert float(az["two_party_margin"]) > 2.0


def test_changing_winner_breaks_reconcile():
    """Acceptance R-02: held seats are not re-solved when a winner flips."""
    from midterms.evidence.warehouse import Warehouse

    wh = Warehouse(ensure_fixtures=False)
    results = wh.results.copy()
    mask = results["race_id"] == "senate-2018-OH"
    assert mask.any()
    results.loc[mask, "dem_votes"] = 1_000_000
    results.loc[mask, "rep_votes"] = 2_000_000
    results.loc[mask, "two_party_margin"] = -33.3
    results.loc[mask, "winner_party"] = "R"
    if "winner_caucus" in results.columns:
        results.loc[mask, "winner_caucus"] = "R"
    bad = reconcile_cycle(2018, races=wh.races, results=results)
    assert bad["ok"] is False
    assert any("canary" in r or "post-election" in r for r in bad.get("reasons") or [])


def test_2026_fixture_domains_block_publication():
    """Synthetic fixture_hash finance must block; curated/FRED paths may be eligible."""
    from midterms.evidence.eligibility import _classify_manifest_domain

    blocked = _classify_manifest_domain(
        name="finance",
        manifest={"source_mix": {"fixture_hash": 35}, "n_shares": 35},
    )
    assert blocked["eligible"] is False
    assert blocked["tier"] == "synthetic"

    rep = audit_evidence(election_id="senate-2026", as_of="2026-09-13")
    assert "finance" in rep["domains"] and "economics" in rep["domains"]
    if rep["domains"]["finance"].get("source_mix", {}).get("fixture_hash"):
        assert rep["publishable"] is False


def test_require_publishable_raises_on_fixture_2026():
    rep = audit_evidence(election_id="senate-2026", as_of="2026-09-13")
    if not rep["publishable"]:
        with pytest.raises(
            ValueError, match="publication-eligible|ineligible|synthetic|blocked|finance|economics"
        ):
            assert_publishable("senate-2026", as_of="2026-09-13", allow_non_publication=False)
    else:
        out = assert_publishable("senate-2026", as_of="2026-09-13", allow_non_publication=False)
        assert out["publishable"] is True


def test_public_live_contained():
    assert PUBLIC_LIVE_ENABLED is False
    ready = assert_public_ready()
    assert ready["ok"] is False
    assert any("PUBLIC_LIVE_ENABLED" in r for r in ready["reasons"])
