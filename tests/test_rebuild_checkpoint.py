"""Synthetic lineage checks for reusing a failed rebuild checkpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.config import MODEL_VERSION
from midterms.evidence.evidence_bundle import build_evidence_bundle
from midterms.model.poll_structure import PollStructureConfig
from midterms.validation.artifact_lineage import frozen_index_semantic_sha256
from midterms.validation.nested_component_loo import _draws_fingerprint
from midterms.validation.rebuild_checkpoint import restore_rebuild_checkpoint
from midterms.validation.validated_model_spec import (
    CANONICAL_OOF_PHASE,
    CANDIDATE_SPEC_SCHEMA,
    SELECTION_OOF_PHASE,
    canonical_sha256,
    file_sha256,
    poll_structure_identity,
)


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _report_pair(root: Path, stem: str, *, phase: str, bundle: dict, config_id: str,
                 candidate_sha: str | None = None, failed: bool = False) -> tuple[Path, Path]:
    entries = []
    failures = []
    if failed:
        entries = [{
            "component": "pymc", "holdout_year": 2020, "lead_days": 60,
            "status": "failed", "prediction_sha256": None, "error": "synthetic convergence",
        }]
        failures = [{"year": 2020, "lead": 60, "component": "pymc"}]
    index = {
        "schema_version": "nested-component-frozen-index-v2",
        "model_version": MODEL_VERSION,
        "validation_phase": phase,
        "poll_structure_config_id": config_id,
        "n": len(entries),
        "entries": entries,
    }
    index_path = _write(root / f"{stem}_frozen.json", index)
    draws: dict = {}
    report = {
        "model_version": MODEL_VERSION,
        "validation_phase": phase,
        "evidence_bundle_id": bundle["evidence_bundle_id"],
        "evidence_bundle_sha256": bundle["evidence_bundle_sha256"],
        "poll_structure_config_id": config_id,
        "oof_draws": draws,
        "frozen_draws_sha256": _draws_fingerprint(draws),
        "frozen_index_semantic_sha256": frozen_index_semantic_sha256(index),
        "failures": failures,
        "stack_training_protocol": "formal_60_30_v1",
        "model_spec_candidate_sha256": candidate_sha,
    }
    report_path = _write(root / f"{stem}.json", report)
    return report_path, index_path


def _checkpoint(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "download" / "data" / "artifacts"
    destination = tmp_path / "current"
    bundle = build_evidence_bundle(
        as_of="2026-09-27", current_snapshot_id="snap-current",
        historical_snapshot_ids={"2020:60": "snap-history"}, domains={},
    )
    bundle_path = _write(tmp_path / "bundle.json", bundle)
    base_id = poll_structure_identity(PollStructureConfig())
    selection_path, _ = _report_pair(
        source, "nested_component_loo_selection", phase=SELECTION_OOF_PHASE,
        bundle=bundle, config_id=base_id,
    )
    crossfit = {
        "model_version": MODEL_VERSION,
        "source_validation_phase": SELECTION_OOF_PHASE,
        "source_nested_loo_sha256": file_sha256(selection_path),
        "source_frozen_draws_sha256": _draws_fingerprint({}),
        "source_evidence_bundle_id": bundle["evidence_bundle_id"],
        "source_evidence_bundle_sha256": bundle["evidence_bundle_sha256"],
    }
    crossfit_path = _write(source / "poll_structure_crossfit_latest.json", crossfit)
    selected = PollStructureConfig(study_effect=True)
    candidate = {
        "schema_version": CANDIDATE_SPEC_SCHEMA,
        "model_version": MODEL_VERSION,
        "status": "candidate_pending_canonical_oof",
        "evidence_bundle_id": bundle["evidence_bundle_id"],
        "evidence_bundle_sha256": bundle["evidence_bundle_sha256"],
        "poll_structure_selection_sha256": file_sha256(crossfit_path),
        "poll_structure_selection_source_nested_sha256": file_sha256(selection_path),
        "selected_poll_structure": selected.to_dict(),
        "selected_poll_structure_id": poll_structure_identity(selected),
    }
    candidate["spec_sha256"] = canonical_sha256(candidate)
    candidate_path = _write(source / "validated_model_spec_candidate.json", candidate)
    _report_pair(
        source, "nested_component_loo_canonical", phase=CANONICAL_OOF_PHASE,
        bundle=bundle, config_id=poll_structure_identity(selected),
        candidate_sha=file_sha256(candidate_path), failed=True,
    )
    return tmp_path / "download", bundle_path, destination


def test_restore_checkpoint_reuses_only_lineage_checked_oof_files(tmp_path: Path) -> None:
    source, bundle, destination = _checkpoint(tmp_path)
    _write(source / "data" / "artifacts" / "stack_weights_oof.json", {"stale": True})
    _write(destination / "stack_weights_oof.json", {"keep": True})
    result = restore_rebuild_checkpoint(
        source_root=source, evidence_bundle_path=bundle, artifacts_dir=destination,
    )
    assert result["ok"] is True
    assert result["canonical_failures_to_repair"] == [
        {"year": 2020, "lead": 60, "component": "pymc"}
    ]
    assert (destination / "nested_component_loo_canonical.json").is_file()
    assert json.loads((destination / "stack_weights_oof.json").read_text()) == {"keep": True}


def test_restore_checkpoint_rejects_bundle_mismatch_before_copy(tmp_path: Path) -> None:
    source, bundle, destination = _checkpoint(tmp_path)
    canonical_path = source / "data" / "artifacts" / "nested_component_loo_canonical.json"
    canonical = json.loads(canonical_path.read_text())
    canonical["evidence_bundle_id"] = "eb-stale"
    canonical_path.write_text(json.dumps(canonical), encoding="utf-8")
    destination.mkdir()
    marker = _write(destination / "nested_component_loo_canonical.json", {"keep": True})
    with pytest.raises(ValueError, match="evidence bundle"):
        restore_rebuild_checkpoint(
            source_root=source, evidence_bundle_path=bundle, artifacts_dir=destination,
        )
    assert json.loads(marker.read_text()) == {"keep": True}
