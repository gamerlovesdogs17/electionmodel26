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

COVERAGE_SCHEMA_VERSION = "current-race-poll-coverage-v1"


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

    records: list[dict[str, Any]] = []
    for _, race in races.iterrows():
        if bool(race.get("not_up")) or not is_active_ballot_row(race):
            continue
        race_id = str(race.get("race_id") or "")
        if not race_id.startswith("senate-2026-"):
            continue
        raw = raw_polls[raw_polls["race_id"].astype(str).eq(race_id)].copy()
        compatible = compatible_polls[
            compatible_polls["race_id"].astype(str).eq(race_id)
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
        binary_eligible = bool(state.get("binary_score_eligible", True))
        structure = (
            "ranked_choice_multiway" if str(race.get("state")) == "AK"
            else "non_major_party_vs_republican" if modeled_party not in {"D", "DEM", "DEMOCRAT", "DEMOCRATIC"}
            else "binary_dem_vs_rep"
        )
        identity_sensitive = bool(
            state.get("candidate_identity_required")
            or len(matchups) > 1
            or structure != "binary_dem_vs_rep"
        )
        hard_reasons: list[str] = []
        warning_reasons: list[str] = []
        if not binary_eligible:
            hard_reasons.append(str(state.get("binary_score_exclusion_reason") or "unsupported_binary_target"))
        if state.get("candidate_identity_required") and not state.get("candidate_identity_resolved"):
            hard_reasons.append("current_candidate_identity_unresolved")
        if identity_sensitive and len(raw) and not len(compatible):
            hard_reasons.append("identity_sensitive_race_has_zero_compatible_polls")
        elif identity_sensitive and not len(raw):
            warning_reasons.append("identity_sensitive_race_has_zero_raw_polls")
        status = "fail" if hard_reasons else "warning" if warning_reasons else "pass"

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
            },
            "contest_structure": structure,
            "binary_score_eligible": binary_eligible,
            "identity_sensitive": identity_sensitive,
            "qa_universe": identity_sensitive,
            "n_raw_current_polls": len(raw),
            "n_candidate_compatible_polls": len(compatible),
            "n_excluded_obsolete_or_incompatible": len(candidate_excluded_ids),
            "n_excluded_identity_unresolved": len(unresolved_ids),
            "n_excluded_unsupported_target": len(unsupported_target_ids),
            "n_excluded_hypothetical_or_pre_nomination": len(hypothetical_ids),
            "raw_poll_ids": sorted(raw.get("poll_id", pd.Series(dtype=str)).astype(str).tolist()),
            "compatible_poll_ids": sorted(
                compatible.get("poll_id", pd.Series(dtype=str)).astype(str).tolist()
            ),
            "excluded_poll_ids": sorted({str(item.get("poll_id") or "") for item in excluded}),
            "matchup_ids_seen": matchups,
            "latest_compatible_field_end": latest("field_end"),
            "latest_compatible_available_at": latest("available_at"),
            "status": status,
            "reasons": sorted(set(hard_reasons + warning_reasons + reasons)),
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
            "n_pass": sum(item["status"] == "pass" for item in records),
            "n_warning": sum(item["status"] == "warning" for item in records),
            "n_fail": sum(item["status"] == "fail" for item in records),
            "promotion_eligible": not any(item["status"] == "fail" for item in records),
        },
    }


def write_current_race_poll_coverage(report: dict[str, Any], path: Path | None = None) -> Path:
    path = path or (ARTIFACTS_DIR / "current_race_poll_coverage_v0923.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
