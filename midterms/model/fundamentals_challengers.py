"""Off-by-default, provenance-gated fundamentals challenger features."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FundamentalsChallengerConfig:
    candidate_experience: bool = False
    recent_statewide_performance: bool = False
    special_election_signal: bool = False
    ridge_lambda: float = 50.0
    version: str = "fundamentals-challengers-v1"


FEATURES = (
    "candidate_experience",
    "recent_statewide_performance",
    "special_election_signal",
)


def challenger_feature_row(
    row: pd.Series,
    *,
    as_of: str | date,
    config: FundamentalsChallengerConfig | None = None,
) -> dict[str, Any]:
    """Return regularized-design inputs and fail-closed provenance status."""
    cfg = config or FundamentalsChallengerConfig()
    cutoff = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    values: dict[str, float] = {}
    provenance: dict[str, Any] = {}
    problems: list[str] = []
    for feature in FEATURES:
        if not bool(getattr(cfg, feature)):
            continue
        value = row.get(feature)
        available = pd.to_datetime(row.get(f"{feature}_available_at"), errors="coerce")
        source_hash = row.get(f"{feature}_source_hash")
        missing = pd.isna(value)
        future = pd.notna(available) and available.date() > cutoff
        valid = not missing and pd.notna(available) and not future and bool(source_hash)
        values[feature] = float(value) if valid else 0.0
        values[f"{feature}_missing"] = 0.0 if valid else 1.0
        provenance[feature] = {
            "available_at": available.date().isoformat() if pd.notna(available) else None,
            "source_hash": source_hash,
            "valid_as_of": valid,
        }
        if not valid:
            problems.append(feature)
    return {
        "config": asdict(cfg),
        "values": values,
        "provenance": provenance,
        "production_eligible": not problems,
        "missing_or_ineligible": problems,
        "coefficient_policy": "ridge_shrinkage_toward_zero",
    }
