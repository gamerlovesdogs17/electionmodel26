"""Deterministic current-race poll coverage and matchup-integrity audit."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.evidence.schema import is_active_ballot_row

COVERAGE_SCHEMA_VERSION = "current-race-poll-coverage-v3"


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def current_race_poll_coverage(
    races: pd.DataFrame,
    raw_polls: pd.DataFrame,
    compatible_polls: pd.DataFrame,
    candidate_metadata: dict[str, Any],
    *,
    as_of: str | date,
) -> dict[str, Any]:
    """Audit every active 2026 race without assuming every race needs a poll."""
    cutoff = pd.Timestamp(as_of).date().isoformat()
    exclusions = candidate_metadata.get("poll_exclusions") or []
    exclusion_by_race: dict[str, list[dict[str, Any]]] = {}
    for item in exclusions:
        exclusion_by_race.setdefault(str(item.get("race_id") or ""), []).append(dict(item))
    classification = {
        str(item.get("race_id")): dict(item)
        for item in candidate_metadata.get("classification_records") or []
    }
    compatible_ids_by_race = {
        str(race_id): {str(poll_id) for poll_id in poll_ids}
        for race_id, poll_ids in (
            candidate_metadata.get("candidate_compatible_poll_ids_by_race") or {}
        ).items()
    }

    records: list[dict[str, Any]] = []
    for _, race in races.iterrows():
        if bool(race.get("not_up")) or not is_active_ballot_row(race):
            continue
        race_id = str(race.get("race_id") or "")
        if not race_id.startswith("senate-2026-"):
            continue
        raw = raw_polls[raw_polls["race_id"].astype(str).eq(race_id)].copy()
        model_safe = compatible_polls[
            compatible_polls["race_id"].astype(str).eq(race_id)
        ].copy()
        candidate_compatible_ids = compatible_ids_by_race.get(race_id, set())
        compatible = raw[
            raw.get("poll_id", pd.Series("", index=raw.index)).astype(str).isin(
                candidate_compatible_ids
            )
        ].copy()
        excluded = exclusion_by_race.get(race_id, [])
        reasons = [str(item.get("reason") or "") for item in excluded]
        hypothetical_ids = sorted({
            str(item.get("poll_id") or "") for item in excluded
            if "hypothetical" in str(item.get("reason") or "")
        })
        candidate_excluded_ids = sorted({
            str(item.get("poll_id") or "") for item in excluded
            if "matchup_not_selected" in str(item.get("reason") or "")
            or "ambiguous_candidate_matchup" in str(item.get("reason") or "")
        })
        unresolved_ids = sorted({
            str(item.get("poll_id") or "") for item in excluded
            if "identity_unresolved" in str(item.get("reason") or "")
        })
        unsupported_target_ids = sorted({
            str(item.get("poll_id") or "") for item in excluded
            if "ineligible_for_binary" in str(item.get("reason") or "")
        })
        matchups = sorted({
            str(value) for value in raw.get("matchup_id", pd.Series(dtype=object)).dropna()
            if str(value).strip()
        })
        state = classification.get(race_id, {})
        modeled_party = str(race.get("modeled_ballot_party") or "").upper()
        binary_score_eligible = bool(state.get("binary_score_eligible", True))
        target_supported = bool(
            state.get("probability_model_supported", binary_score_eligible)
        )
        support_status = str(
            state.get("probability_model_support_status")
            or ("ordinary_binary_model" if binary_score_eligible else "unsupported")
        )
        structure = str(
            race.get("contest_structure")
            or state.get("contest_structure")
            or (
                "ranked_choice_multiway" if str(race.get("state")) == "AK"
                else "non_major_party_vs_republican"
                if modeled_party not in {"D", "DEM", "DEMOCRAT", "DEMOCRATIC"}
                else "binary_dem_vs_rep"
            )
        )
        identity_resolved = bool(state.get("candidate_identity_resolved"))
        identity_sensitive = bool(
            state.get("candidate_identity_required")
            or len(matchups) > 1
            or structure != "binary_dem_vs_rep"
        )
        hard_reasons: list[str] = []
        warning_reasons: list[str] = []
        if not target_supported:
            hard_reasons.append(str(
                state.get("probability_model_support_reason")
                or state.get("binary_score_exclusion_reason")
                or "unsupported_probability_target"
            ))
        if state.get("candidate_identity_required") and not identity_resolved:
            hard_reasons.append("current_candidate_identity_unresolved")
        if identity_sensitive and len(raw) and not len(compatible):
            hard_reasons.append("identity_sensitive_race_has_zero_compatible_polls")
        elif identity_sensitive and not len(raw):
            warning_reasons.append("identity_sensitive_race_has_zero_raw_polls")
        if support_status in {"limited_supported", "limited_supported_prior_only"}:
            warning_reasons.append("limited_validation_exception_model")
        if support_status == "limited_supported_prior_only":
            warning_reasons.extend([
                "prior_only_zero_candidate_compatible_polls",
                "wide_exceptional_uncertainty_required",
            ])
        forecast_status = "fail" if hard_reasons else "warning" if warning_reasons else "pass"
        evidence_reasons: list[str] = []
        if state.get("candidate_identity_required") and not identity_resolved:
            evidence_reasons.append("current_candidate_identity_unresolved")
        evidence_status = "fail" if evidence_reasons else "pass"

        def latest(column: str, frame: pd.DataFrame = compatible) -> str | None:
            if column not in frame.columns:
                return None
            values = pd.to_datetime(frame.get(column), errors="coerce")
            return values.max().isoformat() if len(values) and values.notna().any() else None

        records.append({
            "race_id": race_id,
            "state": str(race.get("state") or ""),
            "candidate_state": state.get("candidate_state"),
            "candidate_state_reason": state.get("candidate_state_reason"),
            "resolved_matchup": {
                "modeled_candidate_id": race.get("modeled_candidate_id"),
                "modeled_candidate_name": race.get("modeled_candidate_name"),
                "modeled_ballot_party": race.get("modeled_ballot_party"),
                "opposing_candidate_id": race.get("opposing_candidate_id"),
                "opposing_candidate_name": race.get("opposing_candidate_name"),
                "opposing_ballot_party": race.get("opposing_ballot_party"),
                "identity_source": race.get("identity_source"),
                "current_matchup_status": race.get("current_matchup_status"),
                "reviewed_as_of": race.get("identity_reviewed_as_of"),
            },
            "contest_structure": structure,
            "candidate_identity_resolved": identity_resolved,
            "polling_available": bool(len(compatible)),
            "statistical_target_supported": target_supported,
            "binary_score_eligible": binary_score_eligible,
            "probability_model_supported": target_supported,
            "probability_model_support_status": support_status,
            "probability_model_support_reason": state.get(
                "probability_model_support_reason"
            ),
            "identity_sensitive": identity_sensitive,
            "qa_universe": identity_sensitive,
            "n_raw_current_polls": len(raw),
            "n_candidate_compatible_polls": len(compatible),
            "n_model_safe_polls": len(model_safe),
            "n_exception_adapter_polls": (
                len(compatible)
                if support_status in {"limited_supported", "limited_supported_prior_only"}
                else 0
            ),
            "n_excluded_obsolete_or_incompatible": len(candidate_excluded_ids),
            "n_excluded_identity_unresolved": len(unresolved_ids),
            "n_excluded_unsupported_target": (
                0
                if support_status in {"limited_supported", "limited_supported_prior_only"}
                else len(unsupported_target_ids)
            ),
            "ordinary_model_excluded_poll_ids": (
                unsupported_target_ids
                if support_status in {"limited_supported", "limited_supported_prior_only"}
                else []
            ),
            "n_excluded_hypothetical_or_pre_nomination": len(hypothetical_ids),
            "raw_poll_ids": sorted(raw.get("poll_id", pd.Series(dtype=str)).astype(str).tolist()),
            "compatible_poll_ids": sorted(
                compatible.get("poll_id", pd.Series(dtype=str)).astype(str).tolist()
            ),
            "model_safe_poll_ids": sorted(
                model_safe.get("poll_id", pd.Series(dtype=str)).astype(str).tolist()
            ),
            "excluded_poll_ids": sorted({str(item.get("poll_id") or "") for item in excluded}),
            "matchup_ids_seen": matchups,
            "latest_compatible_field_end": latest("field_end"),
            "latest_compatible_available_at": latest("available_at"),
            "status": forecast_status,
            "forecast_status": forecast_status,
            "evidence_status": evidence_status,
            "evidence_reasons": evidence_reasons,
            "reasons": sorted(set(
                hard_reasons
                + warning_reasons
                + [
                    reason for reason in reasons
                    if not (
                        support_status in {"limited_supported", "limited_supported_prior_only"}
                        and reason == "race_ineligible_for_binary_scoring"
                    )
                ]
            )),
        })
    records.sort(key=lambda item: item["race_id"])
    semantic = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "as_of": cutoff,
        "candidate_state_snapshot_sha256": candidate_metadata.get("snapshot_sha256"),
        "races": records,
    }
    fingerprint = _canonical_sha256(semantic)
    return {
        **semantic,
        "artifact_sha256": fingerprint,
        "summary": {
            "n_races": len(records),
            "n_pass": sum(item["forecast_status"] == "pass" for item in records),
            "n_warning": sum(item["forecast_status"] == "warning" for item in records),
            "n_fail": sum(item["forecast_status"] == "fail" for item in records),
            "n_evidence_fail": sum(item["evidence_status"] == "fail" for item in records),
            "evidence_ready": not any(item["evidence_status"] == "fail" for item in records),
            "forecast_complete": not any(item["forecast_status"] == "fail" for item in records),
            "promotion_eligible": not any(item["forecast_status"] == "fail" for item in records),
            "source_and_model_coverage_separated": True,
        },
    }


def write_current_race_poll_coverage(report: dict[str, Any], path: Path | None = None) -> Path:
    path = path or (ARTIFACTS_DIR / "current_race_poll_coverage_v0923.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
