"""Domain-aware evidence freshness classification."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


FRESHNESS_POLICY_VERSION = "domain-freshness-v1"
DEFAULT_MAX_AGES_DAYS = {
    "polls": {"retrieval": 7, "observation": 28},
    "markets": {"retrieval": 2, "observation": 2},
    "finance": {"retrieval": 14, "observation": 45},
    "ratings": {"retrieval": 14, "observation": 45},
    "economics": {"retrieval": 35, "observation": 120},
    "candidate_ballot": {"retrieval": 14, "observation": 60},
}


def classify_freshness(
    domain: str,
    *,
    checked_at: str | datetime | None = None,
    retrieved_at: str | datetime | None = None,
    observed_at: str | datetime | None = None,
    source_available: bool = True,
    refresh_status: str = "ok",
    parser_status: str = "ok",
    schema_status: str = "ok",
    policy: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    limits = (policy or DEFAULT_MAX_AGES_DAYS).get(domain)
    if limits is None:
        raise KeyError(f"freshness policy is undefined for {domain}")
    now = pd.Timestamp(checked_at or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    reasons: list[str] = []
    if parser_status != "ok":
        status = "parser_failed"
        reasons.append(parser_status)
    elif schema_status != "ok":
        status = "schema_discontinuity"
        reasons.append(schema_status)
    elif refresh_status != "ok":
        status = "refresh_failed"
        reasons.append(refresh_status)
    elif not source_available:
        status = "unavailable"
    elif retrieved_at is None:
        status = "stale_retrieval"
        reasons.append("retrieval timestamp missing")
    else:
        retrieved = pd.Timestamp(retrieved_at)
        if retrieved.tzinfo is None:
            retrieved = retrieved.tz_localize("UTC")
        retrieval_age = float((now - retrieved).total_seconds() / 86400.0)
        if retrieval_age > limits["retrieval"]:
            status = "stale_retrieval"
        elif observed_at is None:
            status = "unavailable"
            reasons.append("no observation exists")
        else:
            observed = pd.Timestamp(observed_at)
            if observed.tzinfo is None:
                observed = observed.tz_localize("UTC")
            observation_age = float((now - observed).total_seconds() / 86400.0)
            status = "stale_observation" if observation_age > limits["observation"] else "fresh"
    return {
        "policy_version": FRESHNESS_POLICY_VERSION,
        "domain": domain,
        "status": status,
        "limits_days": dict(limits),
        "source_available": bool(source_available),
        "reasons": reasons,
    }
