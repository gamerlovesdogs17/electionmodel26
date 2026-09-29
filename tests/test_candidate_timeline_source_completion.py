"""Cheap fail-closed tests for official candidate-list source auditing."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from midterms.evidence.candidate_source_audit import (
    parse_fec_congressional_ballot_workbook,
    prepare_official_candidate_source_audit,
)
from midterms.evidence.source_readiness import audit_source_readiness


def _workbook() -> bytes:
    frame = pd.DataFrame([
        {
            "STATE ABBREVIATION": "AA", "STATE": "Alpha", "DISTRICT": "S",
            "FEC ID#": "S2AA00001", "CANDIDATE NAME": "Candidate One", "PARTY": "DEM",
        },
        {
            "STATE ABBREVIATION": "AA", "STATE": "Alpha", "DISTRICT": "S",
            "FEC ID#": "S2AA00002", "CANDIDATE NAME": "Candidate Two", "PARTY": "REP",
        },
        {
            "STATE ABBREVIATION": "AA", "STATE": "Alpha", "DISTRICT": "01",
            "FEC ID#": "H2AA01001", "CANDIDATE NAME": "House Candidate", "PARTY": "DEM",
        },
    ])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="AA General Ballot 2022", index=False)
    return buffer.getvalue()


def test_parser_keeps_only_senate_rows_and_source_lineage():
    blob = _workbook()
    parsed = parse_fec_congressional_ballot_workbook(
        blob,
        cycle=2022,
        source_url="https://example.test/official.xlsx",
        source_sha256="a" * 64,
        available_at="2022-10-28T00:00:00+00:00",
        retrieved_at="2026-09-29T00:00:00+00:00",
    )
    assert len(parsed) == 2
    assert set(parsed["candidate_id"]) == {"S2AA00001", "S2AA00002"}
    assert parsed["source_object_sha256"].eq("a" * 64).all()


def test_late_official_workbook_is_archived_but_never_backdated(tmp_path: Path):
    blob = _workbook()
    races = pd.DataFrame([
        {
            "election_id": "senate-2022", "race_id": "senate-2022-AA",
            "state": "AA", "not_up": False,
        }
    ])
    races_path = tmp_path / "races.parquet"
    races.to_parquet(races_path, index=False)

    def fetcher(_url: str):
        return blob, {"Last-Modified": "Fri, 28 Oct 2022 16:00:02 GMT"}

    report = prepare_official_candidate_source_audit(
        sources={2022: "https://example.test/official.xlsx"},
        fetcher=fetcher,
        races_path=races_path,
        raw_dir=tmp_path / "raw",
        normalized_path=tmp_path / "inventory.parquet",
        manifest_path=tmp_path / "manifest.json",
        gaps_path=tmp_path / "gaps.json",
    )
    cutoff = report["coverage"]["senate-2022-lead-30"]
    assert cutoff["source_available_by_cutoff"] is False
    assert cutoff["covered_race_ids"] == []
    assert cutoff["missing_race_ids"] == ["senate-2022-AA"]
    assert report["production_eligible"] is False
    assert (tmp_path / "raw" / "fec_congressional_ballot_candidates_2022.xlsx").read_bytes() == blob


def test_missing_official_cycle_source_remains_an_explicit_gap(tmp_path: Path):
    races = pd.DataFrame([
        {
            "election_id": "senate-2020", "race_id": "senate-2020-AA",
            "state": "AA", "not_up": False,
        }
    ])
    races_path = tmp_path / "races.parquet"
    races.to_parquet(races_path, index=False)
    report = prepare_official_candidate_source_audit(
        sources={},
        races_path=races_path,
        raw_dir=tmp_path / "raw",
        normalized_path=tmp_path / "inventory.parquet",
        manifest_path=tmp_path / "manifest.json",
        gaps_path=tmp_path / "gaps.json",
    )
    gap = report["coverage"]["senate-2020-lead-60"]
    assert gap["source_available_at"] is None
    assert gap["missing_race_ids"] == ["senate-2020-AA"]
    assert "no traceable official" in gap["reason"]


def test_readiness_reports_only_the_candidate_races_still_missing(tmp_path: Path):
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    raw = tmp_path / "raw"
    normalized.mkdir()
    manifests.mkdir()
    (raw / "external").mkdir(parents=True)
    races = pd.DataFrame([
        {"election_id": "senate-2026", "race_id": "known", "state": "AA", "not_up": False},
        {"election_id": "senate-2026", "race_id": "missing", "state": "BB", "not_up": False},
    ])
    races.to_parquet(normalized / "races_official.parquet", index=False)
    common = {
        "election_id": "senate-2026", "event_type": "nomination",
        "effective_at": "2026-01-01", "available_at": "2026-01-02",
        "retrieved_at": "2026-01-02", "source_url": "https://example.test/official",
        "source_tier": "official", "source_object_sha256": "a" * 64,
        "source_hash": "a" * 64, "parser_version": "test-v1", "valid_from": "2026-01-02",
    }
    timeline = pd.DataFrame([
        {**common, "event_id": "m", "race_id": "known", "candidate_id": "c1",
         "candidate_name": "One", "modeled_side": "modeled", "ballot_party": "DEM"},
        {**common, "event_id": "o", "race_id": "known", "candidate_id": "c2",
         "candidate_name": "Two", "modeled_side": "opposing", "ballot_party": "REP"},
    ])
    timeline.to_parquet(normalized / "candidate_timeline.parquet", index=False)
    report = audit_source_readiness(
        election_id="senate-2026", as_of="2026-09-27",
        normalized_dir=normalized, manifests_dir=manifests, raw_dir=raw,
    )
    current = report["domains"]["candidate_timeline"]["cutoffs"]["senate-2026-current"]
    assert current["missing_race_ids"] == ["missing"]


def test_readiness_with_no_timeline_reports_every_contested_race(tmp_path: Path):
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    raw = tmp_path / "raw"
    normalized.mkdir()
    manifests.mkdir()
    raw.mkdir()
    pd.DataFrame([
        {"election_id": "senate-2026", "race_id": "r1", "state": "AA", "not_up": False},
        {"election_id": "senate-2026", "race_id": "held", "state": "BB", "not_up": True},
    ]).to_parquet(normalized / "races_official.parquet", index=False)
    report = audit_source_readiness(
        election_id="senate-2026", as_of="2026-09-27",
        normalized_dir=normalized, manifests_dir=manifests, raw_dir=raw,
    )
    current = report["domains"]["candidate_timeline"]["cutoffs"]["senate-2026-current"]
    assert current["missing_race_ids"] == ["r1"]
