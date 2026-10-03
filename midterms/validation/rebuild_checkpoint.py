"""Restore expensive rebuild work without trusting unrelated artifacts.

Failed research runs upload their model artifacts for diagnosis.  A resume
always verifies selection/canonical OOF semantic lineage.  It may also restore
a completed publication fit, but only after checking its sealed evidence,
validated model spec, stack/crossfit lineage, numerical floors, output hashes,
and release archive.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MODEL_VERSION,
    PRODUCTION_CHAINS,
    PRODUCTION_DRAWS,
    PRODUCTION_JOINT_SIMS,
    PRODUCTION_TARGET_ACCEPT,
    PRODUCTION_TUNE,
)
from midterms.evidence.evidence_bundle import verify_evidence_bundle
from midterms.validation.artifact_lineage import frozen_index_semantic_sha256
from midterms.validation.nested_component_loo import (
    REPAIR_CHECKPOINT_DIRNAME,
    _draws_fingerprint,
    _write_posterior_predictive_oof_artifact,
    read_repair_checkpoint,
    validate_frozen_oof_case_coverage,
    validate_repair_checkpoint,
)
from midterms.validation.validated_model_spec import (
    CANONICAL_OOF_PHASE,
    SELECTION_OOF_PHASE,
    file_sha256,
    load_candidate_model_spec,
    load_validated_model_spec,
    verify_validated_spec_artifacts,
)

CHECKPOINT_FILES = (
    "nested_component_loo_selection.json",
    "nested_component_loo_selection_frozen.json",
    "poll_structure_crossfit_latest.json",
    "validated_model_spec_candidate.json",
    "nested_component_loo_canonical.json",
    "nested_component_loo_canonical_frozen.json",
)

COMPLETED_MODEL_ARTIFACTS = (
    "stack_weights_oof.json",
    "stack_reliability_crossfit_latest.json",
    "joint_oof_scores_latest.json",
    "validated_model_spec_latest.json",
    "forecast_latest.json",
    "evidence_eligibility_latest.json",
    "race_decomposition_latest.json",
    "prior_predictive_latest.json",
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_source_path(source_root: Path, reference: str) -> Path:
    """Resolve a portable manifest path without allowing archive traversal."""
    path = Path(str(reference))
    _require(not path.is_absolute() and ".." not in path.parts, "run manifest path is unsafe")
    resolved = (source_root / path).resolve()
    _require(resolved.is_relative_to(source_root.resolve()), "run manifest path escapes checkpoint")
    return resolved


def _verify_completed_model(
    *, source_root: Path, source: Path, bundle: dict[str, Any]
) -> dict[str, Any]:
    """Verify a completed publication fit before allowing it to be resumed."""
    paths = {name: source / name for name in COMPLETED_MODEL_ARTIFACTS}
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    _require(not missing, "completed model checkpoint is incomplete: " + ", ".join(missing))

    forecast = _read(paths["forecast_latest.json"])
    run_id = str(forecast.get("run_id") or "")
    _require(bool(run_id), "completed model forecast lacks run_id")
    _require(forecast.get("model_version") == MODEL_VERSION, "completed forecast model version is stale")
    _require(
        forecast.get("forecast_as_of") == bundle.get("as_of"),
        "completed forecast as-of differs from the current evidence bundle",
    )
    _require(
        forecast.get("evidence_bundle_id") == bundle.get("evidence_bundle_id")
        and forecast.get("evidence_bundle_sha256") == bundle.get("evidence_bundle_sha256"),
        "completed forecast evidence bundle differs from the current sealed bundle",
    )
    _require(
        (forecast.get("snapshot_ids") or {}).get("evidence") == bundle.get("current_snapshot_id"),
        "completed forecast evidence snapshot differs from the current sealed bundle",
    )
    _require(
        forecast.get("publishable") is True
        and forecast.get("run_class") == "publication"
        and forecast.get("publication_surface") == "research_only",
        "completed forecast is not a publication-quality research artifact",
    )

    diag = forecast.get("diagnostics") or {}
    _require(int(diag.get("draws") or 0) >= PRODUCTION_DRAWS, "completed forecast draw floor failed")
    _require(int(diag.get("tune") or 0) >= PRODUCTION_TUNE, "completed forecast tune floor failed")
    _require(int(diag.get("chains") or 0) >= PRODUCTION_CHAINS, "completed forecast chain floor failed")
    _require(
        float(diag.get("target_accept") or 0) >= PRODUCTION_TARGET_ACCEPT,
        "completed forecast target_accept floor failed",
    )
    _require(
        int(diag.get("n_posterior_samples") or 0) >= PRODUCTION_DRAWS * PRODUCTION_CHAINS,
        "completed forecast posterior sample floor failed",
    )
    _require(
        int(diag.get("n_joint_sims") or 0) >= PRODUCTION_JOINT_SIMS,
        "completed forecast joint simulation floor failed",
    )
    numerical = forecast.get("numerical_quality") or {}
    numerical_checks = numerical.get("checks") or []
    _require(
        numerical.get("ok") is True
        and bool(numerical_checks)
        and all(check.get("ok") is True for check in numerical_checks),
        "completed forecast numerical-quality checks failed",
    )

    spec, _ = load_validated_model_spec(
        path=paths["validated_model_spec_latest.json"],
        expected_evidence_bundle_id=str(bundle.get("evidence_bundle_id")),
        expected_evidence_bundle_sha256=str(bundle.get("evidence_bundle_sha256")),
    )
    verify_validated_spec_artifacts(spec, artifacts_dir=source)
    _require(
        forecast.get("validated_model_spec_sha256") == spec.get("spec_sha256"),
        "completed forecast validated-model-spec identity changed",
    )
    _require(
        forecast.get("stack_artifact_sha256") == file_sha256(paths["stack_weights_oof.json"]),
        "completed forecast stack artifact identity changed",
    )
    from midterms.validation.stack_reliability_crossfit import validate_crossfit_artifact

    crossfit = _read(paths["stack_reliability_crossfit_latest.json"])
    crossfit_check = validate_crossfit_artifact(
        crossfit,
        nested_path=source / "nested_component_loo_canonical.json",
        stack_path=paths["stack_weights_oof.json"],
    )
    _require(bool(crossfit_check.get("ok")), "completed crossfit lineage changed")
    _require(_read(paths["joint_oof_scores_latest.json"]).get("ok") is True, "joint OOF scores failed")

    eligibility = _read(paths["evidence_eligibility_latest.json"])
    _require(
        eligibility.get("model_version") == MODEL_VERSION
        and eligibility.get("forecast_run_id") == run_id
        and eligibility.get("snapshot_id") == bundle.get("current_snapshot_id")
        and eligibility.get("as_of") == bundle.get("as_of")
        and eligibility.get("publishable") is True,
        "completed forecast eligibility lineage changed",
    )

    manifest_path = source_root / "data" / "manifests" / f"run_{run_id}.json"
    _require(manifest_path.is_file(), "completed model run manifest is missing")
    manifest = _read(manifest_path)
    _require(
        manifest.get("run_id") == run_id
        and manifest.get("model_version") == MODEL_VERSION
        and manifest.get("forecast_as_of") == bundle.get("as_of"),
        "completed model run manifest lineage changed",
    )
    forecast_path = _safe_source_path(source_root, (manifest.get("paths") or {}).get("forecast", ""))
    draws_path = _safe_source_path(source_root, (manifest.get("paths") or {}).get("draws", ""))
    _require(forecast_path.is_file() and draws_path.is_file(), "completed forecast outputs are missing")
    _require(
        _sha256(forecast_path) == (manifest.get("output_hashes") or {}).get("forecast_json")
        and _sha256(draws_path) == (manifest.get("output_hashes") or {}).get("draws"),
        "completed forecast output hashes changed",
    )
    _require(
        forecast_path.read_bytes() == paths["forecast_latest.json"].read_bytes(),
        "completed forecast latest/run-specific content differs",
    )
    release_dir = source_root / "data" / "releases" / run_id
    _require(
        (release_dir / "forecast.json").is_file()
        and (release_dir / "run_manifest.json").is_file()
        and (release_dir / "forecast.json").read_bytes() == forecast_path.read_bytes()
        and (release_dir / "run_manifest.json").read_bytes() == manifest_path.read_bytes(),
        "completed release archive differs from its run artifacts",
    )
    return {
        "run_id": run_id,
        "manifest_path": manifest_path,
        "forecast_path": forecast_path,
        "draws_path": draws_path,
        "release_dir": release_dir,
        "source_code_commit": manifest.get("code_commit"),
    }


def _verify_report_pair(
    report_path: Path,
    index_path: Path,
    *,
    phase: str,
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = _read(report_path)
    index = _read(index_path)
    label = "canonical OOF" if phase == CANONICAL_OOF_PHASE else "selection OOF"
    _require(report.get("model_version") == MODEL_VERSION, f"{label} model version is stale")
    _require(index.get("model_version") == MODEL_VERSION, f"{label} index model version is stale")
    _require(report.get("validation_phase") == phase, f"{label} validation phase changed")
    _require(index.get("validation_phase") == phase, f"{label} index phase changed")
    _require(
        report.get("evidence_bundle_id") == bundle.get("evidence_bundle_id")
        and report.get("evidence_bundle_sha256") == bundle.get("evidence_bundle_sha256"),
        f"{label} evidence bundle differs from the current sealed bundle",
    )
    _require(
        report.get("poll_structure_config_id") == index.get("poll_structure_config_id"),
        f"{label} poll-structure identity differs from its freeze index",
    )
    _require(
        report.get("frozen_draws_sha256") == _draws_fingerprint(report.get("oof_draws") or {}),
        f"{label} predictive draws changed",
    )
    _require(
        report.get("frozen_index_semantic_sha256") == frozen_index_semantic_sha256(index),
        f"{label} freeze index changed",
    )
    validate_frozen_oof_case_coverage(report)
    entries = list(index.get("entries") or [])
    _require(index.get("n") == len(entries), f"{label} freeze index count changed")
    failed_index = sorted(
        (
            int(row.get("holdout_year", 0)),
            int(row.get("lead_days", 0)),
            str(row.get("component", "")),
        )
        for row in entries
        if row.get("status") == "failed"
    )
    failed_report = sorted(
        (int(row.get("year", 0)), int(row.get("lead", 0)), str(row.get("component", "")))
        for row in (report.get("failures") or [])
    )
    _require(failed_index == failed_report, f"{label} failure index differs from its report")
    return report, index


def restore_rebuild_checkpoint(
    *,
    source_root: str | Path,
    evidence_bundle_path: str | Path,
    artifacts_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Verify and restore reusable OOF and, when complete, publication outputs."""
    source_root = Path(source_root)
    source = source_root / "data" / "artifacts"
    if not source.is_dir():
        source = source_root
    destination = Path(artifacts_dir or ARTIFACTS_DIR)
    paths = {name: source / name for name in CHECKPOINT_FILES}
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    if missing:
        raise FileNotFoundError(f"rebuild checkpoint is incomplete: {', '.join(missing)}")

    bundle = _read(Path(evidence_bundle_path))
    verification = verify_evidence_bundle(bundle)
    _require(bool(verification.get("ok")), "current evidence bundle is invalid")
    _require(bundle.get("model_version") == MODEL_VERSION, "current evidence bundle model version is stale")

    selection, _ = _verify_report_pair(
        paths["nested_component_loo_selection.json"],
        paths["nested_component_loo_selection_frozen.json"],
        phase=SELECTION_OOF_PHASE,
        bundle=bundle,
    )
    crossfit_path = paths["poll_structure_crossfit_latest.json"]
    crossfit = _read(crossfit_path)
    selection_sha = file_sha256(paths["nested_component_loo_selection.json"])
    _require(crossfit.get("model_version") == MODEL_VERSION, "poll-structure selection model version is stale")
    _require(crossfit.get("source_validation_phase") == SELECTION_OOF_PHASE, "poll-structure selection phase changed")
    _require(crossfit.get("source_nested_loo_sha256") == selection_sha, "poll-structure selection source changed")
    _require(
        crossfit.get("source_frozen_draws_sha256") == selection.get("frozen_draws_sha256"),
        "poll-structure selection draw lineage changed",
    )
    _require(
        crossfit.get("source_evidence_bundle_id") == bundle.get("evidence_bundle_id")
        and crossfit.get("source_evidence_bundle_sha256") == bundle.get("evidence_bundle_sha256"),
        "poll-structure selection evidence bundle changed",
    )

    candidate_path = paths["validated_model_spec_candidate.json"]
    candidate = load_candidate_model_spec(candidate_path)
    _require(
        candidate.get("evidence_bundle_id") == bundle.get("evidence_bundle_id")
        and candidate.get("evidence_bundle_sha256") == bundle.get("evidence_bundle_sha256"),
        "candidate model spec evidence bundle changed",
    )
    _require(candidate.get("poll_structure_selection_sha256") == file_sha256(crossfit_path), "candidate selection artifact changed")
    _require(
        candidate.get("poll_structure_selection_source_nested_sha256") == selection_sha,
        "candidate selection OOF changed",
    )

    canonical, canonical_index = _verify_report_pair(
        paths["nested_component_loo_canonical.json"],
        paths["nested_component_loo_canonical_frozen.json"],
        phase=CANONICAL_OOF_PHASE,
        bundle=bundle,
    )
    _require(canonical.get("stack_training_protocol") == "formal_60_30_v1", "canonical OOF protocol changed")
    _require(
        canonical.get("model_spec_candidate_sha256") == file_sha256(candidate_path),
        "canonical OOF candidate model spec changed",
    )
    _require(
        canonical.get("poll_structure_config_id") == candidate.get("selected_poll_structure_id"),
        "canonical OOF selected poll structure changed",
    )

    repair_checkpoints: list[Path] = []
    repair_source_dir = source / REPAIR_CHECKPOINT_DIRNAME
    if repair_source_dir.is_dir():
        for checkpoint_path in sorted(repair_source_dir.glob("*.json.gz")):
            payload = read_repair_checkpoint(checkpoint_path)
            frozen = payload.get("frozen_prediction") or {}
            matching = [entry for entry in canonical_index.get("entries") or [] if (
                entry.get("status") == "failed"
                and entry.get("component") == frozen.get("component")
                and entry.get("holdout_year") == frozen.get("holdout_year")
                and entry.get("lead_days") == frozen.get("lead_days")
            )]
            _require(
                len(matching) == 1,
                f"OOF repair checkpoint target is not a current failed fold: {checkpoint_path.name}",
            )
            original = matching[0]
            year_key = str(original["holdout_year"])
            lead_key = str(original["lead_days"])
            expected_snapshot = (bundle.get("historical_snapshot_ids") or {}).get(
                f"senate-{year_key}-lead-{lead_key}"
            )
            _require(bool(expected_snapshot), "evidence bundle lacks repair checkpoint snapshot")
            validate_repair_checkpoint(
                payload,
                index=canonical_index,
                original_entry=original,
                recovery_settings=payload.get("recovery_settings") or {},
                expected_snapshot_id=str(expected_snapshot),
                expected_prior_sha256=(
                    canonical.get("prior_snapshot_sha256_by_fold_lead") or {}
                ).get(year_key, {}).get(lead_key),
                expected_presidential_sha256=(
                    canonical.get("presidential_source_sha256_by_fold_lead") or {}
                ).get(year_key, {}).get(lead_key),
            )
            repair_checkpoints.append(checkpoint_path)

    completed_model: dict[str, Any] | None = None
    completed_model_reason = "completed publication forecast not present in checkpoint"
    if (source / "forecast_latest.json").is_file():
        try:
            completed_model = _verify_completed_model(
                source_root=source_root,
                source=source,
                bundle=bundle,
            )
            completed_model_reason = "verified publication forecast restored"
        except (ValueError, FileNotFoundError, KeyError, TypeError) as exc:
            # OOF recovery remains useful.  A partial or stale forecast is never
            # copied and the workflow will rerun the downstream production steps.
            completed_model_reason = str(exc)

    destination.mkdir(parents=True, exist_ok=True)
    restored = []
    for name in CHECKPOINT_FILES:
        shutil.copy2(source / name, destination / name)
        restored.append(name)
    if repair_checkpoints:
        repair_destination = destination / REPAIR_CHECKPOINT_DIRNAME
        repair_destination.mkdir(parents=True, exist_ok=True)
        for checkpoint_path in repair_checkpoints:
            shutil.copy2(checkpoint_path, repair_destination / checkpoint_path.name)
            restored.append(f"{REPAIR_CHECKPOINT_DIRNAME}/{checkpoint_path.name}")
    # This diagnostic is a deterministic derivative of the canonical pair.
    # Rebuild it rather than trusting a stale copy left by an interrupted run.
    _write_posterior_predictive_oof_artifact(
        out_path=destination / "nested_component_loo_canonical.json",
        report=canonical,
        entries=canonical_index["entries"],
    )
    restored.append("posterior_predictive_oof_latest.json")
    if completed_model is not None:
        for name in COMPLETED_MODEL_ARTIFACTS:
            shutil.copy2(source / name, destination / name)
            restored.append(name)
        for path_key in ("forecast_path", "draws_path"):
            source_path = Path(completed_model[path_key])
            shutil.copy2(source_path, destination / source_path.name)
            restored.append(source_path.name)

        # The real workflow points artifacts_dir at <repo>/data/artifacts.  In
        # that case restore the manifest/release/web copies needed by numerical,
        # independent-rebuild, coherence, and deploy steps.  Unit-test scratch
        # directories remain isolated.
        if destination.name == "artifacts" and destination.parent.name == "data":
            target_root = destination.parent.parent
            manifest_destination = target_root / "data" / "manifests"
            manifest_destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                completed_model["manifest_path"],
                manifest_destination / Path(completed_model["manifest_path"]).name,
            )
            release_destination = target_root / "data" / "releases" / completed_model["run_id"]
            shutil.copytree(completed_model["release_dir"], release_destination, dirs_exist_ok=True)
            web_destination = target_root / "web" / "public" / "data" / "forecast_latest.json"
            web_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / "forecast_latest.json", web_destination)
            restored.extend([
                f"data/manifests/{Path(completed_model['manifest_path']).name}",
                f"data/releases/{completed_model['run_id']}/",
                "web/public/data/forecast_latest.json",
            ])
    return {
        "ok": True,
        "schema_version": "rebuild-checkpoint-restore-v2",
        "model_version": MODEL_VERSION,
        "evidence_bundle_id": bundle.get("evidence_bundle_id"),
        "selected_poll_structure_id": candidate.get("selected_poll_structure_id"),
        "canonical_failures_to_repair": canonical.get("failures") or [],
        "forecast_restored": completed_model is not None,
        "forecast_restore_reason": completed_model_reason,
        "forecast_run_id": (completed_model or {}).get("run_id"),
        "forecast_source_code_commit": (completed_model or {}).get("source_code_commit"),
        "restored_files": restored,
    }
