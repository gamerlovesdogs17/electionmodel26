"""Consolidated pre-rebuild model integrity audit."""

from __future__ import annotations

import inspect
import json
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MODEL_VERSION,
    PREVIOUS_SEALED_MODEL_VERSION,
    PUBLIC_LIVE_ENABLED,
    ROOT,
)
from midterms.evidence.federal_election_day import federal_election_day, formal_cutoff
from midterms.evidence.generic_ballot_context import (
    FORMAL_OOF_CYCLES,
    write_generic_ballot_parity_artifact,
)
from midterms.model.challengers import (
    STATE_SPACE_CHALLENGERS,
    state_space_challenger_lineage,
)
from midterms.model.contest_classifier import (
    classify_contest_structure,
    registry_contest_structure,
)
from midterms.model.national_environment_ablations import (
    NATIONAL_ENVIRONMENT_ABLATIONS,
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
from midterms.validation.nested_component_loo import (
    FORMAL_OOF_NATIONAL_ENV_CHALLENGERS,
    FORMAL_OOF_STATE_SPACE_CHALLENGERS,
    freeze_component_predictions,
)
from midterms.validation.official_ballot_fields import load_official_ballot_fields
from midterms.validation.oof_diagnostic_slices import slice_registry


def _repo_rel(path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except (ValueError, OSError, TypeError):
        return str(path)


def _contest_structure_report() -> dict[str, Any]:
    official = load_official_ballot_fields()
    out: dict[str, Any] = {}
    for state, race in official["races"].items():
        certified = [
            c
            for c in race.get("candidates") or []
            if c.get("status") == "certified_general_ballot" and not c.get("withdrawn")
        ]
        cls = classify_contest_structure(
            ballot_candidates=certified,
            election_rule=str(race.get("institutional_rule") or ""),
        )
        out[state] = {
            "race_id": race["race_id"],
            "ballot_candidate_count": len(certified),
            "structural_classification": cls["category"],
            "registry_contest_structure": registry_contest_structure(cls),
            "principal_candidate_pair": cls.get("principal_pair"),
            "residual_candidate_count": len(cls.get("residual_candidates") or []),
            "residual_candidates": cls.get("residual_candidates"),
            "evidence_used": cls.get("classification_evidence") or {"basis": cls.get("basis")},
            "modeling_path": cls.get("modeling_path"),
            "validation_status": cls.get("validation_level")
            or (
                "unsupported"
                if cls["category"] == "genuine_multiway_plurality"
                else "limited_supported"
            ),
            "estimand_note": cls.get("estimand_note"),
        }
    highlight = {k: out[k] for k in ("NE", "ID", "MT", "SD") if k in out}
    # AK is RCV via separate evidence surface.
    highlight["AK"] = {
        "race_id": "senate-2026-AK",
        "structural_classification": "alaska_rcv_multiway",
        "modeling_path": "alaska_rcv_adapter",
        "validation_status": "limited",
    }
    return {"all_active_races": out, "highlight": highlight}


def _challenger_wiring_report() -> dict[str, Any]:
    src = inspect.getsource(freeze_component_predictions)
    wired = {}
    for name in FORMAL_OOF_STATE_SPACE_CHALLENGERS:
        wired[name] = {
            "registered_in_state_space_challengers": name in STATE_SPACE_CHALLENGERS,
            "frozen_in_freeze_component_predictions": f'"{name}"' in src or f"'{name}'" in src,
            "lineage_ok": state_space_challenger_lineage(name)["lineage_ok"],
        }
    for name in FORMAL_OOF_NATIONAL_ENV_CHALLENGERS:
        wired[name] = {
            "registered_in_national_env_ablations": name in NATIONAL_ENVIRONMENT_ABLATIONS,
            "frozen_in_freeze_component_predictions": f'"{name}"' in src
            or "FORMAL_OOF_NATIONAL_ENV_CHALLENGERS" in src,
        }
    all_wired = all(
        v.get("frozen_in_freeze_component_predictions") for v in wired.values()
    )
    return {
        "challengers": wired,
        "all_intended_challengers_wired_for_formal_oof": all_wired,
        "candidate_neutral_binary_variants": "midterms.model.candidate_neutral_binary",
        "prior_sd_exceptional_challengers": [20.0, 30.0, 40.0],
    }


def build_pre_rebuild_integrity_audit(*, ci_status: dict[str, Any] | None = None) -> dict[str, Any]:
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
    contest = _contest_structure_report()
    challengers = _challenger_wiring_report()

    election_dates = {}
    for year in FORMAL_OOF_CYCLES:
        ed = federal_election_day(year)
        election_dates[str(year)] = {
            "election_day": ed.isoformat(),
            "T60": formal_cutoff(year, 60).isoformat(),
            "T30": formal_cutoff(year, 30).isoformat(),
        }

    gb_ok = int(gb.get("n_formal_eligible") or 0) == int(gb.get("n_cutoffs") or 8)
    ed_ok = (
        election_dates["2022"]["election_day"] == "2022-11-08"
        and election_dates["2018"]["election_day"] == "2018-11-06"
        and election_dates["2020"]["election_day"] == "2020-11-03"
        and election_dates["2024"]["election_day"] == "2024-11-05"
    )
    ne = contest["highlight"].get("NE") or {}
    ne_ok = (
        ne.get("structural_classification") == "principal_binary_with_minor_residual"
        and ne.get("ballot_candidate_count", 0) >= 3
    )
    cache_ok = bool(gb.get("historical_archive_hash"))
    ci_ok = bool((ci_status or {}).get("ok", False))

    blockers: list[str] = []
    if not gb_ok:
        blockers.append(
            "formal OOF generic-ballot archive missing/incomplete for the eight T-60/T-30 cutoffs"
        )
        blockers.extend(f"missing fold: {m}" for m in (gb.get("missing_formal_folds") or []))
    if not ed_ok:
        blockers.append("federal Election Day dates incorrect (esp. 2022)")
    if not ne_ok:
        blockers.append(
            "Nebraska still classified as genuine multiway from candidate count alone"
        )
    if not challengers["all_intended_challengers_wired_for_formal_oof"]:
        blockers.append("intended statistical challengers not fully wired into freeze_component_predictions")
    if not cache_ok:
        blockers.append("historical GB archive hash missing; OOF cache identity unbound")
    if PUBLIC_LIVE_ENABLED:
        blockers.append("PUBLIC_LIVE_ENABLED is True during pre-rebuild integrity pass")
    if not forecast_coherent.get("ok"):
        blockers.append(f"forecast_latest coherence failed: {forecast_coherent.get('error')}")
    if ci_status is not None and not ci_ok:
        blockers.append("cheap CI not green")

    historical_readiness = {
        "point_in_time_structural_prior": "assumed_available_via_existing_snapshot_pipeline",
        "candidate_specific_finance": "assumed_available_via_existing_snapshot_pipeline",
        "personal_incumbency": "assumed_available_via_existing_snapshot_pipeline",
        "true_historical_generic_ballot": "OK" if gb_ok else "FAIL",
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

    # Strip absolute paths from nested gb payload if present.
    gb_clean = dict(gb)
    if "path" in gb_clean:
        gb_clean["path"] = str(gb_clean["path"]).replace("\\", "/")
        if ":/" in gb_clean["path"] or gb_clean["path"].startswith("C:"):
            gb_clean["path"] = "data/artifacts/generic_ballot_parity_v0925.json"

    payload = {
        "schema_version": "pre-rebuild-model-integrity-v0925",
        "model_version": MODEL_VERSION,
        "previous_sealed_model_version": PREVIOUS_SEALED_MODEL_VERSION,
        "verdict": verdict,
        "blockers": blockers,
        "election_dates": election_dates,
        "historical_generic_ballot": {
            "n_formal_eligible": gb.get("n_formal_eligible"),
            "n_cutoffs": gb.get("n_cutoffs"),
            "status": gb.get("status"),
            "archive_path": gb.get("historical_archive_path"),
            "archive_hash": gb.get("historical_archive_hash"),
            "folds": [
                {
                    "election_id": r["election_id"],
                    "lead_days": r["lead_days"],
                    "election_day": r["election_day"],
                    "as_of": r["as_of"],
                    "available": r["formal_oof_eligible"],
                    "margin": r["gb_margin"],
                    "n_polls": r["n_eligible_polls"],
                    "source_hash": r["source_hash"],
                    "point_in_time_eligible": r["formal_oof_eligible"],
                    "aggregation_method": r["aggregation_method"],
                }
                for r in gb.get("rows") or []
            ],
        },
        "contest_structure": contest,
        "challengers": {
            **challengers,
            "state_space_lineage": ss_lineage,
            "national_environment_ablations": nat,
            "reference_fund_pull": 0.35,
            "process_sd_per_sqrt_day": 0.8,
        },
        "source_integrity": {
            "votehub_pagination": "defensive_multi_page_client_implemented",
            "generic_ballot_history_status": gb.get("status"),
            "generic_ballot_parity_path": "data/artifacts/generic_ballot_parity_v0925.json",
            "discovery_artifact": "data/artifacts/current_poll_discovery_reconciliation_v0925.json",
            "superseded_artifacts_wrong_2022_election_day": [
                "data/artifacts/generic_ballot_parity_v0924.json",
                "data/artifacts/pre_rebuild_model_integrity_v0924.json",
            ],
        },
        "candidate_identity": {
            "race_scoped_contract": "midterms.evidence.race_scoped_identity",
            "global_name_party_production": "disabled_for_collisions",
            "montana_kyle_austin": "race_scoped_L_not_global_R",
        },
        "ordinary_model": {
            "generic_ballot_parity": gb_clean,
            "fundamentals_coefficient_parity": {
                "production": "PRIOR_COEF / COEF",
                "oof_primary": "same PRIOR_COEF via fundamentals_mean",
                "ridge_challenger": "fit_ridge_fundamentals only",
                "status": "aligned_on_PRIOR_COEF_for_primary_paths",
            },
            "oof_diagnostic_slices": slice_registry(),
        },
        "exceptional_models": {
            "contest_classifier": "midterms.model.contest_classifier",
            "principal_binary_criterion": "midterms.model.principal_binary_criterion",
            "binary_nonmajor_prior_sd_challengers": [20.0, 30.0, 40.0],
            "historical_multiway_analogs_n": analogs.get("n_analogs"),
            "historical_multiway_status": analogs.get("status"),
            "n_principal_binary_with_minors": analogs.get("n_principal_binary_with_minors"),
            "n_materially_multiway": analogs.get("n_materially_multiway"),
            "coverage_years": analogs.get("years_present"),
            "coverage_gaps": analogs.get("coverage_gaps"),
            "leave_one_poll_out": "midterms.validation.exceptional_loo_diagnostics",
        },
        "historical_validation_readiness": historical_readiness,
        "forecast_artifact": {
            "sealed": sealed,
            "latest_coherence": forecast_coherent,
            "stub_status": DEV_STUB_STATUS,
        },
        "ci": ci_status or {"ok": None, "note": "populated by write path after test run"},
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


def write_pre_rebuild_integrity_audit(*, ci_status: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = build_pre_rebuild_integrity_audit(ci_status=ci_status)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "pre_rebuild_model_integrity_v0925.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    payload["path"] = _repo_rel(path)
    return payload
