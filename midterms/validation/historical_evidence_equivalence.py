"""Semantic comparison of formal historical evidence across code boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.evidence.warehouse import Warehouse

SCHEMA_VERSION = "historical-evidence-equivalence-v1"
FORMAL_CUTOFFS = {
    "senate-2018": ("2018-09-07", "2018-10-07"),
    "senate-2020": ("2020-09-04", "2020-10-04"),
    "senate-2022": ("2022-09-09", "2022-10-09"),
    "senate-2024": ("2024-09-06", "2024-10-06"),
}
POLL_FIELDS = (
    "poll_id", "race_id", "two_party_margin", "available_at",
    "dem_candidate_id", "dem_candidate_name", "rep_candidate_id",
    "rep_candidate_name", "matchup_id", "hypothetical",
    "exclusion_status", "exclusion_reason",
)
RACE_FIELDS = (
    "race_id", "state", "not_up", "binary_score_eligible",
    "candidate_state", "candidate_state_reason", "candidate_timeline_status",
    "modeled_candidate_id", "modeled_candidate_name", "modeled_ballot_party",
    "opposing_candidate_id", "opposing_candidate_name", "opposing_ballot_party",
)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(_clean(payload), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _rows(frame: pd.DataFrame, fields: tuple[str, ...], key: tuple[str, ...]) -> list[dict[str, Any]]:
    records = [
        {field: _clean(row.get(field)) for field in fields}
        for _, row in frame.iterrows()
    ]
    return sorted(records, key=lambda row: tuple(str(row.get(column) or "") for column in key))


def build_historical_projection(warehouse: Warehouse | None = None) -> dict[str, Any]:
    warehouse = warehouse or Warehouse()
    cutoffs: dict[str, Any] = {}
    for election_id, cutoff_dates in FORMAL_CUTOFFS.items():
        for cutoff in cutoff_dates:
            snapshot = warehouse.build_as_of(cutoff, election_id)
            value = {
                "races": _rows(snapshot.races, RACE_FIELDS, ("race_id",)),
                "polls": _rows(snapshot.polls, POLL_FIELDS, ("poll_id", "race_id")),
                "candidate_timeline": _clean(snapshot.candidate_timeline),
            }
            value["semantic_sha256"] = _canonical_sha256(value)
            cutoffs[f"{election_id}@{cutoff}"] = value
    return {
        "schema_version": "historical-evidence-projection-v1",
        "model_version": MODEL_VERSION,
        "cutoffs": cutoffs,
    }


def compare_historical_projections(
    before: dict[str, Any], after: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    keys = sorted(set(before.get("cutoffs") or {}) | set(after.get("cutoffs") or {}))
    for key in keys:
        left = (before.get("cutoffs") or {}).get(key)
        right = (after.get("cutoffs") or {}).get(key)
        changed_sections = [
            section for section in ("races", "polls", "candidate_timeline")
            if (left or {}).get(section) != (right or {}).get(section)
        ]
        rows.append({
            "cutoff": key,
            "equivalent": not changed_sections,
            "before_semantic_sha256": (left or {}).get("semantic_sha256"),
            "after_semantic_sha256": (right or {}).get("semantic_sha256"),
            "changed_sections": changed_sections,
            "before_counts": {
                "races": len((left or {}).get("races") or []),
                "polls": len((left or {}).get("polls") or []),
            },
            "after_counts": {
                "races": len((right or {}).get("races") or []),
                "polls": len((right or {}).get("polls") or []),
            },
        })
    equivalent = all(row["equivalent"] for row in rows)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "reference_model_version": before.get("model_version"),
        "candidate_model_version": after.get("model_version") or MODEL_VERSION,
        "classification": "historically_equivalent" if equivalent else "historical_inputs_changed",
        "formal_cutoffs": rows,
        "comparison_fields": {
            "races": list(RACE_FIELDS),
            "polls": list(POLL_FIELDS),
            "candidate_state_metadata": True,
        },
        "reusable_lineage_claim": (
            "The reference and candidate historical input projections are semantically "
            "identical at every formal cutoff. This comparison does not relabel model-fit "
            "or forecast artifacts."
            if equivalent else
            "One or more formal historical inputs changed; historical validation must be rebuilt."
        ),
    }
    payload["comparison_sha256"] = _canonical_sha256(payload)
    return payload


def write_historical_equivalence_report(
    before_path: Path,
    path: Path | None = None,
    *,
    warehouse: Warehouse | None = None,
) -> dict[str, Any]:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = build_historical_projection(warehouse)
    report = compare_historical_projections(before, after)
    path = path or (ARTIFACTS_DIR / "historical_evidence_equivalence_v0923.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def rebind_equivalent_projection_lineage(
    path: Path | None = None,
    *,
    current_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance only the code-version label after proving inputs are unchanged.

    This is deliberately narrower than rebuilding historical validation.  Every
    current projection hash must equal the previously recorded candidate-side
    hash.  A changed cutoff, missing cutoff, or changed semantic hash fails
    closed instead of blessing stale historical evidence.
    """
    path = path or (ARTIFACTS_DIR / "historical_evidence_equivalence_v0923.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("historical equivalence schema is unsupported")
    if report.get("classification") != "historically_equivalent":
        raise ValueError("historical inputs are not classified as equivalent")

    projection = current_projection or build_historical_projection()
    rows = {str(row.get("cutoff")): row for row in report.get("formal_cutoffs") or []}
    current = projection.get("cutoffs") or {}
    if set(rows) != set(current):
        raise ValueError("historical cutoff set changed")
    for cutoff, value in current.items():
        expected = rows[cutoff].get("after_semantic_sha256")
        actual = value.get("semantic_sha256")
        if not expected or actual != expected:
            raise ValueError(f"historical projection changed at {cutoff}")

    previous = report.get("candidate_model_version")
    report["candidate_model_version"] = MODEL_VERSION
    report["reusable_lineage_claim"] = (
        "The recorded candidate projection was recomputed under the current code and "
        "remains semantically identical at every formal cutoff. No historical model-fit "
        "or forecast artifact was relabeled."
    )
    report["lineage_rebind"] = {
        "from_model_version": previous,
        "to_model_version": MODEL_VERSION,
        "verification": "all_current_projection_hashes_equal_recorded_candidate_hashes",
        "n_verified_cutoffs": len(current),
    }
    report.pop("comparison_sha256", None)
    report["comparison_sha256"] = _canonical_sha256(report)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
