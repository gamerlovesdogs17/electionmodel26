"""Deterministic, point-in-time relative-baseline provenance framework.

Records are generic numerical observations. This module does not load or
populate any current political prior values.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any

SCHEMA_VERSION = "relative-prior-source-v1"
METHOD_VERSION = "weighted-relative-baseline-v1"


@dataclass(frozen=True)
class PriorSource:
    source_id: str
    entity_id: str
    observation_date: date
    available_at: date
    source_sha256: str
    source_url: str
    local_value: float
    national_reference: float
    source_kind: str = "observed"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported prior source schema version")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256.lower()):
            raise ValueError("source_sha256 must be a SHA-256 digest")
        if self.available_at < self.observation_date:
            raise ValueError("available_at cannot precede observation_date")
        if not self.source_id or not self.entity_id or not self.source_url:
            raise ValueError("source identity and attribution are required")
        if not isfinite(self.local_value) or not isfinite(self.national_reference):
            raise ValueError("source values must be finite")
        if self.source_kind not in {"observed", "synthetic_fixture"}:
            raise ValueError("unsupported source kind")


def available_sources(observations: Sequence[PriorSource], *, as_of: date) -> tuple[PriorSource, ...]:
    """Expose only observations actually knowable at a historical snapshot."""
    return tuple(
        row for row in observations
        if row.observation_date <= as_of and row.available_at <= as_of
    )


def derive_relative_prior(
    observations: Sequence[PriorSource],
    *,
    entity_id: str,
    as_of: date,
    source_weights: dict[str, float],
    allow_fixture_fallback: bool = False,
    fixture_value: float | None = None,
) -> dict[str, Any]:
    """Compute a declared weighted relative baseline with strict vintage checks."""
    if not source_weights:
        if not allow_fixture_fallback or fixture_value is None:
            raise ValueError("source_weights required unless explicit fixture fallback is enabled")
        if not isfinite(fixture_value):
            raise ValueError("fixture fallback must be finite")
        payload = {
            "entity_id": entity_id,
            "as_of": as_of.isoformat(),
            "final_prior": float(fixture_value),
            "method": "fixture_fallback",
            "method_version": METHOD_VERSION,
            "schema_version": SCHEMA_VERSION,
            "production_eligible": False,
            "provenance": {"source_kind": "synthetic_fixture", "reason": "explicit fixture fallback"},
        }
        payload["snapshot_sha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        return payload
    if (any(not isfinite(weight) or weight < 0 for weight in source_weights.values())
            or abs(sum(source_weights.values()) - 1.0) > 1e-10):
        raise ValueError("predeclared source weights must be nonnegative and sum to one")
    by_id = {row.source_id: row for row in observations}
    if len(by_id) != len(observations):
        raise ValueError("duplicate source_id")
    components = []
    for source_id, weight in sorted(source_weights.items()):
        if source_id not in by_id:
            raise ValueError(f"declared source missing: {source_id}")
        row = by_id[source_id]
        if row.entity_id != entity_id:
            raise ValueError(f"entity mismatch for {source_id}")
        if row.observation_date > as_of or row.available_at > as_of:
            raise ValueError(f"future source rejected: {source_id}")
        relative = float(row.local_value - row.national_reference)
        components.append({
            "source_id": source_id,
            "source_election_date": row.observation_date.isoformat(),
            "available_at": row.available_at.isoformat(),
            "source_sha256": row.source_sha256,
            "source_url": row.source_url,
            "source_kind": row.source_kind,
            "local_value": float(row.local_value),
            "national_reference": float(row.national_reference),
            "relative_value": relative,
            "weight": float(weight),
            "weighted_component": float(weight * relative),
        })
    final = sum(component["weighted_component"] for component in components)
    payload = {
        "entity_id": entity_id,
        "as_of": as_of.isoformat(),
        "final_prior": float(final),
        "method": "recency_weighted_relative_baseline",
        "method_version": METHOD_VERSION,
        "schema_version": SCHEMA_VERSION,
        "production_eligible": all(c["source_kind"] == "observed" for c in components),
        "provenance": {"components": components, "weights_predeclared": True},
    }
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return payload
