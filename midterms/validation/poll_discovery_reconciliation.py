"""Current-cycle poll discovery reconciliation (find missing individual polls)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.evidence.ingest import _parse_subject_state, normalize_candidate_key
from midterms.evidence.race_scoped_identity import (
    build_race_identity_index,
    resolve_race_candidate,
)
from midterms.model.contest_classifier import classify_contest_structure

ABSENCE_CATEGORIES = (
    "not_present_in_source_api",
    "candidate_mismatch",
    "pre_nomination_matchup",
    "duplicate",
    "publication_after_as_of",
    "unsupported_contest_structure",
    "source_inaccessible",
    "parser_failure",
    "identity_resolution_failure",
)


def _load_votehub_raw() -> list[dict]:
    path = RAW_DIR / "external" / "votehub_us_senator.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    polls = payload.get("polls", payload) if isinstance(payload, dict) else payload
    return list(polls or [])


def build_current_poll_discovery_reconciliation(
    *,
    as_of: str | date | None = None,
    warehouse_path: Path | None = None,
) -> dict[str, Any]:
    registry = load_current_candidate_registry()
    cutoff = (
        date.fromisoformat(str(as_of)[:10])
        if as_of
        else date.fromisoformat(str(registry["reviewed_as_of"]))
    )
    identity = build_race_identity_index(list(registry.get("races") or []))
    raw_polls = _load_votehub_raw()
    wh_path = warehouse_path or (NORMALIZED_DIR / "polls.parquet")
    warehouse = pd.read_parquet(wh_path) if wh_path.is_file() else pd.DataFrame()

    races_out = []
    for race in registry.get("races") or []:
        race_id = str(race["race_id"])
        state = str(race["state"])
        structure = classify_contest_structure(
            ballot_candidates=list(race.get("ballot_candidates") or []),
            contest_structure_hint=str(race.get("contest_structure") or ""),
            state=state,
        )
        source_for_state = []
        for p in raw_polls:
            st, is_primary = _parse_subject_state(str(p.get("subject") or ""))
            if is_primary or st != state:
                continue
            source_for_state.append(p)

        compatible = []
        exact = []
        excluded = []
        pollsters = set()
        field_dates = []
        avail_dates = []
        for p in source_for_state:
            answers = p.get("answers") or []
            names = [str(a.get("choice") or "") for a in answers]
            resolved = [
                resolve_race_candidate(race_id, n, index=identity, allow_global_fallback=False)
                for n in names
                if normalize_candidate_key(n) not in {"undecided", "unsure", "not sure", ""}
            ]
            resolved_ok = [r for r in resolved if r is not None]
            pollsters.add(str(p.get("pollster") or "unknown"))
            fe = str(p.get("end_date") or p.get("start_date") or "")[:10]
            av = str(p.get("created_at") or fe)[:10]
            if fe:
                field_dates.append(fe)
            if av:
                avail_dates.append(av)
            modeled = race.get("modeled_candidate_id")
            opposing = race.get("opposing_candidate_id")
            ids = {r.candidate_id for r in resolved_ok}
            if modeled in ids and opposing in ids:
                exact.append(p)
                compatible.append(p)
            elif resolved_ok:
                compatible.append(p)
                excluded.append(
                    {
                        "poll_id": f"vh-{p.get('id')}",
                        "category": "candidate_mismatch",
                        "reason": "resolved_but_not_exact_current_matchup",
                    }
                )
            else:
                excluded.append(
                    {
                        "poll_id": f"vh-{p.get('id')}",
                        "category": "identity_resolution_failure",
                        "reason": "no_race_scoped_resolution",
                    }
                )
            if av and date.fromisoformat(av[:10]) > cutoff:
                excluded.append(
                    {
                        "poll_id": f"vh-{p.get('id')}",
                        "category": "publication_after_as_of",
                        "reason": f"available_at={av} > as_of={cutoff.isoformat()}",
                    }
                )

        wh_race = (
            warehouse[warehouse["race_id"].astype(str) == race_id]
            if len(warehouse) and "race_id" in warehouse.columns
            else pd.DataFrame()
        )
        wh_ids = set(wh_race["poll_id"].astype(str)) if len(wh_race) and "poll_id" in wh_race.columns else set()
        source_ids = {f"vh-{p.get('id')}" for p in source_for_state}
        missing_from_warehouse = sorted(source_ids - wh_ids)
        known_absent = [
            {
                "poll_id": pid,
                "category": "parser_failure"
                if pid in {e["poll_id"] for e in excluded if e["category"] == "identity_resolution_failure"}
                else "not_present_in_normalized_warehouse",
                "reason": "present_in_source_feed_absent_from_warehouse",
            }
            for pid in missing_from_warehouse
        ]

        races_out.append(
            {
                "race_id": race_id,
                "state": state,
                "contest_classification": structure,
                "n_source_feed_polls": len(source_for_state),
                "n_candidate_compatible": len(compatible),
                "n_exact_current_matchup": len(exact),
                "n_excluded": len(excluded),
                "latest_field_date": max(field_dates) if field_dates else None,
                "latest_available_at": max(avail_dates) if avail_dates else None,
                "pollsters": sorted(pollsters),
                "warehouse_poll_count": len(wh_race),
                "known_discoverable_absent_from_warehouse": known_absent,
                "exclusion_samples": excluded[:20],
                "absence_category_counts": dict(Counter(e["category"] for e in excluded)),
            }
        )

    sparse_focus = [r for r in races_out if r["state"] in {"NE", "ID", "MT", "KS", "IA"}]
    payload = {
        "schema_version": "current-poll-discovery-reconciliation-v1",
        "model_version": MODEL_VERSION,
        "as_of": cutoff.isoformat(),
        "source_feed": "votehub_us_senator",
        "n_races": len(races_out),
        "races": races_out,
        "sparse_state_focus": sparse_focus,
        "absence_categories": list(ABSENCE_CATEGORIES),
        "note": (
            "Discovery audit only — does not ingest another site's polling average. "
            "Missing individual polls require original-release reconciliation."
        ),
    }
    return payload


def write_poll_discovery_artifact(**kwargs: Any) -> dict[str, Any]:
    payload = build_current_poll_discovery_reconciliation(**kwargs)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "current_poll_discovery_reconciliation_v0924.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
