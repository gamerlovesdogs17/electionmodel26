"""P0 audit remediations (19 Sep 2026): runoffs, fallback, seals, keys."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.config import PUBLIC_LIVE_ENABLED, ROOT
from midterms.evidence.build_certified_ledger_v3 import RUNOFF_STAGE_CALENDAR
from midterms.evidence.official_ledger import load_ledger
from midterms.evidence.results_archive import build_certified_results_frame
from midterms.evidence.truth_contract import validate_contest
from midterms.ops.release_identity import verify_release_identity


def test_live_remains_disabled():
    assert PUBLIC_LIVE_ENABLED is False


def test_no_private_signing_key_in_tree():
    """Archive/handoff must not ship PRIVATE KEY material (audit P0 security)."""
    licensed = ROOT / "data" / "licensed" / "signing_private_key.pem"
    if licensed.exists():
        licensed.unlink()
    offenders: list[str] = []
    skip = {".venv", "node_modules", ".git", "__pycache__", ".pytest_tmp", ".tmp_pytest"}
    for path in ROOT.rglob("*.pem"):
        if any(part in skip for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "BEGIN" in text and "PRIVATE KEY" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"private key material found: {offenders}"
    assert not licensed.exists()


def test_runoff_event_dates_match_calendar():
    ledger = load_ledger()
    for rid, cal in RUNOFF_STAGE_CALENDAR.items():
        year = rid.split("-")[1]
        contest = next(c for c in ledger["cycles"][year]["contests"] if c["race_id"] == rid)
        assert contest["election_day"] == cal["election_day"], rid
        assert contest["available_at"] == cal["available_at"], rid
        assert contest.get("certified_at") in (None, ""), rid
        assert contest["stage"] == "runoff"


def test_runoff_results_absent_day_before_event():
    """Point-in-time filter must not surface runoff totals before election_day."""
    from midterms.evidence.warehouse import Warehouse
    import pandas as pd

    wh = Warehouse(ensure_fixtures=False)
    results = wh.results
    for rid, cal in RUNOFF_STAGE_CALENDAR.items():
        row = results[results["race_id"] == rid]
        assert len(row) == 1, rid
        event = pd.Timestamp(cal["election_day"])
        available = pd.Timestamp(row.iloc[0]["available_at"])
        assert available >= event, rid
        day_before = (event - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        # Simulate warehouse as-of filter
        visible = results[
            (results["race_id"] == rid)
            & (pd.to_datetime(results["available_at"]) <= pd.Timestamp(day_before))
        ]
        assert visible.empty, f"{rid} visible on {day_before}"


def test_invalid_pre_event_available_at_fails_validator():
    bad = {
        "race_id": "senate-2014-LA",
        "state": "LA",
        "seat_class": "II",
        "kind": "regular",
        "term_type": "full",
        "stage": "runoff",
        "election_day": "2014-12-06",
        "available_at": "2014-11-22",
        "dem_votes": 1,
        "rep_votes": 2,
        "other_votes": 0,
        "two_party_margin": -1.0,
        "winner_party": "R",
        "winner_caucus": "R",
        "modeled_side": "R",
        "certification_status": "public_canvass_unproven",
        "source_object_hash": "abc",
    }
    errs = validate_contest(bad)
    assert any("precedes election_day" in e for e in errs)


def test_release_identity_hashes_match_truth_artifacts():
    report = verify_release_identity()
    assert report["ok"] is True, report.get("mismatches")
    assert PUBLIC_LIVE_ENABLED is False


def test_ledger_load_errors_do_not_emit_synthetic_certified(monkeypatch):
    import midterms.evidence.official_ledger as ol_mod
    from midterms.evidence.results_archive import build_certified_results_frame as build

    missing = Path("/nonexistent/official_senate_ledger.json")
    monkeypatch.setattr(ol_mod, "LEDGER_PATH", missing)
    with pytest.raises(FileNotFoundError):
        build(allow_exploratory_fallback=False)

    explor = build(allow_exploratory_fallback=True)
    assert len(explor) > 0
    assert (explor["certification_status"] != "certified").all()
