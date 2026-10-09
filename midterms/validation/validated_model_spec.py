"""Fail-closed lineage contract for the empirically selected model.

The candidate specification freezes the poll measurement structure selected by
the first historical pass.  The final specification is created only after a
second, canonical OOF pass and its stack/calibration artifacts have been bound
to that exact structure and evidence bundle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.model.poll_structure import PollStructureConfig

CANDIDATE_SPEC_SCHEMA = "validated-model-spec-candidate-v1"
VALIDATED_SPEC_SCHEMA = "validated-model-spec-v2"
CANONICAL_OOF_PHASE = "canonical_poll_structure"
SELECTION_OOF_PHASE = "poll_structure_selection"

# Candidate / selection / canonical OOF identity is ordinary-statistical only.
# Exceptional adapters bind later into the finalized release model spec and are
# verified by exceptional_model_lineage — they must not enter ordinary OOF caches.
ORDINARY_STATISTICAL_SPEC_FIELDS = (
    "schema_version",
    "model_version",
    "evidence_bundle_id",
    "evidence_bundle_sha256",
    "selected_structure_id",
    "selected_poll_structure",
    "selected_poll_structure_id",
    "historical_cycles",
    "lead_cutoffs_days",
    # Ordinary OOF consumes derived race-id finance + personal incumbency.
    # Exceptional adapters remain excluded from this identity.
    "historical_structural_feature_schema",
    # Same-family G8 keep/drop decisions for hierarchical structural terms.
    "structural_feature_enables",
    # Same-family selection: chosen state-space configuration (fund_pull,
    # flat_prior, process_sd_per_sqrt_day).  Changing selection changes SHA256.
    "selected_state_space_config",
    # Same-family selection: chosen national-environment (ridge-fundamentals
    # coefficient) configuration (generic_ballot_enabled, approval_enabled,
    # midterm_outparty_enabled, income_enabled).
    "selected_national_environment_config",
)


DEFAULT_STRUCTURAL_FEATURE_ENABLES = {
    "similarity_terminal": True,
    "terminal_race": True,
}


def structural_feature_enables_from_g8(
    recommendations: dict[str, Any] | None,
) -> dict[str, bool]:
    """Enable hierarchical structural terms only when selection OOF votes keep."""

    recs = recommendations or {}
    enables: dict[str, bool] = {}
    for feature in DEFAULT_STRUCTURAL_FEATURE_ENABLES:
        recommend = (recs.get(feature) or {}).get("recommend")
        enables[feature] = recommend == "keep"
    return enables


def coerce_structural_feature_enables(value: Any | None) -> dict[str, bool]:
    raw = dict(value or {})
    return {
        feature: bool(raw.get(feature, default))
        for feature, default in DEFAULT_STRUCTURAL_FEATURE_ENABLES.items()
    }

STRUCTURE_CONFIGS: dict[str, dict[str, bool]] = {
    "pymc": {},
    "hier_plus_study_effect": {"study_effect": True},
    "hier_plus_sponsor_effect": {"sponsor_effect": True},
    "hier_plus_questionnaire_effect": {"questionnaire_effect": True},
}


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def poll_structure_from_dict(value: dict[str, Any] | None) -> PollStructureConfig:
    allowed = {field.name for field in fields(PollStructureConfig)}
    return PollStructureConfig(**{
        key: item for key, item in dict(value or {}).items() if key in allowed
    })


def poll_structure_for_selection(identifier: str) -> PollStructureConfig:
    if identifier not in STRUCTURE_CONFIGS:
        raise ValueError(f"unsupported selected poll structure: {identifier}")
    return PollStructureConfig().merged(STRUCTURE_CONFIGS[identifier])


def poll_structure_identity(config: PollStructureConfig | dict[str, Any]) -> str:
    cfg = PollStructureConfig.coerce(config).to_dict()
    return f"psc-{canonical_sha256(cfg)[:16]}"


def ordinary_statistical_spec_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Subset of a candidate/final spec that determines ordinary OOF reuse."""

    return {key: payload.get(key) for key in ORDINARY_STATISTICAL_SPEC_FIELDS}


