"""Part-6 blueprint fidelity delta for v0.9.24 (challengers remain disabled)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from midterms.config import ARTIFACTS_DIR, FORMAL_VALIDATION_LEADS, LEAD_DAYS, MODEL_VERSION
from midterms.model.fundamentals_challengers import FundamentalsChallengerConfig
from midterms.model.turnout import TurnoutLayerConfig


def build_blueprint_fidelity_delta() -> dict[str, Any]:
    challengers = FundamentalsChallengerConfig()
    turnout = TurnoutLayerConfig()
    return {
        "schema_version": "blueprint-fidelity-delta-v0924",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "active_statistical_changes": [
            {
                "id": "poll_weight_no_n_quality_double_count",
                "status": "repaired",
                "detail": (
                    "influence_weight uses recency × partisan × caps × study-cluster only; "
                    "n and quality enter measurement variance once"
                ),
            },
            {
                "id": "absolute_recency_preserved",
                "status": "repaired",
                "detail": (
                    "Within-race normalization applies to non-recency factors; "
                    "absolute calendar recency is multiplied back"
                ),
            },
            {
                "id": "state_space_calendar_delta_t",
                "status": "repaired",
                "detail": (
                    "Process variance accumulates (0.8 pp/√day)^2 × calendar Δt; "
                    "same-day Δt = 0"
                ),
            },
            {
                "id": "multiway_plurality_fail_closed",
                "status": "implemented",
                "detail": "Generic plurality path + unsupported fail-closed when analogs sparse",
            },
            {
                "id": "shared_race_presentation",
                "status": "implemented",
                "detail": "UI presentation contract; no Alaska draw / candidate_probability mutation",
            },
        ],
        "deferred_or_disabled": [
            {
                "id": "candidate_experience_challenger",
                "status": "not_yet_implemented",
                "enabled": challengers.candidate_experience,
                "implementation_status": (
                    "Config flag and provenance-gated consumer exist in "
                    "fundamentals_challengers.py, but no feature builder / sourced "
                    "store constructs candidate_experience values. Do not treat as "
                    "an implemented-but-disabled production challenger."
                ),
                "activation_gate": "implement_builder_then_nested_oos",
            },
            {
                "id": "special_election_signal_challenger",
                "status": "not_yet_implemented",
                "enabled": challengers.special_election_signal,
                "implementation_status": (
                    "Config flag and provenance-gated consumer exist; no special-election "
                    "signal feature builder or sourced historical store is implemented."
                ),
                "activation_gate": "implement_builder_then_nested_oos",
            },
            {
                "id": "recent_statewide_performance_challenger",
                "status": "not_yet_implemented",
                "enabled": challengers.recent_statewide_performance,
                "implementation_status": (
                    "Config flag only; no feature builder / sourced store."
                ),
                "activation_gate": "implement_builder_then_nested_oos",
            },
            {
                "id": "turnout_layer",
                "status": "intentionally_deferred_interface_only",
                "enabled": turnout.enabled,
                "evidence_gap": "No validated turnout→margin translation for production seats",
            },
            {
                "id": "dynamic_pymc_sponsor_questionnaire_study",
                "status": "disabled_in_validated_spec",
                "detail": "Selection remains all-off; heuristic study downweighting only",
            },
            {
                "id": "expert_market_overlays",
                "status": "disabled_for_publication",
                "detail": "Require exact-weight timestamp-pure nested-OOS contract",
            },
            {
                "id": "formal_validation_lead_grid",
                "status": "wired_not_run",
                "formal_validation_leads": list(FORMAL_VALIDATION_LEADS),
                "diagnostic_lead_days": list(LEAD_DAYS),
                "canonical_formal_oof_subset": [60, 30],
                "note": "FORMAL_VALIDATION_LEADS available for later full validation; not executed in v0.9.24 cheap pass",
            },
            {
                "id": "joint_heavy_tails",
                "status": "preserved",
                "detail": "Student-t national + local path retained in state_space / chamber",
            },
        ],
        "outside_model_calibration_targets": False,
        "expensive_rebuild_run": False,
    }


def write_blueprint_fidelity_delta() -> dict[str, Any]:
    payload = build_blueprint_fidelity_delta()
    path = ARTIFACTS_DIR / "blueprint_fidelity_delta_v0924.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
