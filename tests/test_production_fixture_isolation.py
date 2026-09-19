"""Production/release paths must not silently ingest fixture/synthetic universes."""

from __future__ import annotations

import importlib
from pathlib import Path

from midterms.evidence.eligibility import PUBLICATION_BLOCKED, audit_evidence, classify_poll_row
from midterms.evidence.warehouse import Warehouse


def _run_forecast_source() -> str:
    mod = importlib.import_module("midterms.pipeline.run_forecast")
    return Path(mod.__file__).read_text(encoding="utf-8")


def test_synthetic_poll_rows_classified_blocked():
    assert classify_poll_row({"source_url": "synthetic://fixtures/polls-2026"}) == "synthetic"
    assert "synthetic" in PUBLICATION_BLOCKED


def test_2026_fixture_poll_universe_blocks_publication():
    """senate-2026 fixture polls must keep the run non-publication when present."""
    rep = audit_evidence(election_id="senate-2026", as_of="2026-09-13")
    polls = rep["domains"]["polls"]
    # If the warehouse still carries the synthetic 2026 fixture universe, gate must block.
    if polls.get("n", 0) and polls.get("blocked_n", 0) == polls.get("n"):
        assert rep["publishable"] is False
        assert rep["run_class"] == "non_publication"
        assert any("polls include blocked" in r for r in rep["reasons"])
    # Demographics / curated domains must not be silently treated as publication-eligible.
    demo = rep["domains"].get("demographics") or {}
    if demo.get("tier") == "curated":
        assert demo.get("eligible") is False


def test_warehouse_production_path_disables_fixture_autobuild():
    """run_forecast constructs Warehouse(ensure_fixtures=False)."""
    assert "Warehouse(ensure_fixtures=False)" in _run_forecast_source()
    wh = Warehouse(ensure_fixtures=False)
    assert wh.polls is not None


def test_historical_election_skips_living_expert_ratings_refresh():
    src = _run_forecast_source()
    assert "historical election — refuse living ratings refresh" in src
    assert 'endswith("-2026")' in src
