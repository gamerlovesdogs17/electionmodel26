"""Synthetic lineage checks for reusing a failed rebuild checkpoint."""

from __future__ import annotations

import gzip
import importlib
import json
from pathlib import Path

import pytest

from midterms.config import MODEL_VERSION
from midterms.evidence.evidence_bundle import build_evidence_bundle
from midterms.model.poll_structure import PollStructureConfig
from midterms.validation.artifact_lineage import frozen_index_semantic_sha256
from midterms.validation.nested_component_loo import (
    FrozenPrediction,
    _draws_fingerprint,
    _write_repair_checkpoint,
    read_repair_checkpoint,
    repair_checkpoint_path,
)
from midterms.validation.rebuild_checkpoint import (
    _verify_independent_rebuild_artifact,
    restore_rebuild_checkpoint,
)
from midterms.validation.validated_model_spec import (
    CANDIDATE_SPEC_SCHEMA,
    CANONICAL_OOF_PHASE,
    SELECTION_OOF_PHASE,
    canonical_sha256,
    file_sha256,
    poll_structure_identity,
)
from midterms.ops.reproducibility import independent_rebuild


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
            "as_of": "2020-09-04", "seed": 2081,
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
        "prior_snapshot_sha256_by_fold_lead": {"2020": {"60": "prior-sha"}},
        "presidential_source_sha256_by_fold_lead": {"2020": {"60": "presidential-sha"}},
    }
    report_path = _write(root / f"{stem}.json", report)
    return report_path, index_path


