"""Point-in-time integrity for pollster ratings (historical as-of leakage)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from midterms.evidence.ratings import (
    DEFAULT_QUALITY,
    PollsterRating,
    RATINGS_SNAPSHOT_FLOOR,
    build_rating_lookup,
    build_rating_lookup_with_meta,
    rating_for,
)


def _vintaged_rows() -> list[dict]:
    from midterms.evidence.candidates import canonicalize_pollster, normalize_candidate_key

    emerson_key = normalize_candidate_key(canonicalize_pollster("Emerson College"))
    future_key = normalize_candidate_key(canonicalize_pollster("Future Pollster 2026"))
    return [
        {
            "pollster": "Emerson College",
            "pollster_key": emerson_key,
            "grade": "A",
            "quality_weight": 0.88,
            "house_effect_dem_pp": 0.1,
            "percent_error": 1.5,
            "relative_error": None,
            "herding_error_pct": None,
            "within_moe_pct": None,
            "available_at": "2018-06-01",
            "source": "vintaged_pollster_ratings",
        },
        {
            "pollster": "Future Pollster 2026",
            "pollster_key": future_key,
            "grade": "A+",
            "quality_weight": 1.0,
            "house_effect_dem_pp": 0.0,
            "percent_error": 0.5,
            "relative_error": None,
            "herding_error_pct": None,
            "within_moe_pct": None,
            "available_at": "2026-01-15",
            "source": "vintaged_pollster_ratings",
        },
    ]


def test_2018_as_of_excludes_post_cutoff_ratings(tmp_path: Path, monkeypatch):
    """A 2018 as-of run cannot consume a ratings artifact dated after the cutoff."""
    vintage = tmp_path / "pollster_ratings_vintages.json"
    vintage.write_text(json.dumps({"rows": _vintaged_rows()}), encoding="utf-8")
    monkeypatch.setattr(
        "midterms.evidence.ratings._vintaged_ratings_path", lambda: vintage
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_votehub_scorecards", lambda path=None: pd.DataFrame()
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_fte_ratings", lambda path=None: pd.DataFrame()
    )

    lookup, meta = build_rating_lookup_with_meta(as_of="2018-09-01")
    from midterms.evidence.candidates import canonicalize_pollster, normalize_candidate_key

    emerson_key = normalize_candidate_key(canonicalize_pollster("Emerson College"))
    future_key = normalize_candidate_key(canonicalize_pollster("Future Pollster 2026"))
    assert emerson_key in lookup
    assert future_key not in lookup
    assert meta["n_skipped_future"] >= 1
    assert lookup[emerson_key].available_at == "2018-06-01"
    assert date.fromisoformat(lookup[emerson_key].available_at) <= date(2018, 9, 1)


def test_empty_historical_lookup_does_not_invoke_current(monkeypatch):
    """Empty filtered lookup must not rebuild present-day ratings via rating_for."""
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_votehub_scorecards", lambda path=None: pd.DataFrame()
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_fte_ratings", lambda path=None: pd.DataFrame()
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_vintaged_ratings", lambda path=None: pd.DataFrame()
    )

    empty = build_rating_lookup(as_of="2018-09-01")
    assert empty == {}

    calls = {"n": 0}

    def _boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("present-day build_rating_lookup must not be invoked")

    with patch("midterms.evidence.ratings.build_rating_lookup", side_effect=_boom):
        r = rating_for("Emerson College", lookup={})
    assert calls["n"] == 0
    assert r.source == "prior_default"
    assert r.quality_weight == DEFAULT_QUALITY


def test_valid_pre_cutoff_historical_rating_used(tmp_path: Path, monkeypatch):
    vintage = tmp_path / "pollster_ratings_vintages.json"
    vintage.write_text(json.dumps({"rows": _vintaged_rows()}), encoding="utf-8")
    monkeypatch.setattr(
        "midterms.evidence.ratings._vintaged_ratings_path", lambda: vintage
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_votehub_scorecards", lambda path=None: pd.DataFrame()
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_fte_ratings", lambda path=None: pd.DataFrame()
    )

    lookup = build_rating_lookup(as_of="2018-09-01")
    r = rating_for("Emerson College", lookup=lookup)
    assert r.source == "vintaged_pollster_ratings"
    assert r.grade == "A"
    assert r.quality_weight == pytest.approx(0.88)


def test_missing_historical_ratings_yield_neutral(monkeypatch):
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_votehub_scorecards", lambda path=None: pd.DataFrame()
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_fte_ratings", lambda path=None: pd.DataFrame()
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_vintaged_ratings", lambda path=None: pd.DataFrame()
    )
    lookup = build_rating_lookup(as_of="2018-09-01")
    r = rating_for("Unknown Pollster XYZ", lookup=lookup)
    assert r.source == "prior_default"
    assert r.grade is None
    assert r.house_effect_dem_pp == 0.0


def test_live_run_uses_current_ratings(monkeypatch):
    """Current/live runs (as_of=None) still ingest living scorecards."""
    from midterms.evidence.candidates import canonicalize_pollster, normalize_candidate_key

    key = normalize_candidate_key(canonicalize_pollster("Live Pollster"))
    living = pd.DataFrame(
        [
            {
                "pollster": "Live Pollster",
                "pollster_key": key,
                "grade": "B",
                "quality_weight": 0.72,
                "house_effect_dem_pp": -0.2,
                "percent_error": 2.0,
                "relative_error": None,
                "herding_error_pct": None,
                "within_moe_pct": None,
                "available_at": "2026-03-01",
                "source": "votehub_pollster_scorecards",
                "provenance": "living_scorecard_mtime",
            }
        ]
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_votehub_scorecards", lambda path=None: living
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_fte_ratings", lambda path=None: pd.DataFrame()
    )
    monkeypatch.setattr(
        "midterms.evidence.ratings.load_vintaged_ratings", lambda path=None: pd.DataFrame()
    )
    lookup = build_rating_lookup(as_of=None)
    assert key in lookup
    r = rating_for("Live Pollster", lookup=lookup)
    assert r.grade == "B"
    assert r.source == "votehub_pollster_scorecards"

    # Same living stamp must be excluded from a pre-floor historical as-of.
    hist, meta = build_rating_lookup_with_meta(as_of="2018-09-01")
    assert key not in hist
    assert meta["n_skipped_living_unvintaged"] >= 1 or meta["n_skipped_future"] >= 1
    assert date(2018, 9, 1) < RATINGS_SNAPSHOT_FLOOR


def test_falsy_empty_dict_is_not_none():
    """Regression: ``lookup or build_rating_lookup()`` treated {} as missing."""
    r = rating_for("Anyone", lookup={})
    assert isinstance(r, PollsterRating)
    assert r.source == "prior_default"
