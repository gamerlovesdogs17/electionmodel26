"""Kalshi markets + expert ratings production layers."""

from __future__ import annotations

import pytest

from midterms.config import DEMO_AS_OF
from midterms.evidence.expert_ratings import load_expert_ratings, write_expert_ratings_store
from midterms.evidence.markets import load_race_markets, write_markets_store
from midterms.pipeline.run_forecast import run_forecast


def test_expert_ratings_store():
    man = write_expert_ratings_store(available_at="2026-09-01")
    assert man["n"] >= 20
    df = load_expert_ratings(as_of="2026-09-01", election_id="senate-2026")
    assert "TX" in set(df["state"])
    assert "rating" in df.columns


def test_kalshi_markets_store():
    try:
        man = write_markets_store("senate-2026", available_at=DEMO_AS_OF)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Kalshi unreachable: {exc}")
    if man.get("n_races", 0) < 5:
        pytest.skip(f"Insufficient Kalshi coverage: {man}")
    races = load_race_markets(as_of=DEMO_AS_OF)
    assert len(races) >= 5
    assert races["p_dem"].between(0.0, 1.0).all()


def test_forecast_with_real_overlays(tmp_path):
    result = run_forecast(
        method="fast",
        draws=300,
        seed=21,
        ensemble=True,
        with_ratings=True,
        with_markets=True,
        out_dir=tmp_path,
    )
    art = result["artifact"]
    assert art["model_version"].startswith("senate-hierarchical-v0.")
    ver = art["model_version"].rsplit("-", 1)[-1]
    assert ver.startswith("v0.") and float(ver[1:]) >= 0.4
    assert "ablation" in art
    assert "unadjusted" in art["ablation"]
    assert "adjusted" in art["ablation"]
    assert abs(art["chamber"]["p_dem_majority"] + art["chamber"]["p_rep_majority"] - 1.0) < 1e-6
    assert art["overlays"]["ratings"]["enabled"] in {True, False}
    assert art["overlays"]["markets"]["enabled"] in {True, False}