def _checkpoint(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "download" / "data" / "artifacts"
    destination = tmp_path / "current"
    bundle = build_evidence_bundle(
        as_of="2026-09-27", current_snapshot_id="snap-current",
        historical_snapshot_ids={"senate-2020-lead-60": "snap-history"}, domains={},
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
    canonical_path, canonical_index_path = _report_pair(
        source, "nested_component_loo_canonical", phase=CANONICAL_OOF_PHASE,
        bundle=bundle, config_id=poll_structure_identity(selected),
        candidate_sha=file_sha256(candidate_path), failed=True,
    )
    canonical_index = json.loads(canonical_index_path.read_text())
    original = canonical_index["entries"][0]
    replacement = FrozenPrediction(
        component="pymc", election_id="senate-2020", holdout_year=2020,
        lead_days=60, as_of="2020-09-04", race_ids=["synthetic-race"],
        means=[0.0], sds=[1.0], method="pymc", n_draws=2, seed=2081,
        status="ok", draws_by_race={"synthetic-race": [-1.0, 1.0]},
        prediction_sha256=_draws_fingerprint({"synthetic-race": [-1.0, 1.0]}),
        evidence_snapshot_id="snap-history", prior_snapshot_sha256="prior-sha",
        presidential_source_sha256="presidential-sha",
    )
    _write_repair_checkpoint(
        repair_checkpoint_path(
            canonical_path, component="pymc", year=2020, lead_days=60,
        ),
        replacement=replacement,
        index=canonical_index,
        original_entry=original,
        recovery_settings={
            "draws_per_chain": 2000, "tune_per_chain": 4000,
            "chains": 4, "target_accept": 0.99,
            "parameterization": "noncentered_scale_mixtures_v1",
        },
    )
    return tmp_path / "download", bundle_path, destination


def test_restore_checkpoint_reuses_only_lineage_checked_oof_files(tmp_path: Path) -> None:
    source, bundle, destination = _checkpoint(tmp_path)
    _write(source / "data" / "artifacts" / "stack_weights_oof.json", {"stale": True})
    _write(
        source / "data" / "artifacts" / "posterior_predictive_oof_latest.json",
        {"source_nested_sha256": "stale"},
    )
    _write(destination / "stack_weights_oof.json", {"keep": True})
    result = restore_rebuild_checkpoint(
        source_root=source, evidence_bundle_path=bundle, artifacts_dir=destination,
    )
    assert result["ok"] is True
    assert result["schema_version"] == "rebuild-checkpoint-restore-v3"
    assert result["forecast_restored"] is False
    assert result["canonical_failures_to_repair"] == [
        {"year": 2020, "lead": 60, "component": "pymc"}
    ]
    assert (destination / "nested_component_loo_canonical.json").is_file()
    assert (
        destination / "oof_inference_repair_checkpoints"
        / "nested_component_loo_canonical__pymc__2020__60.json.gz"
    ).is_file()
    posterior = json.loads(
        (destination / "posterior_predictive_oof_latest.json").read_text()
    )
    assert posterior["source_nested_sha256"] == file_sha256(
        destination / "nested_component_loo_canonical.json"
    )
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


def test_restore_checkpoint_rejects_tampered_repair_draws(tmp_path: Path) -> None:
    source, bundle, destination = _checkpoint(tmp_path)
    checkpoint = next(
        (source / "data" / "artifacts" / "oof_inference_repair_checkpoints").glob(
            "*.json.gz"
        )
    )
    payload = read_repair_checkpoint(checkpoint)
    payload["frozen_prediction"]["draws_by_race"]["synthetic-race"][0] = -9.0
    checkpoint.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8"), mtime=0))
    with pytest.raises(ValueError, match="predictive draws changed"):
        restore_rebuild_checkpoint(
            source_root=source, evidence_bundle_path=bundle, artifacts_dir=destination,
        )
    assert not destination.exists()


def test_restore_checkpoint_refuses_partial_publication_outputs(tmp_path: Path) -> None:
    source, bundle, destination = _checkpoint(tmp_path)
    source_artifacts = source / "data" / "artifacts"
    _write(source_artifacts / "forecast_latest.json", {
        "model_version": MODEL_VERSION, "publishable": True,
    })
    destination.mkdir()
    marker = _write(destination / "forecast_latest.json", {"keep": True})
    result = restore_rebuild_checkpoint(
        source_root=source, evidence_bundle_path=bundle, artifacts_dir=destination,
    )
    assert result["forecast_restored"] is False
    assert "incomplete" in result["forecast_restore_reason"]
    assert json.loads(marker.read_text()) == {"keep": True}


def test_checkpoint_reuses_only_lineage_checked_independent_rebuild(tmp_path: Path) -> None:
    source = tmp_path / "data" / "artifacts"
    source.mkdir(parents=True)
    run_id = "synthetic-publication-run"
    forecast_sha = "a" * 64
    generated_at = "2026-10-03T12:00:00+00:00"
    _write(source / "forecast_latest.json", {
        "run_id": run_id, "generated_at": generated_at,
    })
    configuration = {
        "method": "pymc", "draws": 2000, "tune": 4000, "chains": 4,
        "target_accept": 0.99, "seed": 20260901, "generic_ballot": 1.5,
        "ensemble": True, "with_ratings": False, "with_markets": False,
        "rating_weight": 0.15, "market_weight": 0.12, "control_weight": 0.15,
        "control_calibrate": False, "allow_fast_fallback": False,
    }
    manifest_path = _write(tmp_path / "data" / "manifests" / f"run_{run_id}.json", {
        "run_id": run_id, "election_id": "senate-2026",
        "forecast_as_of": "2026-09-27", "configuration": configuration,
        "output_hashes": {"forecast_json": forecast_sha},
    })
    independent = {
        "ok": True, "mode": "independent", "run_id": run_id,
        "generated_at": "2026-10-03T12:05:00+00:00",
        "domain_ok": True, "domain_mismatches": [],
        "configuration": {
            "election_id": "senate-2026", "as_of": "2026-09-27", **configuration,
        },
        "comparison": {"ok": True, "checks": [{"name": "synthetic", "ok": True}]},
        "lite_hash_seal": {
            "ok": True, "run_id": run_id, "draws_ok": True,
            "forecast_hash_expected": forecast_sha,
            "forecast_hash_actual": forecast_sha,
        },
    }
    independent_path = _write(source / "independent_rebuild_latest.json", independent)
    completed_model = {"run_id": run_id, "manifest_path": manifest_path}
    assert _verify_independent_rebuild_artifact(
        independent_path, completed_model=completed_model, source=source,
    )["ok"] is True

    independent["configuration"]["with_markets"] = True
    _write(independent_path, independent)
    with pytest.raises(ValueError, match="sealed publication configuration"):
        _verify_independent_rebuild_artifact(
            independent_path, completed_model=completed_model, source=source,
        )


def test_independent_rebuild_replays_publication_configuration(tmp_path: Path, monkeypatch) -> None:
    release = tmp_path / "release"
    release.mkdir()
    sealed = {
        "run_id": "synthetic-publication-run",
        "election_id": "synthetic-election",
        "forecast_as_of": "2026-09-27",
        "method": "ensemble_stack",
        "publishable": True,
        "diagnostics": {
            "core_method": "pymc", "draws": 2000, "tune": 4000,
            "chains": 4, "target_accept": 0.99,
        },
        "chamber": {"p_dem_majority": 0.5, "expected_dem_seats": 50.0},
        "races": [],
    }
    manifest = {
        "run_id": sealed["run_id"],
        "election_id": sealed["election_id"],
        "forecast_as_of": sealed["forecast_as_of"],
        "configuration": {
            "method": "pymc", "ensemble": True, "draws": 2000,
            "tune": 4000, "chains": 4, "seed": 17,
        },
        "domain_hashes": {},
    }
    _write(release / "forecast.json", sealed)
    _write(release / "run_manifest.json", manifest)
    captured: dict = {}

    def fake_run_forecast(**kwargs):
        captured.update(kwargs)
        return {"artifact": sealed}

    monkeypatch.setattr("midterms.ops.reproducibility.verify_rebuild", lambda **_: {
        "ok": True, "run_id": sealed["run_id"],
    })
    monkeypatch.setattr("midterms.ops.reproducibility.snapshot_domain_hashes", lambda: {})
    run_forecast_module = importlib.import_module("midterms.pipeline.run_forecast")
    monkeypatch.setattr(run_forecast_module, "run_forecast", fake_run_forecast)
    result = independent_rebuild(
        run_id=sealed["run_id"], release_dir=release,
        out_dir=tmp_path / "rebuilt", write_artifact=False,
    )
    assert result["ok"] is True
    assert captured["require_publishable"] is True
    assert captured["allow_non_publication"] is False
    assert captured["target_accept"] == 0.99
    assert captured["tune"] == 4000
    assert captured["chains"] == 4
