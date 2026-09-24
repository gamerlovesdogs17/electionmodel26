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
    "poll_structure_nested_oos": {"artifact": "nested_component_loo.json", "required": False, "disabled_feature": True},
    "same_family_ablation_oos": {"artifact": "nested_component_loo.json", "required": True},
    "candidate_timeline_real_data": {"artifact": "evidence_eligibility_latest.json", "required": True, "source_gate": True},
    "demographic_point_in_time_snapshots": {"artifact": None, "required": True, "source_gate": True},
    "economic_realtime_history": {"artifact": None, "required": True, "source_gate": True},
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
    if disabled_features is None:
        disabled = {
            "poll_structure_nested_oos", "overlay_incremental_value",
            "market_contract_semantics_refresh", "institutional_transition_model",
        }
        forecast = _read(artifacts_dir / "forecast_latest.json") or {}
        current_forecast = forecast if _artifact_model_version(forecast) == model_version else {}
        if current_forecast:
            poll_structure = (
                (current_forecast.get("diagnostics") or {}).get("optional_poll_structure") or {}
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
        if source_only and not req.get("source_gate"):
            result.update({
                "status": "not_applicable", "ok": True,
                "reason": "not part of the source-integrity preflight",
            })
        elif name == "demographic_point_in_time_snapshots":
            manifest = _read(ROOT / "data" / "manifests" / "demography.json") or {}
            ok = bool(manifest.get("historical_point_in_time_eligible"))
            result.update({
                "status": "pass" if ok else "blocked", "ok": ok,
                "reason": None if ok else "historical point-in-time demographic source is not sealed",
                "required_manifest": "data/manifests/demography.json",
            })
        elif name == "economic_realtime_history":
            manifest = _read(ROOT / "data" / "manifests" / "economics_vintages.json") or {}
            ok = bool(manifest.get("historical_replay_eligible"))
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
                nested_path = artifacts_dir / "nested_component_loo.json"
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
                frozen_path = artifacts_dir / "nested_component_loo_frozen.json"
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
            elif name == "poll_structure_nested_oos":
                frozen_path = artifacts_dir / "nested_component_loo_frozen.json"
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
                component_by_feature = {
                    "sponsor_effect": "hier_no_sponsor_effect",
                    "questionnaire_effect": "hier_no_questionnaire_effect",
                    "study_effect": "hier_no_study_effect",
                }
                required_components = {
                    component_by_feature[feature]: feature
                    for feature in enabled_poll_features
                }
                validated: set[str] = set()
                for entry in frozen.get("entries") or []:
                    component = str(entry.get("component") or "")
                    feature = required_components.get(component)
                    if not feature:
                        continue
                    lineage = entry.get("structural_ablation_lineage") or {}
                    if (
                        entry.get("status") == "ok"
                        and lineage.get("eligible")
                        and lineage.get("changed_features") == [feature]
                    ):
                        validated.add(component)
                ok = (
                    frozen_lineage_ok
                    and bool(required_components)
                    and validated == set(required_components)
                    and all(
                        (payload.get("g8_recommendations") or {}).get(feature, {}).get(
                            "recommend"
                        ) == "keep"
                        for feature in enabled_poll_features
                    )
                )
                result.update({
                    "status": "pass" if ok else "blocked", "ok": ok,
                    "reason": None if ok else (
                        "enabled poll structures lack current one-change nested OOS "
                        "lineage with a fold-majority keep recommendation"
                    ),
                    "enabled_poll_features": sorted(enabled_poll_features),
                    "required_ablation_ids": sorted(required_components),
                    "validated_ablation_ids": sorted(validated),
                    "feature_recommendations": {
                        feature: (payload.get("g8_recommendations") or {}).get(
                            feature, {}
                        ).get("recommend")
                        for feature in sorted(enabled_poll_features)
                    },
                    "required_frozen_index_semantic_sha256": semantic_expected,
                    "actual_frozen_index_semantic_sha256": semantic_actual,
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
