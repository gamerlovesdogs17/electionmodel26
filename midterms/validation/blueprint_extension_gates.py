"""Machine-readable empirical gates for blueprint extensions.

Code capability and empirical validation are separate. This module never
promotes an implementation merely because its source or unit tests exist.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, ROOT
from midterms.validation.artifact_lineage import frozen_index_semantic_sha256

EXTENSION_GATE_VERSION = "blueprint-extension-gates-v1"

GATE_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "prior_predictive_current_model": {"artifact": "prior_predictive_latest.json", "required": True},
    "posterior_predictive_historical": {"artifact": "posterior_predictive_oof_latest.json", "required": True},
    "full_model_sbc": {"artifact": "full_model_sbc_latest.json", "required": False},
    "sampler_health_current_reference": {"artifact": "forecast_latest.json", "required": True},
    "poll_structure_nested_oos": {"artifact": "nested_component_loo_canonical.json", "required": False, "disabled_feature": True},
    "poll_structure_positive_crossfit": {
        "artifact": "poll_structure_crossfit_latest.json", "required": True,
    },
    "same_family_ablation_oos": {"artifact": "nested_component_loo_selection.json", "required": True},
    "validated_model_spec_lineage": {"artifact": "validated_model_spec_latest.json", "required": True},
    "candidate_timeline_real_data": {"artifact": "evidence_eligibility_latest.json", "required": True, "source_gate": True},
    "pollster_ratings_point_in_time": {"artifact": None, "required": True, "source_gate": True},
    "demographic_point_in_time_snapshots": {"artifact": None, "required": True, "source_gate": True},
    "economic_realtime_history": {"artifact": None, "required": True, "source_gate": True},
    "finance_report_level_history": {"artifact": None, "required": True, "source_gate": True},
    "approval_poll_timing_lineage": {"artifact": None, "required": True, "source_gate": True},
    "overlay_incremental_value": {"artifact": "overlay_validation.json", "required": False, "disabled_feature": True},
    "market_contract_semantics_refresh": {"artifact": None, "required": False, "disabled_feature": True, "source_gate": True},
    "institutional_transition_model": {"artifact": None, "required": False, "disabled_feature": True},
    "joint_scores_historical": {"artifact": "joint_oof_scores_latest.json", "required": True},
    "freshness_current_domains": {"artifact": "evidence_eligibility_latest.json", "required": True, "source_gate": True},
}


def _read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _artifact_model_version(payload: dict[str, Any]) -> str | None:
    return str(
        payload.get("model_version")
        or payload.get("source_model_version")
        or (payload.get("lineage") or {}).get("model_version")
        or ""
    ) or None


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def evaluate_blueprint_extension_gates(
    *,
    compliance_path: Path | None = None,
    artifacts_dir: Path | None = None,
    model_version: str = MODEL_VERSION,
    disabled_features: set[str] | None = None,
    source_only: bool = False,
) -> dict[str, Any]:
    """Evaluate empirical evidence without treating old-version artifacts as current."""
    compliance_path = compliance_path or (ROOT / "BLUEPRINT_COMPLIANCE_AUDIT.json")
    artifacts_dir = artifacts_dir or ARTIFACTS_DIR
    compliance = _read(compliance_path) or {}
    declared = compliance.get("empirical_validation_gates") or {}
    source_readiness = _read(artifacts_dir / "source_readiness_latest.json") or {}
    if disabled_features is None:
        disabled = {
            "poll_structure_nested_oos", "overlay_incremental_value",
            "market_contract_semantics_refresh", "institutional_transition_model",
        }
        forecast = _read(artifacts_dir / "forecast_latest.json") or {}
        current_forecast = forecast if _artifact_model_version(forecast) == model_version else {}
        if current_forecast:
            poll_structure = (
                (current_forecast.get("diagnostics") or {}).get("selected_poll_structure")
                or (current_forecast.get("diagnostics") or {}).get("optional_poll_structure")
                or {}
            )
        else:
            from midterms.model.poll_structure import PollStructureConfig

            poll_structure = PollStructureConfig().to_dict()
        enabled_poll_features = {
            name for name in ("sponsor_effect", "questionnaire_effect", "study_effect")
            if bool(poll_structure.get(name))
        }
        if any(bool(poll_structure.get(name)) for name in (
            "sponsor_effect", "questionnaire_effect", "study_effect",
        )):
            disabled.discard("poll_structure_nested_oos")
        eligibility = _read(artifacts_dir / "evidence_eligibility_latest.json") or {}
        current_eligibility = (
            eligibility if _artifact_model_version(eligibility) == model_version else {}
        )
        roles = (
            (
                current_eligibility.get("effective_domain_contract")
                or (current_forecast.get("evidence_eligibility") or {}).get(
                    "effective_domain_contract"
                )
                or {}
            )
            .get("roles") or {}
        )
        if roles.get("ratings") == "conditionally_required":
            disabled.discard("overlay_incremental_value")
        if roles.get("markets") == "conditionally_required":
            disabled.discard("overlay_incremental_value")
            disabled.discard("market_contract_semantics_refresh")
    else:
        disabled = set(disabled_features)
        enabled_poll_features = set()
    gates: dict[str, dict[str, Any]] = {}
    for name in sorted(set(GATE_REQUIREMENTS) | set(declared)):
        declared_status = declared.get(name, "undeclared")
        req = dict(GATE_REQUIREMENTS.get(name) or {"artifact": None, "required": True})
        artifact_name = req.get("artifact")
        path = artifacts_dir / artifact_name if artifact_name else None
        # Read-only compatibility for pre-two-pass diagnostic fixtures. New
        # rebuilds must use the phase-specific names, but a legacy artifact is
        # still inspected (and normally blocked) so the report explains the
        # actual lineage mismatch instead of reducing it to "missing".
        if path is not None and not path.exists() and name in {
            "same_family_ablation_oos", "poll_structure_nested_oos",
        }:
            legacy_path = artifacts_dir / "nested_component_loo.json"
            if legacy_path.exists():
                path = legacy_path
        payload = _read(path) if path else None
        required = bool(req.get("required")) or bool(
            req.get("disabled_feature") and name not in disabled
        )
        result: dict[str, Any] = {
            "declared_status": declared_status,
            "required_for_full_validation": required,
            "required_model_version": model_version,
            "required_artifact": artifact_name,
            "artifact_sha256": _sha256(path) if path else None,
        }
        if source_only and name in disabled and not required:
            result.update({
                "status": "intentionally_deferred", "ok": True,
                "reason": "feature is disabled in the effective production configuration",
            })
        elif source_only and req.get("source_gate"):
            domain_name = {
                "candidate_timeline_real_data": "candidate_timeline",
                "pollster_ratings_point_in_time": "pollster_ratings",
                "demographic_point_in_time_snapshots": "demographics",
                "economic_realtime_history": "economics",
                "finance_report_level_history": "finance",
                "approval_poll_timing_lineage": "approval",
            }.get(name)
            if name == "freshness_current_domains":
                used = {
                    key: block for key, block in (source_readiness.get("domains") or {}).items()
                    if block.get("required_for_core") or block.get("required_for_historical_validation")
                }
                failed = sorted(key for key, block in used.items() if block.get("status") != "ready")
                ok = bool(used) and not failed and bool(
                    source_readiness.get("ready_for_expensive_rebuild")
                )
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "source_readiness_model_version": source_readiness.get("model_version"),
                    "failed_required_domains": failed,
                    "reason": None if ok else "sealed source-readiness report has unresolved required domains",
                })
            else:
                block = (source_readiness.get("domains") or {}).get(domain_name or "") or {}
                ok = (
                    source_readiness.get("model_version") == model_version
                    and block.get("status") == "ready"
                )
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "source_readiness_model_version": source_readiness.get("model_version"),
                    "source_domain": domain_name,
                    "source_status": block.get("status"),
                    "reason": None if ok else f"source-readiness domain {domain_name} is not ready",
                })
        elif source_only and not req.get("source_gate"):
            result.update({
                "status": "not_applicable", "ok": True,
                "reason": "not part of the source-integrity preflight",
            })
        elif name in {
            "pollster_ratings_point_in_time", "demographic_point_in_time_snapshots",
            "finance_report_level_history", "approval_poll_timing_lineage",
        }:
            domain_name = {
                "pollster_ratings_point_in_time": "pollster_ratings",
                "demographic_point_in_time_snapshots": "demographics",
                "finance_report_level_history": "finance",
                "approval_poll_timing_lineage": "approval",
            }[name]
            manifest_name = {
                "pollster_ratings": "pollster_ratings.json",
                "demographics": "demographic_vintages.json",
                "finance": "fundraising_shares.json",
                "approval": "pres_approval.json",
            }[domain_name]
            manifest = _read(ROOT / "data" / "manifests" / manifest_name) or {}
            readiness_block = (source_readiness.get("domains") or {}).get(domain_name) or {}
            ok = readiness_block.get("status") == "ready" and bool(manifest)
            result.update({
                "status": "pass" if ok else "blocked", "ok": ok,
                "reason": None if ok else f"source-readiness domain {domain_name} is not ready",
                "source_domain": domain_name,
                "source_status": readiness_block.get("status"),
                "required_manifest": f"data/manifests/{manifest_name}",
            })
        elif name == "economic_realtime_history":
            manifest = _read(ROOT / "data" / "manifests" / "economics_vintages.json") or {}
            readiness_block = (source_readiness.get("domains") or {}).get("economics") or {}
            ok = bool(manifest.get("publication_eligible")) and readiness_block.get("status") == "ready"
            result.update({
                "status": "pass" if ok else "blocked", "ok": ok,
                "reason": None if ok else "historical real-time economic vintages are not sealed",
                "required_manifest": "data/manifests/economics_vintages.json",
            })
        elif name in disabled and not required:
            result.update({
                "status": "intentionally_deferred",
                "ok": True,
                "reason": "feature is disabled in the effective production configuration",
            })
        elif payload is None:
            result.update({
                "status": "blocked" if required else "partial",
                "ok": False,
                "reason": (
                    "real source ingest/refresh is required" if req.get("source_gate")
                    else "lineage-compatible empirical artifact is missing"
                ),
            })
        else:
            artifact_version = _artifact_model_version(payload)
            result["artifact_model_version"] = artifact_version
            if artifact_version != model_version:
                result.update({
                    "status": "blocked",
                    "ok": False,
                    "reason": f"artifact model_version={artifact_version!r} does not match {model_version}",
                })
            elif name == "prior_predictive_current_model":
                forecast_path = artifacts_dir / "forecast_latest.json"
                expected = _sha256(forecast_path)
                actual = payload.get("forecast_sha256")
                ok = bool(payload.get("ok")) and bool(expected) and actual == expected
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "required_source_sha256": expected,
                    "artifact_source_sha256": actual,
                    "reason": None if ok else "prior predictive artifact is not bound to current forecast",
                })
            elif name in {"posterior_predictive_historical", "joint_scores_historical"}:
                nested_path = artifacts_dir / "nested_component_loo_canonical.json"
                nested = _read(nested_path) or {}
                expected = _sha256(nested_path)
                actual = payload.get("source_nested_sha256")
                draw_match = bool(nested) and payload.get("source_frozen_draws_sha256") == nested.get(
                    "frozen_draws_sha256"
                )
                ok = bool(payload.get("ok")) and bool(expected) and actual == expected and draw_match
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "required_source_sha256": expected,
                    "artifact_source_sha256": actual,
                    "frozen_draw_fingerprint_matches": draw_match,
                    "reason": None if ok else "diagnostic is not bound to current frozen OOF predictions",
                })
            elif name == "candidate_timeline_real_data":
                block = (payload.get("domains") or {}).get("candidate_timeline") or {}
                historical: dict[str, Any]
                try:
                    from midterms.evidence.candidate_timeline import (
                        audit_candidate_timeline_history,
                    )
                    from midterms.evidence.official_ballot import election_day
                    from midterms.evidence.warehouse import Warehouse

                    warehouse = Warehouse(ensure_fixtures=False)
                    cutoffs = {
                        f"senate-{year}-lead-{lead}": election_day(year) - timedelta(days=lead)
                        for year in (2018, 2020, 2022, 2024)
                        for lead in (60, 30)
                    }
                    # Audit helper keys select election_id, so retain the lead in
                    # the reported key while evaluating each cycle separately.
                    history_rows = []
                    for key, cutoff in cutoffs.items():
                        election_id = "-".join(key.split("-")[:2])
                        history_rows.append(audit_candidate_timeline_history(
                            warehouse.races,
                            warehouse.candidate_timeline,
                            cutoffs={election_id: cutoff},
                        )["cutoffs"][0] | {"validation_case": key})
                    blocking_history = [
                        row["validation_case"] for row in history_rows
                        if not row["publication_eligible"]
                    ]
                    historical = {
                        "schema_version": "formal-60-30-candidate-timeline-audit-v1",
                        "cutoffs": history_rows,
                        "blocking_cutoffs": blocking_history,
                        "publication_eligible": not blocking_history,
                    }
                except Exception as exc:  # noqa: BLE001
                    historical = {
                        "schema_version": "formal-60-30-candidate-timeline-audit-v1",
                        "cutoffs": [], "blocking_cutoffs": ["audit_error"],
                        "publication_eligible": False, "error": str(exc),
                    }
                ok = bool(block.get("publication_eligible")) and bool(
                    historical.get("publication_eligible")
                )
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "reason": None if ok else (
                        "current or formal historical candidate timeline is degraded or untraceable"
                    ),
                    "historical_formal_grid": historical,
                })
            elif name == "freshness_current_domains":
                roles = ((payload.get("effective_domain_contract") or {}).get("roles") or {})
                checked = {
                    domain: (block.get("freshness") or {}).get("status")
                    for domain, block in (payload.get("domains") or {}).items()
                    if roles.get(domain) in {"required_core", "conditionally_required"}
                    and block.get("freshness") is not None
                }
                ok = bool(checked) and all(status == "fresh" for status in checked.values())
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "freshness_statuses": checked,
                    "reason": None if ok else "one or more used evidence domains is not fresh",
                })
            elif name == "sampler_health_current_reference":
                numerical = payload.get("numerical_quality") or {}
                convergence = (payload.get("diagnostics") or {}).get("convergence") or {}
                ok = bool(numerical.get("ok")) and bool(convergence.get("available"))
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "reason": None if ok else "current reference sampler-health evidence is incomplete or failed",
                    "numerical_quality_ok": numerical.get("ok"),
                    "convergence_available": convergence.get("available"),
                })
            elif name == "same_family_ablation_oos":
                frozen_path = artifacts_dir / "nested_component_loo_selection_frozen.json"
                if not frozen_path.exists():
                    legacy_frozen_path = artifacts_dir / "nested_component_loo_frozen.json"
                    if legacy_frozen_path.exists():
                        frozen_path = legacy_frozen_path
                frozen = _read(frozen_path) or {}
                semantic_actual = (
                    frozen_index_semantic_sha256(frozen_path) if frozen else None
                )
                semantic_expected = payload.get("frozen_index_semantic_sha256")
                frozen_lineage_ok = (
                    frozen.get("model_version") == model_version
                    and bool(semantic_expected)
                    and semantic_actual == semantic_expected
                )
                required_ids = {"hier_no_similarity", "hier_no_terminal_race"}
                found: set[str] = set()
                invalid: list[str] = []
                for entry in frozen.get("entries") or []:
                    component = str(entry.get("component") or "")
                    if component not in required_ids:
                        continue
                    lineage = entry.get("structural_ablation_lineage") or {}
                    if entry.get("status") == "ok" and lineage.get("eligible") and len(lineage.get("changed_features") or []) == 1:
                        found.add(component)
                    else:
                        invalid.append(component)
                recommendations = payload.get("g8_recommendations") or {}
                feature_recommendations = {
                    feature: (recommendations.get(feature) or {}).get("recommend")
                    for feature in ("similarity_terminal", "terminal_race")
                }
                supported = all(
                    recommendation == "keep"
                    for recommendation in feature_recommendations.values()
                )
                ok = (
                    frozen_lineage_ok and found == required_ids
                    and not invalid and supported
                )
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "reason": None if ok else (
                        "enabled same-family structures lack current one-change evidence "
                        "with a fold-majority keep recommendation"
                    ),
                    "validated_ablation_ids": sorted(found),
                    "invalid_ablation_ids": sorted(set(invalid)),
                    "feature_recommendations": feature_recommendations,
                    "frozen_index_model_version": frozen.get("model_version"),
                    "required_frozen_index_semantic_sha256": semantic_expected,
                    "actual_frozen_index_semantic_sha256": semantic_actual,
                })
            elif name == "poll_structure_positive_crossfit":
                nested_path = artifacts_dir / "nested_component_loo_selection.json"
                expected_nested_sha = _sha256(nested_path)
                folds = payload.get("outer_folds") or []
                expected_candidates = {
                    "pymc", "hier_plus_study_effect", "hier_plus_sponsor_effect",
                    "hier_plus_questionnaire_effect",
                }
                candidates = set(payload.get("candidate_set") or [])
                ok = (
                    expected_nested_sha is not None
                    and payload.get("source_nested_loo_sha256") == expected_nested_sha
                    and payload.get("freeze_before_truth") is True
                    and candidates == expected_candidates
                    and len(folds) == 4
                    and all(fold.get("heldout_truth_used_for_selection") is False for fold in folds)
                )
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "reason": None if ok else (
                        "positive poll-structure crossfit is missing, incomplete, or not bound to current OOF"
                    ),
                    "required_source_sha256": expected_nested_sha,
                    "artifact_source_sha256": payload.get("source_nested_loo_sha256"),
                    "n_outer_folds": len(folds),
                })
            elif name == "poll_structure_nested_oos":
                spec_path = artifacts_dir / "validated_model_spec_latest.json"
                spec = _read(spec_path) or {}
                selected = spec.get("selected_poll_structure") or {}
                selected_features = {
                    feature for feature in (
                        "sponsor_effect", "questionnaire_effect", "study_effect"
                    ) if bool(selected.get(feature))
                }
                feature_to_ablation = {
                    "study_effect": "hier_no_study_effect",
                    "sponsor_effect": "hier_no_sponsor_effect",
                    "questionnaire_effect": "hier_no_questionnaire_effect",
                }
                required_ablation_ids = sorted(
                    feature_to_ablation[feature] for feature in enabled_poll_features
                )
                canonical_ok = bool(spec) and (
                    payload.get("validation_phase") == "canonical_poll_structure"
                    and payload.get("poll_structure_config_id")
                    == spec.get("selected_poll_structure_id")
                    and _sha256(artifacts_dir / "nested_component_loo_canonical.json")
                    == spec.get("canonical_oof_sha256")
                )
                ok = (
                    bool(enabled_poll_features)
                    and selected_features == enabled_poll_features
                    and canonical_ok
                )
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "reason": None if ok else (
                        "enabled poll structure is not bound through selection to canonical OOF"
                    ),
                    "enabled_poll_features": sorted(enabled_poll_features),
                    "selected_poll_features": sorted(selected_features),
                    "required_ablation_ids": required_ablation_ids,
                    "validated_model_spec_sha256": _sha256(spec_path),
                })
            elif name == "validated_model_spec_lineage":
                from midterms.validation.validated_model_spec import (
                    load_validated_model_spec,
                    verify_validated_spec_artifacts,
                )

                try:
                    spec, _ = load_validated_model_spec(path=path)
                    checks = verify_validated_spec_artifacts(
                        spec, artifacts_dir=artifacts_dir,
                    )
                    ok = bool(spec.get("production_research_eligible")) and all(checks.values())
                    error = None
                except Exception as exc:  # noqa: BLE001
                    ok, checks, error = False, {}, str(exc)
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "reason": None if ok else (
                        error or "validated model spec lineage is incomplete"
                    ),
                    "artifact_bindings": checks,
                })
            elif payload.get("ok") is False or payload.get("publishable") is False:
                result.update({
                    "status": "blocked" if required else "partial",
                    "ok": False,
                    "reason": "artifact exists but its own empirical gate is not satisfied",
                })
            else:
                result.update({"status": "pass", "ok": True, "reason": None})
        gates[name] = result
    blocking = sorted(
        name for name, gate in gates.items()
        if gate["required_for_full_validation"]
        and gate["status"] not in {"pass", "not_applicable"}
    )
    return {
        "schema_version": EXTENSION_GATE_VERSION,
        "model_version": model_version,
        "gates": gates,
        "required_ok": not blocking,
        "status": "pass" if not blocking else "blocked",
        "blocking_gates": blocking,
        "note": "Code capability does not satisfy empirical validation.",
        "source_only": bool(source_only),
    }
