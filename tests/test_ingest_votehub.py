"""VoteHub ingest, pollster ratings, and live-poll warehouse tests."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from midterms.config import NORMALIZED_DIR, RAW_DIR
from midterms.evidence.candidates import candidate_party, canonicalize_pollster
from midterms.evidence.ingest import merge_live_polls_into_warehouse, normalize_votehub_senate_polls
from midterms.evidence.ratings import build_rating_lookup, rating_for, write_normalized_ratings
from midterms.evidence.warehouse import Warehouse
from midterms.model.pymc_model import fit_fast_approximation
from midterms.simulate.chamber import simulate_chamber


def test_candidate_party_map_covers_major_names():
    assert candidate_party("James Talarico") == "D"
    assert candidate_party("Ken Paxton") == "R"
    assert candidate_party("Susan Collins") == "R"
    assert candidate_party("Graham Platner") == "D"
    assert candidate_party("Unknown Person XYZ") is None


def test_pollster_aliases_map_to_scorecards():
    assert canonicalize_pollster("Emerson College") == "Emerson"
    assert canonicalize_pollster("The New York Times/Siena College") == "Siena-NYT"
    assert canonicalize_pollster("Trafalgar") == "Trafalgar Group"


def test_votehub_scorecards_load():
    path = RAW_DIR / "external" / "votehub_pollster_scorecards.json"
    assert path.exists()
    write_normalized_ratings()
    lookup = build_rating_lookup()
    assert len(lookup) >= 40
    r = rating_for("Emerson College", lookup)
    assert r.grade == "B"
    assert r.house_effect_dem_pp != 0.0 or r.quality_weight < 1.0
    traf = rating_for("Trafalgar Group", lookup)
    assert traf.grade == "D"
    assert traf.house_effect_dem_pp < 0  # R-leaning house effect


def test_normalize_votehub_senate_polls():
    raw = RAW_DIR / "external" / "votehub_senate_polls.json"
    assert raw.exists()
    df = normalize_votehub_senate_polls()
    assert len(df) > 50
    assert set(["TX", "MI", "GA", "NC"]).issubset(set(df["state"]))
    assert (df["two_party_margin"].abs() < 80).all()
    assert df["poll_id"].str.startswith("vh-").all()
    assert "quality_weight" in df.columns
    # as-of filterable
    assert df["available_at"].notna().all()


def test_merge_live_polls_and_forecast_smoke(tmp_path):
    # Ensure fixtures exist, then merge live VoteHub polls for 2026
    from midterms.evidence.fixtures import build_fixtures

    build_fixtures()
    summary = merge_live_polls_into_warehouse(election_id="senate-2026")
    assert summary["n_live"] > 50
    wh = Warehouse(ensure_fixtures=False)
    snap = wh.build_as_of("2026-09-01", "senate-2026")
    assert len(snap.polls) > 0
    assert "quality_weight" in snap.polls.columns
    # Live polls should dominate 2026 after merge
    assert snap.polls["poll_id"].astype(str).str.startswith("vh-").mean() > 0.5
    fit = fit_fast_approximation(snap, n_draws=800, seed=42)
    assert fit.diagnostics["n_polls"] > 0
    sim, summaries = simulate_chamber(fit, snap.races)
    assert 0 <= sim.p_dem_majority <= 1
    assert len(summaries) >= 20


def test_unknown_candidates_manifest_written():
    normalize_votehub_senate_polls()
    meta = json.loads((Path("data/manifests/normalize_votehub_senate.json")).read_text())
    assert "skipped" in meta
    assert "attribution" in meta
