"""Consolidated pre-rebuild model integrity audit."""

from __future__ import annotations

import json
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MODEL_VERSION,
    PREVIOUS_SEALED_MODEL_VERSION,
    PUBLIC_LIVE_ENABLED,
)
from midterms.evidence.generic_ballot_context import (
    write_generic_ballot_parity_artifact,
)
from midterms.model.challengers import (
    STATE_SPACE_CHALLENGERS,
    state_space_challenger_lineage,
)
from midterms.model.national_environment_ablations import (
    register_national_environment_ablations,
)
from midterms.ops.forecast_artifact_coherence import (
    DEV_STUB_STATUS,
    assert_forecast_latest_coherent,
    ensure_sealed_publication_preserved,
)
from midterms.validation.historical_multiway_analogs import (
    write_historical_multiway_analogs_artifact,
)
from midterms.validation.oof_diagnostic_slices import slice_registry


def build_pre_rebuild_integrity_audit() -> dict[str, Any]:
    gb = write_generic_ballot_parity_artifact()
    analogs = write_historical_multiway_analogs_artifact()
    sealed = ensure_sealed_publication_preserved()
    try:
        forecast_coherent = assert_forecast_latest_coherent()
    except ValueError as exc:
        forecast_coherent = {"ok": False, "error": str(exc)}

    ss_lineage = {
        name: state_space_challenger_lineage(name) for name in STATE_SPACE_CHALLENGERS
    }
    nat = register_national_environment_ablations()

    gb_ok = int(gb.get("n_formal_eligible") or 0) == int(gb.get("n_cutoffs") or 8)

    blockers: list[str] = []
    if not gb_ok:
        blockers.append(
            "formal OOF generic-ballot archive missing/incomplete for the eight T-60/T-30 cutoffs"
        )
    if PUBLIC_LIVE_ENABLED:
        blockers.append("PUBLIC_LIVE_ENABLED is True during pre-rebuild integrity pass")
    if not forecast_coherent.get("ok"):
        blockers.append(f"forecast_latest coherence failed: {forecast_coherent.get('error')}")

    # Historical formal inputs readiness (honest — do not mark ready on tests alone).
    historical_readiness = {
        "point_in_time_structural_prior": "assumed_available_via_existing_snapshot_pipeline",
        "candidate_specific_finance": "assumed_available_via_existing_snapshot_pipeline",
        "personal_incumbency": "assumed_available_via_existing_snapshot_pipeline",
        "true_historical_generic_ballot": "FAIL" if not gb_ok else "OK",
        "required_poll_evidence": "assumed_available_via_existing_snapshot_pipeline",
        "candidate_identity": "race_scoped_contract_added_current; historical ledger path retained",
        "release_hashes": "pending_new_release_identity_after_spec_bump",
        "all_eight_folds_ready": gb_ok,
    }
    if not historical_readiness["all_eight_folds_ready"]:
        blockers.append("eight formal folds lack reconstructable historical generic ballot")

    verdict = (
        "READY_FOR_EXPENSIVE_OOF_REBUILD"
        if not blockers
        else "NOT_READY_FOR_EXPENSIVE_OOF_REBUILD"
    )

    payload = {
        "schema_version": "pre-rebuild-model-integrity-v1",
        "model_version": MODEL_VERSION,
        "previous_sealed_model_version": PREVIOUS_SEALED_MODEL_VERSION,
        "verdict": verdict,
        "blockers": blockers,
        "source_integrity": {
            "votehub_pagination": "defensive_multi_page_client_implemented",
            "generic_ballot_history_status": gb.get("status"),
            "generic_ballot_parity_path": "data/artifacts/generic_ballot_parity_v0924.json",
            "discovery_artifact": "data/artifacts/current_poll_discovery_reconciliation_v0924.json",
        },
        "candidate_identity": {
            "race_scoped_contract": "midterms.evidence.race_scoped_identity",
            "global_name_party_production": "disabled_for_collisions",
            "montana_kyle_austin": "race_scoped_L_not_global_R",
        },
        "ordinary_model": {
            "generic_ballot_parity": gb,
            "fundamentals_coefficient_parity": {
                "production": "PRIOR_COEF / COEF",
                "oof_primary": "same PRIOR_COEF via fundamentals_mean",
                "ridge_challenger": "fit_ridge_fundamentals only",
                "status": "aligned_on_PRIOR_COEF_for_primary_paths",
            },
            "state_space_repull": {
                "reference_fund_pull": 0.35,
                "challenger": "state_space_no_ed_fund_repull",
                "lineage": ss_lineage.get("state_space_no_ed_fund_repull"),
            },
            "calendar_time_propagation": {
                "last_poll_to_as_of": True,
                "as_of_to_ed": "future_movement_sd_only",
                "process_sd_per_sqrt_day": 0.8,
                "process_scale_challengers": [
                    "state_space_process_sd_0_5",
                    "state_space_process_sd_1_2",
                ],
            },
            "national_environment_ablations": nat,
            "oof_diagnostic_slices": slice_registry(),
        },
        "exceptional_models": {
            "contest_classifier": "midterms.model.contest_classifier",
            "binary_nonmajor_prior_sd_challengers": [20.0, 30.0, 40.0],
            "historical_multiway_analogs_n": analogs.get("n_analogs"),
            "historical_multiway_status": analogs.get("status"),
            "leave_one_poll_out": "midterms.validation.exceptional_loo_diagnostics",
        },
        "historical_validation_readiness": historical_readiness,
        "forecast_artifact": {
            "sealed": sealed,
            "latest_coherence": forecast_coherent,
            "stub_status": DEV_STUB_STATUS,
        },
        "statistical_choices_unchanged_pending_oos": [
            "fund_pull",
            "process_sd_per_sqrt_day",
            "exceptional_prior_sd",
            "stack_weights",
            "national_environment_feature_inclusion",
            "recency_half_life",
            "sample_size_weighting",
            "quality_weighting",
            "house_effect_strength",
            "structural_prior_composition",
            "terminal_error_scales",
        ],
    }
    return payload


def write_pre_rebuild_integrity_audit() -> dict[str, Any]:
    payload = build_pre_rebuild_integrity_audit()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "pre_rebuild_model_integrity_v0924.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
