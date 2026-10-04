"""Cheap v0.9.23 candidate, matchup, coverage, and lineage regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from midterms.config import MODEL_VERSION
from midterms.evidence import ingest
from midterms.evidence.candidate_timeline import apply_candidate_state_contract
from midterms.evidence.candidates import candidate_party
from midterms.evidence.current_poll_coverage import current_race_poll_coverage
from midterms.evidence.ingest import normalize_votehub_senate_polls
from midterms.evidence.outcome_identity import require_binary_chamber_compatibility
from midterms.evidence.schema import POLL_COLUMNS
from midterms.evidence.source_readiness import audit_source_readiness
from midterms.validation.artifact_lineage import require_current_model_version
from midterms.validation.historical_evidence_equivalence import (
    compare_historical_projections,
)


def _payload(state: str, polls: list[tuple[int, str, str]]) -> dict:
    return {
        "polls": [
            {
                "id": poll_id,
                "subject": f"2026 {state}",
                "pollster": "Example Research",
                "start_date": "2026-09-01",
                "end_date": "2026-09-02",
                "created_at": "2026-09-03T12:00:00+00:00",
                "sample_size": 800,
                "answers": [
                    {"choice": modeled, "pct": 48},
                    {"choice": opposing, "pct": 47},
                    {"choice": "Undecided", "pct": 5},
                ],
            }
            for poll_id, modeled, opposing in polls
        ]
    }


def _event(side: str, name: str, party: str) -> dict:
    slug = name.lower().replace(" ", "-")
    return {
        "election_id": "senate-2026",
        "event_id": f"event-{side}-{slug}",
        "race_id": "senate-2026-TX",
        "candidate_id": f"official:{slug}",
        "modeled_side": side,
        "event_type": "nomination",
        "effective_at": "2026-08-01T00:00:00+00:00",
        "available_at": "2026-08-02T00:00:00+00:00",
        "retrieved_at": "2026-08-02T01:00:00+00:00",
        "candidate_name": name,
        "ballot_party": party,
        "source_url": "https://elections.example.gov/candidates",
        "source_tier": "official",
        "source_object_sha256": "a" * 64,
        "source_hash": "a" * 64,
        "parser_version": "test-official-v1",
        "valid_from": "2026-08-02T00:00:00+00:00",
    }


def test_votehub_candidate_identity_survives_canonical_normalization():
    frame = normalize_votehub_senate_polls(
        _payload("Texas", [(1, "James Talarico", "Ken Paxton")]),
        write_manifest=False,
    )
    assert set(POLL_COLUMNS).issubset(frame.columns)
    row = frame.iloc[0]
    assert row["modeled_candidate_name"] == "James Talarico"
    assert row["opposing_candidate_name"] == "Ken Paxton"
    assert row["dem_candidate_name"] == "James Talarico"
    assert row["modeled_candidate_id"] == "votehub:james-talarico"
    assert row["matchup_id"].endswith(
        "votehub:james-talarico|votehub:ken-paxton"
    )


def test_merge_keeps_candidate_specific_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw_dir = tmp_path / "data" / "raw"
    normalized = tmp_path / "data" / "normalized"
    manifests = tmp_path / "data" / "manifests"
    (raw_dir / "external").mkdir(parents=True)
    normalized.mkdir(parents=True)
    manifests.mkdir(parents=True)
    payload_path = raw_dir / "external" / "votehub_us_senator.json"
    payload_path.write_text(
        json.dumps(_payload("Texas", [(1, "James Talarico", "Ken Paxton")])),
        encoding="utf-8",
    )
    monkeypatch.setattr(ingest, "RAW_DIR", raw_dir)
    monkeypatch.setattr(ingest, "NORMALIZED_DIR", normalized)
    monkeypatch.setattr(ingest, "MANIFESTS_DIR", manifests)
    monkeypatch.setattr(ingest, "write_normalized_ratings", lambda: None)
    monkeypatch.setattr(ingest, "generic_ballot_latest", lambda: None)
    ingest.merge_live_polls_into_warehouse(payload_path=payload_path)
    stored = pd.read_parquet(normalized / "polls.parquet")
    assert stored.loc[0, "modeled_candidate_name"] == "James Talarico"
    assert stored.loc[0, "matchup_id"]


def test_resolved_current_nominee_filters_obsolete_matchups():
    polls = normalize_votehub_senate_polls(
        _payload(
            "Texas",
            [
                (1, "James Talarico", "Ken Paxton"),
                (2, "James Talarico", "John Cornyn"),
            ],
        ),
        write_manifest=False,
    )
    races = pd.DataFrame([{
        "election_id": "senate-2026", "race_id": "senate-2026-TX",
        "state": "TX", "not_up": False,
    }])
    timeline = pd.DataFrame([
        _event("modeled", "James Talarico", "DEM"),
        _event("opposing", "Ken Paxton", "REP"),
    ])
    applied, safe, meta = apply_candidate_state_contract(
        races, timeline, polls, as_of="2026-10-03",
    )
    assert applied.loc[0, "candidate_identity_resolved"] is True
    assert safe["poll_id"].tolist() == ["vh-1"]
    assert meta["poll_exclusions"] == [{
        "poll_id": "vh-2", "race_id": "senate-2026-TX",
        "reason": "matchup_not_selected_by_point_in_time_identity",
    }]


def test_multiple_matchups_without_source_identity_fail_closed():
    polls = normalize_votehub_senate_polls(
        _payload("Texas", [(1, "James Talarico", "Ken Paxton"), (2, "James Talarico", "John Cornyn")]),
        write_manifest=False,
    )
    races = pd.DataFrame([{
        "election_id": "senate-2026", "race_id": "senate-2026-TX",
        "state": "TX", "not_up": False,
    }])
    applied, safe, meta = apply_candidate_state_contract(
        races, pd.DataFrame(), polls, as_of="2026-10-03",
    )
    assert applied.loc[0, "candidate_state_eligible"] is False
    assert safe.empty
    assert meta["production_eligible"] is False


def test_pre_nomination_poll_does_not_leak_eventual_nominee():
    polls = normalize_votehub_senate_polls(
        _payload("Texas", [(1, "James Talarico", "Ken Paxton")]),
        write_manifest=False,
    )
    polls["available_at"] = "2020-09-01"
    polls["election_id"] = "senate-2020"
    polls["race_id"] = "senate-2020-TX"
    races = pd.DataFrame([{
        "election_id": "senate-2020", "race_id": "senate-2020-TX",
        "state": "TX", "not_up": False,
    }])
    _, safe, meta = apply_candidate_state_contract(
        races, pd.DataFrame(), polls, as_of="2020-09-04",
        structural_gaps=[{
            "race_id": "senate-2020-TX", "gap_type": "nomination_not_yet_determined",
        }],
    )
    assert safe.empty
    assert meta["poll_exclusions"][0]["reason"] == "candidate_specific_poll_before_nomination_excluded"


def test_sc_party_and_independent_semantics():
    assert candidate_party("Darline Graham") == "R"
    assert candidate_party("Dan Osborn") == "I"
    ne = normalize_votehub_senate_polls(
        _payload("Nebraska", [(1, "Dan Osborn", "Pete Ricketts")]),
        write_manifest=False,
    ).iloc[0]
    assert ne["modeled_ballot_party"] == "I"
    assert pd.isna(ne["dem_candidate_id"])
    assert pd.isna(ne["two_party_margin"])
    assert ne["margin_definition"] == "independent_minus_rep_two_candidate"


def test_binary_ineligible_race_cannot_enter_chamber_forecast():
    races = pd.DataFrame([{
        "race_id": "senate-2026-AK", "not_up": False,
        "binary_score_eligible": False,
        "binary_score_exclusion_reason": "ranked_choice_multi_candidate_structure",
        "modeled_ballot_party": "D", "modeled_caucus": "D",
        "modeled_caucus_basis": "test", "opposing_caucus": "R",
        "opposing_caucus_basis": "test",
    }])
    with pytest.raises(ValueError, match="ineligible for binary chamber forecast"):
        require_binary_chamber_compatibility(races)


def test_identity_sensitive_zero_compatible_polls_is_a_hard_coverage_failure():
    races = pd.DataFrame([{
        "election_id": "senate-2026", "race_id": "senate-2026-NH",
        "state": "NH", "not_up": False, "ballot_status": "nominated",
        "modeled_candidate_name": "Chris Pappas", "opposing_candidate_name": "Unknown",
        "modeled_ballot_party": "D", "opposing_ballot_party": "R",
    }])
    polls = normalize_votehub_senate_polls(
        _payload("New Hampshire", [(1, "Chris Pappas", "Scott Brown")]),
        write_manifest=False,
    )
    meta = {"snapshot_sha256": "a" * 64, "poll_exclusions": [], "classification_records": [{
        "race_id": "senate-2026-NH", "candidate_state": "identity_required",
        "candidate_state_reason": "unresolved", "candidate_identity_required": True,
        "candidate_identity_resolved": False, "binary_score_eligible": True,
    }]}
    report = current_race_poll_coverage(
        races, polls, polls.iloc[0:0], meta, as_of="2026-10-03",
    )
    assert report["summary"]["promotion_eligible"] is False
    assert report["races"][0]["status"] == "fail"


def test_votehub_lineage_fails_when_raw_semantics_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw = tmp_path / "raw"
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    raw.mkdir(); normalized.mkdir(); manifests.mkdir()
    source = raw / "source.json"
    source.write_text('{"price": 1}', encoding="utf-8")
    (normalized / "polls_live_votehub.parquet").write_bytes(b"live")
    (normalized / "polls.parquet").write_bytes(b"warehouse")
    monkeypatch.setattr(ingest, "RAW_DIR", raw)
    monkeypatch.setattr(ingest, "NORMALIZED_DIR", normalized)
    manifest = {
        "schema_version": ingest.VOTEHUB_LINEAGE_VERSION,
        "source": {"raw_path": "raw/source.json", "raw_canonical_json_sha256": ingest._canonical_json_sha256({"price": 1})},
        "outputs": {
            "live_parquet_byte_sha256": ingest._file_sha256(normalized / "polls_live_votehub.parquet"),
            "warehouse_parquet_byte_sha256": ingest._file_sha256(normalized / "polls.parquet"),
        },
    }
    manifest_path = manifests / "lineage.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert ingest.verify_votehub_poll_lineage(manifest_path)["ok"] is True
    source.write_text('{"price": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="semantic content changed"):
        ingest.verify_votehub_poll_lineage(manifest_path)


def test_historical_equivalence_detects_equal_and_changed_cutoffs():
    entry = {"races": [{"race_id": "r"}], "polls": [{"poll_id": "p"}], "candidate_timeline": {}}
    before = {"model_version": "v-old", "cutoffs": {"cycle@date": {**entry, "semantic_sha256": "a"}}}
    same = {"model_version": "v-new", "cutoffs": {"cycle@date": {**entry, "semantic_sha256": "b"}}}
    assert compare_historical_projections(before, same)["classification"] == "historically_equivalent"
    changed = json.loads(json.dumps(same))
    changed["cutoffs"]["cycle@date"]["polls"][0]["poll_id"] = "changed"
    assert compare_historical_projections(before, changed)["classification"] == "historical_inputs_changed"


def test_checked_in_votehub_regression_matchups():
    frame = normalize_votehub_senate_polls(write_manifest=False)
    expected = {
        "SC": ("Annie Andrews", "Darline Graham"),
        "NE": ("Dan Osborn", "Pete Ricketts"),
        "KS": ("Adam Hamilton", "Roger Marshall"),
        "OH": ("Sherrod Brown", "Jon Husted"),
    }
    for state, pair in expected.items():
        rows = frame[frame["state"].eq(state)]
        assert ((rows["modeled_candidate_name"] == pair[0]) & (rows["opposing_candidate_name"] == pair[1])).any()
    for state in ("NH", "ME", "MI", "FL", "TX", "IA", "MN", "GA", "NC"):
        assert frame.loc[frame["state"].eq(state), "matchup_id"].nunique() > 1


def test_v0922_empirical_artifact_is_stale_at_v0923_boundary():
    assert MODEL_VERSION == "senate-hierarchical-v0.9.23"
    with pytest.raises(ValueError, match="stale historical OOF model_version"):
        require_current_model_version(
            {"model_version": "senate-hierarchical-v0.9.22"},
            label="historical OOF",
        )


def test_generic_ballot_readiness_does_not_inherit_matchup_failure():
    report = audit_source_readiness(
        election_id="senate-2026", as_of="2026-10-03", environ={}
    )
    assert report["domains"]["polls"]["status"] == "incomplete_coverage"
    assert report["domains"]["generic_ballot"]["status"] == "ready"