def ordinary_statistical_spec_sha256(payload: dict[str, Any]) -> str:
    return canonical_sha256(ordinary_statistical_spec_identity(payload))


def write_candidate_model_spec(
    *,
    selection_path: str | Path | None = None,
    evidence_bundle_path: str | Path,
    source_readiness_path: str | Path | None = None,
    nested_selection_path: str | Path | None = None,
    out_path: str | Path | None = None,
    code_commit_sha: str | None = None,
) -> dict[str, Any]:
    selection_path = Path(selection_path or ARTIFACTS_DIR / "poll_structure_crossfit_latest.json")
    evidence_bundle_path = Path(evidence_bundle_path)
    source_readiness_path = Path(
        source_readiness_path or ARTIFACTS_DIR / "source_readiness_latest.json"
    )
    nested_selection_path = Path(
        nested_selection_path or ARTIFACTS_DIR / "nested_component_loo_selection.json"
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    nested_selection = json.loads(nested_selection_path.read_text(encoding="utf-8"))
    bundle = json.loads(evidence_bundle_path.read_text(encoding="utf-8"))
    readiness = json.loads(source_readiness_path.read_text(encoding="utf-8"))
    if selection.get("model_version") != MODEL_VERSION:
        raise ValueError("poll-structure selection model version is stale")
    if bundle.get("model_version") != MODEL_VERSION:
        raise ValueError("evidence bundle model version is stale")
    if readiness.get("model_version") != MODEL_VERSION:
        raise ValueError("source readiness model version is stale")
    if not readiness.get("ready_for_expensive_rebuild"):
        raise ValueError("source readiness is not green for the expensive rebuild")
    if selection.get("source_validation_phase") != SELECTION_OOF_PHASE:
        raise ValueError("poll-structure selection does not come from the selection OOF pass")
    if selection.get("source_evidence_bundle_id") != bundle.get("evidence_bundle_id"):
        raise ValueError("poll-structure selection evidence bundle differs from candidate spec")
    if selection.get("source_evidence_bundle_sha256") != bundle.get("evidence_bundle_sha256"):
        raise ValueError("poll-structure selection evidence bundle hash differs from candidate spec")
    if nested_selection.get("validation_phase") != SELECTION_OOF_PHASE:
        raise ValueError("nested selection OOF phase mismatch while freezing candidate spec")
    nested_sha = file_sha256(nested_selection_path)
    if selection.get("source_nested_loo_sha256") != nested_sha:
        raise ValueError("poll-structure crossfit is not bound to the selection nested OOF")
    folds = selection.get("outer_folds") or []
    if (selection.get("freeze_before_truth") is not True or len(folds) != 4
            or any(fold.get("heldout_truth_used_for_selection") is not False for fold in folds)):
        raise ValueError("poll-structure selection is not a complete freeze-before-truth crossfit")
    recommendation = selection.get("final_production_candidate_recommendation") or {}
    selected_id = str(recommendation.get("selected_structure") or "")
    config = poll_structure_for_selection(selected_id)
    config_dict = config.to_dict()
    recommendation_config_id = recommendation.get("selected_poll_structure_id")
    if recommendation_config_id != poll_structure_identity(config):
        raise ValueError("poll-structure recommendation config identity changed")
    structural_feature_enables = structural_feature_enables_from_g8(
        nested_selection.get("g8_recommendations")
    )

    # Same-family configuration selection: choose one state-space config and one
    # national-environment config from the selection OOF challenger scores.
    from midterms.validation.same_family_selection import select_same_family_configs
    same_family_selection = select_same_family_configs(nested_selection)

    payload = {
        "schema_version": CANDIDATE_SPEC_SCHEMA,
        "model_version": MODEL_VERSION,
        "status": "candidate_pending_canonical_oof",
        "evidence_bundle_id": bundle.get("evidence_bundle_id"),
        "evidence_bundle_sha256": bundle.get("evidence_bundle_sha256"),
        "source_readiness_sha256": file_sha256(source_readiness_path),
        "poll_structure_selection_sha256": file_sha256(selection_path),
        "poll_structure_selection_source_nested_sha256": selection.get(
            "source_nested_loo_sha256"
        ),
        "selected_structure_id": selected_id,
        "selected_poll_structure": config_dict,
        "selected_poll_structure_id": poll_structure_identity(config),
        "historical_cycles": [2018, 2020, 2022, 2024],
        "lead_cutoffs_days": [60, 30],
        "historical_structural_feature_schema": "historical-structural-features-v1",
        "structural_feature_enables": structural_feature_enables,
        # Same-family config selections (part of ordinary-statistical-spec identity)
        "selected_state_space_config": same_family_selection["selected_state_space_config"],
        "selected_national_environment_config": same_family_selection["selected_national_environment_config"],
        "selected_state_space_name": same_family_selection["selected_state_space_name"],
        "selected_national_environment_name": same_family_selection["selected_national_environment_name"],
        "same_family_selection_audit": same_family_selection["same_family_selection_audit"],
        "code_commit_sha": code_commit_sha,
        "production_research_eligible": False,
        "next_required_phase": CANONICAL_OOF_PHASE,
        "ordinary_statistical_only": True,
    }
    payload["ordinary_statistical_spec_sha256"] = ordinary_statistical_spec_sha256(payload)
    payload["spec_sha256"] = canonical_sha256(payload)
    path = Path(out_path or ARTIFACTS_DIR / "validated_model_spec_candidate.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**payload, "path": str(path)}


def load_candidate_model_spec(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = payload.pop("spec_sha256", None)
    actual = canonical_sha256(payload)
    payload["spec_sha256"] = expected
    if payload.get("schema_version") != CANDIDATE_SPEC_SCHEMA or expected != actual:
        raise ValueError("validated model candidate spec identity is invalid")
    if payload.get("model_version") != MODEL_VERSION:
        raise ValueError("validated model candidate spec model version is stale")
    config = poll_structure_from_dict(payload.get("selected_poll_structure"))
    if payload.get("selected_poll_structure_id") != poll_structure_identity(config):
        raise ValueError("validated model candidate poll structure identity changed")
    payload["structural_feature_enables"] = coerce_structural_feature_enables(
        payload.get("structural_feature_enables")
    )
    return payload


def finalize_validated_model_spec(
    *,
    candidate_path: str | Path,
    canonical_oof_path: str | Path,
    stack_path: str | Path,
    calibration_path: str | Path,
    out_path: str | Path | None = None,
    code_commit_sha: str | None = None,
    exceptional_model_lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_path = Path(candidate_path)
    canonical_oof_path = Path(canonical_oof_path)
    stack_path = Path(stack_path)
    calibration_path = Path(calibration_path)
    candidate = load_candidate_model_spec(candidate_path)
    canonical = json.loads(canonical_oof_path.read_text(encoding="utf-8"))
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    expected_config_id = candidate["selected_poll_structure_id"]
    candidate_sha = file_sha256(candidate_path)
    canonical_sha = file_sha256(canonical_oof_path)
    checks = {
        "canonical_model_version": canonical.get("model_version") == MODEL_VERSION,
        "canonical_phase": canonical.get("validation_phase") == CANONICAL_OOF_PHASE,
        "canonical_poll_structure": canonical.get("poll_structure_config_id") == expected_config_id,
        "canonical_candidate_spec": canonical.get("model_spec_candidate_sha256") == candidate_sha,
        "canonical_bundle": canonical.get("evidence_bundle_id") == candidate.get("evidence_bundle_id"),
        "canonical_bundle_hash": canonical.get("evidence_bundle_sha256") == candidate.get("evidence_bundle_sha256"),
        "canonical_complete": not bool(canonical.get("failures")),
        "stack_model_version": stack.get("source_model_version") == MODEL_VERSION,
        "stack_uses_canonical_oof": stack.get("source_nested_sha256") == canonical_sha,
        "stack_poll_structure": stack.get("source_poll_structure_config_id") == expected_config_id,
        "stack_candidate_spec": stack.get("source_model_spec_candidate_sha256") == candidate_sha,
        "stack_bundle": stack.get("source_evidence_bundle_id") == candidate.get("evidence_bundle_id"),
        "stack_bundle_hash": stack.get("source_evidence_bundle_sha256") == candidate.get("evidence_bundle_sha256"),
        "stack_reproduced": bool((stack.get("reproduction") or {}).get("ok")),
        "calibration_model_version": calibration.get("model_version") == MODEL_VERSION,
        "calibration_uses_canonical_draws": (calibration.get("source") or {}).get(
            "frozen_draws_sha256"
        ) == canonical.get("frozen_draws_sha256"),
    }
    if not all(checks.values()):
        failed = [name for name, ok in checks.items() if not ok]
        raise ValueError("validated model spec lineage failed: " + ", ".join(failed))
    if exceptional_model_lineage is None:
        from midterms.validation.exceptional_model_lineage import (
            build_exceptional_model_lineage,
        )

        exceptional_model_lineage = build_exceptional_model_lineage()
    exceptional_sha = exceptional_model_lineage.get("lineage_sha256")
    from midterms.validation.exceptional_model_lineage import (
        LINEAGE_SCHEMA_VERSION,
    )
    from midterms.validation.exceptional_model_lineage import (
        canonical_sha256 as exceptional_canonical_sha256,
    )

    exceptional_semantic = dict(exceptional_model_lineage)
    exceptional_semantic.pop("lineage_sha256", None)
    if (
        exceptional_model_lineage.get("schema_version") != LINEAGE_SCHEMA_VERSION
        or exceptional_sha != exceptional_canonical_sha256(exceptional_semantic)
    ):
        raise ValueError("exceptional model lineage identity is invalid")
    coverage_summary = (
        (exceptional_model_lineage.get("forecast_coverage") or {}).get("summary") or {}
    )
    n_fail_closed = int(coverage_summary.get("n_fail_closed_multiway") or 0)
    n_fail = int(coverage_summary.get("n_fail") or 0)
    # Fail-closed multiway races may leave forecast_complete=false without
    # blocking research-model validation of the ordinary + exceptional paths.
    forecast_coverage_ok = bool(coverage_summary.get("forecast_complete")) or (
        bool(coverage_summary.get("evidence_ready"))
        and n_fail_closed > 0
        and n_fail == n_fail_closed
    )
    checks.update({
        "exceptional_models_semantically_valid": True,
        "forecast_coverage_complete": forecast_coverage_ok,
        "historical_evidence_equivalent": (
            (exceptional_model_lineage.get("historical_evidence_equivalence") or {})
            .get("classification") == "historically_equivalent"
        ),
    })
    if not all(checks.values()):
        failed = [name for name, ok in checks.items() if not ok]
        raise ValueError("validated model spec lineage failed: " + ", ".join(failed))
    stack_candidates = sorted((stack.get("stack_weights_production") or {}).keys())
    payload = {
        "schema_version": VALIDATED_SPEC_SCHEMA,
        "model_version": MODEL_VERSION,
        "status": "validated_research_model",
        "evidence_bundle_id": candidate["evidence_bundle_id"],
        "evidence_bundle_sha256": candidate["evidence_bundle_sha256"],
        "source_readiness_sha256": candidate["source_readiness_sha256"],
        "candidate_spec_sha256": candidate_sha,
        "poll_structure_selection_sha256": candidate["poll_structure_selection_sha256"],
        "selected_structure_id": candidate["selected_structure_id"],
        "selected_poll_structure": candidate["selected_poll_structure"],
        "selected_poll_structure_id": expected_config_id,
        "canonical_oof_sha256": canonical_sha,
        "canonical_frozen_draws_sha256": canonical.get("frozen_draws_sha256"),
        "stack_weights_sha256": file_sha256(stack_path),
        "stack_candidate_identities": stack_candidates,
        "uncertainty_calibration_sha256": file_sha256(calibration_path),
        "exceptional_model_lineage": exceptional_model_lineage,
        "exceptional_model_lineage_sha256": exceptional_sha,
        "exceptional_models": exceptional_model_lineage["exceptional_models"],
        "forecast_coverage_lineage": exceptional_model_lineage["forecast_coverage"],
        "historical_evidence_equivalence_lineage": exceptional_model_lineage[
            "historical_evidence_equivalence"
        ],
        "historical_cycles": candidate["historical_cycles"],
        "lead_cutoffs_days": candidate["lead_cutoffs_days"],
        "historical_structural_feature_schema": candidate.get(
            "historical_structural_feature_schema",
            "historical-structural-features-v1",
        ),
        "structural_feature_enables": coerce_structural_feature_enables(
            candidate.get("structural_feature_enables")
        ),
        "code_commit_sha": code_commit_sha or candidate.get("code_commit_sha"),
        "lineage_checks": checks,
        "production_research_eligible": True,
    }
    payload["spec_sha256"] = canonical_sha256(payload)
    path = Path(out_path or ARTIFACTS_DIR / "validated_model_spec_latest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**payload, "path": str(path)}


def load_validated_model_spec(
    *,
    path: str | Path | None = None,
    expected_evidence_bundle_id: str | None = None,
    expected_evidence_bundle_sha256: str | None = None,
) -> tuple[dict[str, Any], PollStructureConfig]:
    path = Path(path or ARTIFACTS_DIR / "validated_model_spec_latest.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = payload.pop("spec_sha256", None)
    actual = canonical_sha256(payload)
    payload["spec_sha256"] = expected
    if payload.get("schema_version") != VALIDATED_SPEC_SCHEMA or expected != actual:
        raise ValueError("validated model spec identity is invalid")
    if payload.get("model_version") != MODEL_VERSION:
        raise ValueError("validated model spec model version is stale")
    if not payload.get("production_research_eligible"):
        raise ValueError("validated model spec is not production/research eligible")
    if expected_evidence_bundle_id and payload.get("evidence_bundle_id") != expected_evidence_bundle_id:
        raise ValueError("validated model spec evidence bundle ID differs from current run")
    if (expected_evidence_bundle_sha256
            and payload.get("evidence_bundle_sha256") != expected_evidence_bundle_sha256):
        raise ValueError("validated model spec evidence bundle hash differs from current run")
    config = poll_structure_from_dict(payload.get("selected_poll_structure"))
    if payload.get("selected_poll_structure_id") != poll_structure_identity(config):
        raise ValueError("validated model spec poll structure identity changed")
    exceptional = payload.get("exceptional_model_lineage") or {}
    if (
        not exceptional
        or payload.get("exceptional_model_lineage_sha256")
        != exceptional.get("lineage_sha256")
        or payload.get("exceptional_models") != exceptional.get("exceptional_models")
    ):
        raise ValueError("validated model spec exceptional model lineage is incomplete")
    return payload, config


def verify_validated_spec_artifacts(
    payload: dict[str, Any], *, artifacts_dir: str | Path | None = None,
) -> dict[str, bool]:
    """Verify current files still match the artifacts frozen into the spec."""
    root = Path(artifacts_dir or ARTIFACTS_DIR)
    bindings = {
        "canonical_oof": (
            root / "nested_component_loo_canonical.json",
            payload.get("canonical_oof_sha256"),
        ),
        "stack_weights": (
            root / "stack_weights_oof.json",
            payload.get("stack_weights_sha256"),
        ),
        "uncertainty_calibration": (
            root / "stack_reliability_crossfit_latest.json",
            payload.get("uncertainty_calibration_sha256"),
        ),
    }
    checks = {
        name: bool(expected and path.is_file() and file_sha256(path) == expected)
        for name, (path, expected) in bindings.items()
    }
    try:
        from midterms.validation.exceptional_model_lineage import (
            verify_exceptional_model_lineage,
        )

        verify_exceptional_model_lineage(
            payload.get("exceptional_model_lineage") or {},
            artifacts_dir=root,
        )
        checks["exceptional_model_lineage"] = True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        checks["exceptional_model_lineage"] = False
    if not all(checks.values()):
        raise ValueError(
            "validated model spec artifact lineage changed: "
            + ", ".join(name for name, ok in checks.items() if not ok)
        )
    return checks
