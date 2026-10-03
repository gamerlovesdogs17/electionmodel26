"""Restore an expensive rebuild checkpoint without trusting unrelated artifacts.

Failed research runs upload the whole artifacts directory for diagnosis.  A
resume operation deliberately imports only the selection/canonical OOF files
and verifies their semantic lineage before overwriting the working checkout.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
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
)

CHECKPOINT_FILES = (
    "nested_component_loo_selection.json",
    "nested_component_loo_selection_frozen.json",
    "poll_structure_crossfit_latest.json",
    "validated_model_spec_candidate.json",
    "nested_component_loo_canonical.json",
    "nested_component_loo_canonical_frozen.json",
)
def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


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
    """Verify and restore selection/canonical OOF files from a failed run."""
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
    return {
        "ok": True,
        "schema_version": "rebuild-checkpoint-restore-v1",
        "model_version": MODEL_VERSION,
        "evidence_bundle_id": bundle.get("evidence_bundle_id"),
        "selected_poll_structure_id": candidate.get("selected_poll_structure_id"),
        "canonical_failures_to_repair": canonical.get("failures") or [],
        "restored_files": restored,
    }
