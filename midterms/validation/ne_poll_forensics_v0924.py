"""Nebraska poll-coverage forensic ledger for v0.9.24."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.evidence.warehouse import Warehouse


def _norm_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold())
    return " ".join(text.split())


OSBORN_ALIASES = {
    "dan osborn",
    "daniel osborn",
    "osborn",
    "votehub:dan-osborn",
    "dan-osborn",
}
RICKETTS_ALIASES = {
    "pete ricketts",
    "peter ricketts",
    "ricketts",
    "votehub:pete-ricketts",
    "pete-ricketts",
}


def _is_osborn(value: Any) -> bool:
    key = _norm_name(value)
    return key in OSBORN_ALIASES or "osborn" in key


def _is_ricketts(value: Any) -> bool:
    key = _norm_name(value)
    return key in RICKETTS_ALIASES or "ricketts" in key


def build_ne_poll_forensics() -> dict[str, Any]:
    registry = load_current_candidate_registry()
    race = next(r for r in registry["races"] if r["state"] == "NE")
    race_id = str(race["race_id"])
    raw = pd.read_parquet(NORMALIZED_DIR / "polls.parquet")
    raw = raw[
        raw["election_id"].astype(str).eq("senate-2026")
        & raw["state"].astype(str).eq("NE")
    ].copy()
    # Broader: any poll mentioning Nebraska senate 2026 in race_id or state
    all_ne = raw.copy()

    wh = Warehouse(ensure_fixtures=False)
    snap = wh.build_as_of(str(registry["reviewed_as_of"]), "senate-2026")
    meta = snap.candidate_timeline or {}
    compatible_ids = {
        str(pid)
        for pid in (meta.get("candidate_compatible_poll_ids_by_race") or {}).get(race_id, [])
    }
    exclusions = [
        item
        for item in (meta.get("poll_exclusions") or [])
        if str(item.get("race_id") or "") in {race_id, "NE", "senate-2026-NE"}
        or "NE" in str(item.get("race_id") or "")
    ]
    exclusion_by_poll = {
        str(item.get("poll_id") or ""): item for item in exclusions if item.get("poll_id")
    }

    ledger: list[dict[str, Any]] = []
    for _, poll in all_ne.iterrows():
        poll_id = str(poll.get("poll_id") or "")
        matchup = str(poll.get("matchup_id") or "")
        dem_name = poll.get("dem_candidate") or poll.get("modeled_candidate")
        rep_name = poll.get("rep_candidate") or poll.get("opposing_candidate")
        osborn = _is_osborn(dem_name) or _is_osborn(matchup)
        ricketts = _is_ricketts(rep_name) or _is_ricketts(matchup)
        exact_pair = osborn and ricketts
        included = poll_id in compatible_ids
        excl = exclusion_by_poll.get(poll_id)
        if included:
            reason = None
            classification = "included_candidate_compatible"
        elif excl:
            reason = str(excl.get("reason") or "candidate_state_contract_exclusion")
            classification = "A_filter_exclusion" if "identity" in reason or "matchup" in reason or "hypothetical" in reason else "A_filter_exclusion"
        elif exact_pair:
            reason = "exact_osborn_ricketts_matchup_not_in_compatible_set"
            classification = "A_possible_ingestion_or_filter_bug"
        elif not matchup:
            reason = "missing_matchup_identity_metadata"
            classification = "B_or_A_metadata"
        else:
            reason = "wrong_candidates_or_non_reviewed_matchup"
            classification = "B_source_or_matchup_limit"

        ledger.append(
            {
                "poll_id": poll_id,
                "pollster": poll.get("pollster") or poll.get("pollster_id"),
                "field_end": str(poll.get("field_end") or ""),
                "sample_size": poll.get("sample_size"),
                "population": poll.get("population"),
                "matchup_id": matchup,
                "dem_or_modeled_name": dem_name,
                "rep_or_opposing_name": rep_name,
                "osborn_alias_resolved": osborn,
                "ricketts_alias_resolved": ricketts,
                "exact_osborn_ricketts_matchup": exact_pair,
                "included": included,
                "exclusion_reason": reason,
                "classification": classification,
                "source_url": poll.get("source_url"),
            }
        )

    n_raw = len(ledger)
    n_eligible = sum(1 for row in ledger if row["included"])
    n_exact = sum(1 for row in ledger if row["exact_osborn_ricketts_matchup"])
    n_exact_excluded = sum(
        1 for row in ledger if row["exact_osborn_ricketts_matchup"] and not row["included"]
    )
    bug = n_exact_excluded > 0
    return {
        "schema_version": "ne-poll-forensics-v0924",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "race_id": race_id,
        "reviewed_pair": {
            "modeled": race["modeled_candidate_name"],
            "opposing": race["opposing_candidate_name"],
            "modeled_ballot_party": race["modeled_ballot_party"],
            "modeled_caucus": race["modeled_caucus"],
        },
        "n_raw_ne_polls": n_raw,
        "n_candidate_compatible": n_eligible,
        "n_exact_osborn_ricketts_matchups": n_exact,
        "n_exact_excluded": n_exact_excluded,
        "diagnosis": (
            "A_ingestion_filtering_bug"
            if bug
            else "B_actual_source_coverage_limitation"
        ),
        "repair_applied": False,
        "repair_note": (
            "Exact Osborn-Ricketts rows exist in normalized store but were excluded; inspect candidate_timeline filters."
            if bug
            else "Eligible count matches stored candidate-compatible set; public sites may use out-of-architecture sources."
        ),
        "outside_architecture_sources_added": False,
        "ledger": ledger,
    }


def write_ne_poll_forensics() -> dict[str, Any]:
    payload = build_ne_poll_forensics()
    # If exact pair excluded, attempt a narrow alias-repair diagnostic only when
    # the exclusion reason is identity-related; do not broaden sources.
    if payload["diagnosis"] == "A_ingestion_filtering_bug":
        payload["repair_note"] += (
            " No automatic production filter rewrite in this cheap diagnostic; "
            "alias helpers documented in this module for follow-up if timeline "
            "normalization is confirmed at fault."
        )
    path = ARTIFACTS_DIR / "ne_poll_forensics_audit_v0924.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
