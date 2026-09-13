"""VP tiebreak, economics store, FEC shares, and overlay plumbing."""

from __future__ import annotations

import numpy as np

from midterms.evidence.economics import write_economic_store, yoy_growth_as_of
from midterms.evidence.fec import fixture_fundraising_shares, write_finance_store
from midterms.evidence.warehouse import Warehouse
from midterms.model.overlays import apply_rating_overlay, rating_from_probability
from midterms.model.pymc_model import FitResult, fit_fast_approximation
from midterms.simulate.chamber import simulate_chamber


def test_vp_tiebreak_counts_fifty_as_rep_control():
    wh = Warehouse()
    snap = wh.build_as_of("2026-09-01", "senate-2026")
    fit = fit_fast_approximation(snap, n_draws=800, seed=5)
    # Force a mass of draws near zero so some land at exactly 50 seats after rounding wins
    sim, _ = simulate_chamber(fit, snap.races, vp_tiebreak_party="R")
    assert abs(sim.p_dem_majority + sim.p_rep_majority - 1.0) < 1e-9
    # 50–50 is inside Rep control, not a third bucket
    assert sim.p_fifty_fifty >= 0
    assert sim.p_rep_majority + 1e-12 >= sim.p_fifty_fifty


def test_forced_fifty_fifty_is_rep_control():
    """Construct draws that yield exactly 50 Dem seats and confirm Rep control."""
    wh = Warehouse()
    snap = wh.build_as_of("2026-09-01", "senate-2026")
    contested = snap.races[~snap.races["not_up"]]
    n = len(contested)
    held_dem = int((snap.races[snap.races["not_up"]]["held_by"] == "D").sum()) + int(
        (snap.races[snap.races["not_up"]]["held_by"] == "I").sum()
    )
    need = 50 - held_dem
    means = np.full(n, -5.0)
    # Flip exactly `need` races to Dem
    means[: max(need, 0)] = 5.0
    draws = np.tile(means, (200, 1))
    fit = FitResult(
        race_ids=contested["race_id"].tolist(),
        states=contested["state"].tolist(),
        mean_margin=means,
        sd_margin=np.ones(n),
        draws_margin=draws,
        house_effects={},
        diagnostics={},
        method="test",
    )
    sim, summaries = simulate_chamber(fit, snap.races, vp_tiebreak_party="R")
    assert sim.p_fifty_fifty == 1.0
    assert sim.p_rep_majority == 1.0
    assert sim.p_dem_majority == 0.0
    assert summaries[0]["rating"] in {
        "Solid D",
        "Likely D",
        "Lean D",
        "Tossup",
        "Lean R",
        "Likely R",
        "Solid R",
    }


def test_economics_fixture_yoy():
    write_economic_store()
    yoy = yoy_growth_as_of("2026-09-01", election_year=2026)
    assert yoy is not None
    assert -10 < float(yoy) < 10


def test_alfred_multi_vintage_no_revision_leak():
    """Post-election revisions must not appear in pre-election as-of queries."""
    from midterms.evidence.economics import build_fixture_vintages, write_economic_store, yoy_growth_as_of

    write_economic_store(build_fixture_vintages())
    pre = yoy_growth_as_of("2022-09-01", election_year=2022)
    post = yoy_growth_as_of("2023-01-15", election_year=2022)
    assert pre is not None and post is not None
    # Fixture revises after ED; as-of before ED must not equal the revised value
    assert float(pre) != float(post)


def test_fec_fixture_shares():
    df = fixture_fundraising_shares()
    assert len(df) >= 30
    assert df["fundraising_share"].between(0.05, 0.95).all()
    summary = write_finance_store()
    assert summary["n_shares"] >= 1


def test_rating_overlay_moves_means():
    means = np.array([0.0, 0.0, 0.0])
    import pandas as pd

    ratings = pd.DataFrame(
        {
            "race_id": ["a", "b", "c"],
            "rating": ["Solid D", "Solid R", "Tossup"],
        }
    )
    out = apply_rating_overlay(means, ["a", "b", "c"], ratings, weight=0.5)
    assert out[0] > 0
    assert out[1] < 0
    assert rating_from_probability(0.96) == "Solid D"
    assert rating_from_probability(0.58) == "Tilt D"
    assert rating_from_probability(0.42) == "Tilt R"
