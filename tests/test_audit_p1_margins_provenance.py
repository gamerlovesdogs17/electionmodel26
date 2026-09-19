"""P1/P2 audit remediations: margin semantics, provenance, roster lock."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.evidence.build_certified_ledger_v3 import (
    CANVASS_OVERRIDES,
    ROSTER_PATH,
    write_expectations_from_roster,
)
from midterms.evidence.official_ledger import load_ledger
from midterms.evidence.score_targets import annotate_margin_semantics, truth_margin_map
from midterms.evidence.truth_contract import validate_contest
from midterms.validation.metrics import reliability_overconfidence


def test_independent_winners_not_score_eligible():
    ledger = load_ledger()
    for rid in ("senate-2018-ME", "senate-2018-VT"):
        year = rid.split("-")[1]
        contest = next(c for c in ledger["cycles"][year]["contests"] if c["race_id"] == rid)
        assert contest.get("score_eligible") is False, rid
        assert contest.get("two_party_margin") in (None, ), rid
        assert contest.get("winner_party") == "I"
        assert contest.get("winner_caucus") == "D"


def test_same_party_final_not_dem_minus_rep_eligible():
    ledger = load_ledger()
    contest = next(
        c for c in ledger["cycles"]["2022"]["contests"] if c["race_id"] == "senate-2022-AK"
    )
    assert contest.get("multiway", {}).get("same_party_general") or contest.get(
        "margin_definition"
    ) == "same_party_lead"
    assert contest.get("score_eligible") is False
    assert contest.get("margin_definition") == "same_party_lead"


def test_osborn_not_labeled_dem_nominee():
    ledger = load_ledger()
    contest = next(
        c for c in ledger["cycles"]["2024"]["contests"] if c["race_id"] == "senate-2024-NE"
    )
    annotated = annotate_margin_semantics(dict(contest))
    assert annotated.get("score_eligible") is False
    assert annotated.get("dem_nominee") in (None, "")
    assert annotated.get("two_party_margin") is None


def test_truth_margin_map_excludes_ineligibles():
    ledger = load_ledger()
    import pandas as pd

    flat = []
    for _y, block in ledger["cycles"].items():
        for c in block["contests"]:
            flat.append(
                {
                    "race_id": c["race_id"],
                    "two_party_margin": c.get("two_party_margin"),
                    "margin_value": c.get("margin_value"),
                    "score_eligible": c.get("score_eligible"),
                }
            )
    df = pd.DataFrame(flat)
    mmap = truth_margin_map(df)
    assert "senate-2018-ME" not in mmap
    assert "senate-2018-VT" not in mmap
    assert "senate-2022-AK" not in mmap
    assert "senate-2024-NE" not in mmap
    assert len(mmap) > 100


def test_override_hashes_differ_from_fte_discovery():
    ledger = load_ledger()
    for rid in CANVASS_OVERRIDES:
        year = rid.split("-")[1]
        contest = next(c for c in ledger["cycles"][year]["contests"] if c["race_id"] == rid)
        assert contest.get("discovery_source_hash"), rid
        assert contest["source_object_hash"] != contest["discovery_source_hash"], rid
        tier = str(contest.get("truth_tier") or "")
        assert "transcribed" in tier, rid
        assert validate_contest(contest) == [], (rid, validate_contest(contest))


def test_fake_fec_tier_rejected():
    bad = {
        "race_id": "senate-2018-OH",
        "state": "OH",
        "seat_class": "I",
        "kind": "regular",
        "stage": "general",
        "election_day": "2018-11-06",
        "available_at": "2018-11-22",
        "dem_votes": 1,
        "rep_votes": 1,
        "other_votes": 0,
        "two_party_margin": 0.0,
        "winner_party": "D",
        "winner_caucus": "D",
        "source_object_hash": "abc",
        "truth_tier": "fec_canvass",
        "source_url": "https://example.com",
        "certification_status": "public_canvass_unproven",
    }
    errs = validate_contest(bad)
    assert any("overstates provenance" in e for e in errs)


def test_roster_absent_fails(tmp_path, monkeypatch):
    missing = tmp_path / "no_roster.json"
    monkeypatch.setattr(
        "midterms.evidence.build_certified_ledger_v3.ROSTER_PATH", missing
    )
    with pytest.raises(FileNotFoundError):
        write_expectations_from_roster()


def test_thin_reliability_blocks_calibration_claim():
    bins = [
        {"bin_lo": 0.0, "bin_hi": 0.5, "n": 3, "mean_p": 0.4, "mean_y": 0.4},
        {"bin_lo": 0.5, "bin_hi": 1.0, "n": 3, "mean_p": 0.6, "mean_y": 0.6},
    ]
    gate = reliability_overconfidence(bins)
    assert gate["thin_sample"] is True
    assert gate["calibration_claim_allowed"] is False
