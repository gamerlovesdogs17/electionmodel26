"""Cheap fail-closed tests for official candidate-list source auditing."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pandas as pd

from midterms.evidence.candidate_source_audit import (
    SEALED_SOURCE_RECEIPTS,
    _event_is_visible_in_document,
    parse_fec_congressional_ballot_workbook,
    prepare_official_candidate_source_audit,
    prepare_sealed_candidate_timeline_sources,
)
from midterms.evidence.candidate_timeline import (
    apply_candidate_timeline,
    candidate_timeline_fingerprint,
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


def test_readiness_requires_current_identity_for_every_labeled_contest(tmp_path: Path):
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
    assert current["publication_eligible"] is False


def test_readiness_with_no_timeline_fails_current_contest_closed(tmp_path: Path):
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
    assert current["publication_eligible"] is False


def test_candidate_mapping_must_be_visible_with_party_near_name():
    event = {"candidate_name": "Example Person", "ballot_party": "DEM"}
    assert _event_is_visible_in_document(
        event, "UNITED STATES SENATOR EXAMPLE PERSON DEMOCRATIC RETIRED TEACHER",
    )
    assert not _event_is_visible_in_document(
        event, "EXAMPLE PERSON UNAFFILIATED " + ("X " * 80) + "DEMOCRATIC",
    )


def test_sealed_california_sources_are_deterministic_and_keep_unexpired_distinct(
    tmp_path: Path,
):
    races_path = Path("data/normalized/races_official.parquet")
    first_timeline = tmp_path / "first.parquet"
    first = prepare_sealed_candidate_timeline_sources(
        receipt_path=SEALED_SOURCE_RECEIPTS,
        races_path=races_path,
        timeline_path=first_timeline,
        timeline_manifest_path=tmp_path / "first-manifest.json",
        source_audit_path=tmp_path / "first-audit.json",
        gaps_path=tmp_path / "first-gaps.json",
    )
    second_timeline = tmp_path / "second.parquet"
    second = prepare_sealed_candidate_timeline_sources(
        receipt_path=SEALED_SOURCE_RECEIPTS,
        races_path=races_path,
        timeline_path=second_timeline,
        timeline_manifest_path=tmp_path / "second-manifest.json",
        source_audit_path=tmp_path / "second-audit.json",
        gaps_path=tmp_path / "second-gaps.json",
    )
    first_frame = pd.read_parquet(first_timeline)
    second_frame = pd.read_parquet(second_timeline)
    assert candidate_timeline_fingerprint(first_frame) == candidate_timeline_fingerprint(second_frame)
    assert first["timeline"]["semantic_sha256"] == second["timeline"]["semantic_sha256"]
    assert set(first_frame.loc[
        first_frame["election_id"].eq("senate-2022"), "race_id",
    ]) == {"senate-2022-CA", "senate-2022-CA-unexpired"}
    assert set(first_frame.loc[
        first_frame["election_id"].eq("senate-2024"), "race_id",
    ]) == {"senate-2024-CA", "senate-2024-CA-unexpired"}


def test_late_primary_and_special_finalist_gaps_are_explicit(tmp_path: Path):
    report = prepare_sealed_candidate_timeline_sources(
        receipt_path=SEALED_SOURCE_RECEIPTS,
        races_path=Path("data/normalized/races_official.parquet"),
        timeline_path=tmp_path / "timeline.parquet",
        timeline_manifest_path=tmp_path / "manifest.json",
        source_audit_path=tmp_path / "audit.json",
        gaps_path=tmp_path / "gaps.json",
    )
    late = report["coverage"]["senate-2020-lead-60"]["structurally_unavailable"]
    assert {row["race_id"] for row in late} >= {
        "senate-2020-DE", "senate-2020-NH", "senate-2020-RI",
        "senate-2020-GA-special",
    }
    assert report["coverage"]["senate-2020-lead-30"]["structurally_unavailable"] == [
        {
            "race_id": "senate-2020-GA-special",
            "gap_type": "special_election_finalists_not_yet_determined",
            "event_date": "2020-11-03",
            "reason": "the nonpartisan special-election runoff pairing was determined after the replay cutoff",
        }
    ]


def test_sealed_source_hash_change_fails_closed(tmp_path: Path):
    receipt = json.loads(SEALED_SOURCE_RECEIPTS.read_text(encoding="utf-8"))
    source = receipt["sources"][0]
    shutil.copy2(SEALED_SOURCE_RECEIPTS.parent / source["raw_path"], tmp_path / source["raw_path"])
    source["raw_sha256"] = "0" * 64
    receipt["sources"] = [source]
    receipt["structural_unavailability"] = {}
    receipt_path = tmp_path / "source_receipts.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    races_path = tmp_path / "races.parquet"
    pd.DataFrame([{
        "election_id": "senate-2018", "race_id": "senate-2018-CA",
        "state": "CA", "not_up": False,
    }]).to_parquet(races_path, index=False)
    import pytest
    with pytest.raises(ValueError, match="source hash changed"):
        prepare_sealed_candidate_timeline_sources(
            receipt_path=receipt_path,
            races_path=races_path,
            timeline_path=tmp_path / "timeline.parquet",
            timeline_manifest_path=tmp_path / "manifest.json",
            source_audit_path=tmp_path / "audit.json",
            gaps_path=tmp_path / "gaps.json",
        )


def test_official_candidate_event_obeys_available_at():
    timeline = pd.read_parquet("data/normalized/candidate_timeline.parquet")
    races = pd.DataFrame([{
        "election_id": "senate-2024", "race_id": "senate-2024-CA",
        "state": "CA", "not_up": False,
    }])
    before, _ = apply_candidate_timeline(races, timeline, as_of="2024-08-28")
    after, _ = apply_candidate_timeline(races, timeline, as_of="2024-08-29")
    assert before.loc[0, "candidate_timeline_status"] != "point_in_time"
    assert after.loc[0, "candidate_timeline_status"] == "point_in_time"
    assert after.loc[0, "modeled_candidate_name"] == "Adam B. Schiff"
    assert after.loc[0, "opposing_candidate_name"] == "Steve Garvey"
