"""Synthetic source contracts, bundle identity, and workflow wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from midterms.evidence.candidate_timeline import (
    apply_candidate_timeline,
    validate_candidate_timeline_source,
)
from midterms.evidence.demographic_vintages import select_demographic_vintage
from midterms.evidence.economics import (
    audit_realtime_economic_coverage,
    select_realtime_vintage_as_of,
)
from midterms.evidence.evidence_bundle import build_evidence_bundle, verify_evidence_bundle
from midterms.evidence.source_readiness import (
    audit_source_readiness,
    evaluate_readiness_gate,
)

HASH = "a" * 64


def _timeline() -> pd.DataFrame:
    return pd.DataFrame([{
        "election_id": "senate-2026", "event_id": "e1", "race_id": "r1",
        "candidate_id": "c1", "candidate_name": "Synthetic One", "modeled_side": "modeled",
        "ballot_party": "I", "caucus_affiliation": "D", "caucus_basis": "model_assumption",
        "event_type": "nomination", "effective_at": "2026-06-01",
        "available_at": "2026-06-02", "retrieved_at": "2026-06-03",
        "source_url": "https://example.test/e1", "source_tier": "official",
        "source_object_sha256": HASH, "parser_version": "test-v1",
        "valid_from": "2026-06-02", "valid_to": None,
    }])


def test_candidate_timeline_contract_and_future_event_exclusion():
    timeline = validate_candidate_timeline_source(_timeline())
    races = pd.DataFrame([{"election_id": "senate-2026", "race_id": "r1", "not_up": False}])
    early, meta = apply_candidate_timeline(races, timeline, as_of="2026-06-01")
    assert pd.isna(early.loc[0, "modeled_candidate_id"])
    assert meta["n_events_applied"] == 0
    late, _ = apply_candidate_timeline(races, timeline, as_of="2026-06-02")
    assert late.loc[0, "modeled_candidate_id"] == "c1"


def test_demographic_vintage_released_after_cutoff_is_excluded():
    rows = pd.DataFrame([{
        "dataset_id": "ACS", "dataset_version": "v1", "geographic_level": "state",
        "source_vintage": "2024", "official_release_date": "2025-12-01",
        "retrieved_at": "2026-01-01", "source_url": "https://example.test/acs",
        "raw_object_sha256": HASH, "feature_definition_version": "features-v1",
        "state": "AA", "college": .3, "nonwhite": .4, "density": 1.0,
        "age": 0.0, "urban": .7,
    }])
    selected, meta = select_demographic_vintage(rows, as_of="2025-11-30")
    assert selected.empty
    assert meta["production_eligible"] is False
    selected, meta = select_demographic_vintage(rows, as_of="2025-12-01")
    assert len(selected) == 1 and meta["production_eligible"] is True


def test_economic_revision_after_cutoff_is_excluded():
    rows = pd.DataFrame([
        {"series_id": "S", "observation_date": "2020-01-01", "realtime_start": "2020-02-01",
         "realtime_end": "2020-03-31", "available_at": "2020-02-01", "value": 1.0,
         "vintage_id": "old", "status": "alfred_realtime", "source_url": "https://example.test",
         "source_sha256": HASH, "retrieved_at": "2026-01-01", "parser_version": "v1"},
        {"series_id": "S", "observation_date": "2020-01-01", "realtime_start": "2020-04-01",
         "realtime_end": "9999-12-31", "available_at": "2020-04-01", "value": 9.0,
         "vintage_id": "revised", "status": "alfred_realtime", "source_url": "https://example.test",
         "source_sha256": HASH, "retrieved_at": "2026-01-01", "parser_version": "v1"},
    ])
    selected = select_realtime_vintage_as_of(rows, as_of="2020-03-01")
    assert selected["value"].tolist() == [1.0]


def test_missing_economic_secret_is_machine_readable():
    rows = pd.DataFrame([{"series_id": "WB_X", "status": "worldbank_api"}])
    report = audit_realtime_economic_coverage(
        rows, cutoffs={"fold": "2020-01-01"}, api_key_available=False,
    )
    assert report["status"] == "missing_secret"
    assert report["required_secret"] == "FRED_API_KEY"


def test_disabled_optional_domain_does_not_block_but_required_core_does():
    domains = {
        "polls": {"status": "missing", "required_for_core": True,
                  "required_for_historical_validation": True, "reasons": ["missing"]},
        "markets": {"status": "optional_disabled", "required_for_core": False,
                    "required_for_historical_validation": False, "reasons": ["disabled"]},
    }
    blockers, warnings, optional = evaluate_readiness_gate(domains)
    assert [row["domain"] for row in blockers] == ["polls"]
    assert warnings == []
    assert optional == ["markets"]


def test_missing_timeline_readiness_lists_exact_race_and_cutoff(tmp_path: Path):
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    raw = tmp_path / "raw"
    normalized.mkdir(); manifests.mkdir(); raw.mkdir()
    pd.DataFrame([{
        "election_id": "senate-2026", "race_id": "synthetic-race", "not_up": False,
    }]).to_parquet(normalized / "races_official.parquet", index=False)
    report = audit_source_readiness(
        election_id="senate-2026", as_of="2026-09-01",
        normalized_dir=normalized, manifests_dir=manifests, raw_dir=raw, environ={},
    )
    timeline = report["domains"]["candidate_timeline"]
    assert timeline["status"] == "missing"
    current = next(row for row in timeline["missing_coverage"] if row["cutoff"] == "senate-2026-current")
    assert current["race_ids"] == ["synthetic-race"]


def _bundle(domains: dict) -> dict:
    return build_evidence_bundle(
        as_of="2026-09-01", current_snapshot_id="current",
        historical_snapshot_ids={"b": "2", "a": "1"}, domains=domains,
    )


def test_bundle_identity_is_order_stable_and_ignores_local_metadata():
    a = _bundle({
        "polls": {"status": "ready", "semantic_sha256": "1", "source_hashes": ["b", "a"],
                  "path": "C:/Users/alice/repo", "mtime": 1},
        "races": {"status": "ready", "semantic_sha256": "2"},
    })
    b = _bundle({
        "races": {"semantic_sha256": "2", "status": "ready"},
        "polls": {"source_hashes": ["a", "b"], "semantic_sha256": "1", "status": "ready",
                  "path": "/home/runner/repo", "mtime": 99},
    })
    assert a["evidence_bundle_id"] == b["evidence_bundle_id"]
    assert verify_evidence_bundle(a)["ok"] is True


def test_substantive_source_mutation_changes_bundle_id():
    a = _bundle({"polls": {"status": "ready", "semantic_sha256": "1"}})
    b = _bundle({"polls": {"status": "ready", "semantic_sha256": "2"}})
    assert a["evidence_bundle_id"] != b["evidence_bundle_id"]


def test_workflow_has_two_stage_lineage_and_fail_closed_order():
    rebuild = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
    prepare = Path(".github/workflows/prepare-evidence.yml").read_text(encoding="utf-8")
    assert "group: research-rebuild" in rebuild and "cancel-in-progress: false" in rebuild
    assert rebuild.index("prepare_evidence:") < rebuild.index("rebuild:") < rebuild.index("deploy_pages:")
    assert "needs: prepare_evidence" in rebuild
    assert "ref: ${{ needs.prepare_evidence.outputs.evidence_commit_sha }}" in rebuild
    expensive = rebuild[rebuild.index("  rebuild:"):]
    assert "refresh-safe" not in expensive
    assert "if: always()" in rebuild
    assert rebuild.index("acceptance-gates --strict") < rebuild.index("Commit successful model artifacts")
    assert "if: needs.rebuild.result == 'success'" in rebuild
    assert "workflow_dispatch:" in prepare
    assert "publish-live" not in rebuild and "publish-live" not in prepare
