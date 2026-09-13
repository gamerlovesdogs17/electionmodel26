"""v0.8 Senate blueprint completion coverage tests."""

from __future__ import annotations

from datetime import date

import numpy as np

from midterms.evidence.approval import approval_as_of, write_approval_store
from midterms.evidence.historical_polls import freeze_historical_polls_from_warehouse
from midterms.model.state_space import fit_state_space
from midterms.model.scenarios import run_scenarios
from midterms.ops.signing import sign_payload, verify_signature
from midterms.simulate.institutional import georgia_needs_runoff, louisiana_needs_runoff
from midterms.validation.metrics import energy_score, interval_score_gaussian, reliability_bins


def test_approval_vintage_as_of():
    write_approval_store()
    a = approval_as_of("2026-09-01", election_year=2026)
    assert a["white_house_party"] in {"D", "R"}
    assert isinstance(a["net_approval"], float)


def test_signing_roundtrip():
    payload = {"run_id": "x", "v": 1}
    sig = sign_payload(payload)
    assert verify_signature(payload, sig["signature"], alg=sig.get("alg"))
    assert not verify_signature(payload, "0" * 64, alg="HMAC-SHA256")


def test_runoff_rules():
    assert louisiana_needs_runoff(48.0, 47.0, 5.0)
    assert not georgia_needs_runoff(52.0, 45.0, 3.0)


def test_metrics_helpers():
    assert interval_score_gaussian(1.0, 0.0, 2.0) > 0
    bins = reliability_bins(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1]), n_bins=5)
    assert isinstance(bins, list)
    es = energy_score(np.random.default_rng(0).normal(size=(50, 3)), np.zeros(3))
    assert np.isfinite(es)


def test_state_space_and_scenarios_smoke():
    from midterms.evidence.warehouse import Warehouse

    wh = Warehouse(ensure_fixtures=False)
    snap = wh.build_as_of(date(2026, 9, 1), "senate-2026")
    fit = fit_state_space(snap, n_draws=80, seed=1, generic_ballot=0.0)
    assert len(fit.race_ids) >= 30
    assert fit.draws_margin.shape[0] == 80
    scen = run_scenarios(fit, snap.races)
    assert "baseline" in scen
    assert "national_dem_miss_m3" in scen


def test_freeze_historical_polls():
    man = freeze_historical_polls_from_warehouse()
    assert man["n"] > 100
    assert "sha256" in man
