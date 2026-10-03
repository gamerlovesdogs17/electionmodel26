"""Restore an expensive rebuild checkpoint without trusting unrelated artifacts.

Failed research runs upload the whole artifacts directory for diagnosis.  A
resume operation deliberately imports only the selection/canonical OOF files
and verifies their semantic lineage before overwriting the working checkout.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.evidence.evidence_bundle import verify_evidence_bundle
from midterms.validation.artifact_lineage import frozen_index_semantic_sha256
from midterms.validation.nested_component_loo import _draws_fingerprint
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
OPTIONAL_CHECKPOINT_FILES = ("posterior_predictive_oof_latest.json",)


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

    canonical, _ = _verify_report_pair(
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

    optional = [name for name in OPTIONAL_CHECKPOINT_FILES if (source / name).is_file()]
    if "posterior_predictive_oof_latest.json" in optional:
        posterior = _read(source / "posterior_predictive_oof_latest.json")
        _require(posterior.get("model_version") == MODEL_VERSION, "posterior OOF checkpoint is stale")
        _require(
            posterior.get("source_nested_sha256") == file_sha256(paths["nested_component_loo_canonical.json"]),
            "posterior OOF checkpoint source changed",
        )
        _require(
            posterior.get("source_frozen_draws_sha256") == canonical.get("frozen_draws_sha256"),
            "posterior OOF checkpoint draw lineage changed",
        )

    destination.mkdir(parents=True, exist_ok=True)
    restored = []
    for name in (*CHECKPOINT_FILES, *optional):
        shutil.copy2(source / name, destination / name)
        restored.append(name)
    return {
        "ok": True,
        "schema_version": "rebuild-checkpoint-restore-v1",
        "model_version": MODEL_VERSION,
        "evidence_bundle_id": bundle.get("evidence_bundle_id"),
        "selected_poll_structure_id": candidate.get("selected_poll_structure_id"),
        "canonical_failures_to_repair": canonical.get("failures") or [],
        "restored_files": restored,
    }
