"""Personal Senate incumbency: officeholder running, not seat party.

Distinguishes:
1. ``held_by`` / seat-holding party
2. incumbent officeholder identity
3. whether that officeholder is the modeled general-election candidate

Fundamentals ``incumbency`` is +1 / -1 / 0 from (3) only. Seat party alone
never awards a personal-incumbency bonus.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def personal_incumbency_signed(
    row: pd.Series | dict[str, Any],
) -> float:
    """Return fundamentals incumbency channel in Dem-margin sign convention.

    +1 — modeled (typically Dem / Dem-caucus) candidate is the sitting senator
         running for reelection
    -1 — opposing (typically Republican) candidate is the sitting senator
         running for reelection
    0  — open seat, retiring/defeated incumbent, appointed seat without the
         officeholder as the modeled nominee, or insufficient identity evidence

    Fail closed to 0 when personal incumbency cannot be established from
    explicit candidate-level flags. Never infer from ``held_by`` /
    ``incumbent_party`` alone.
    """
    modeled = row.get("modeled_candidate_is_incumbent")
    opposing = row.get("opposing_candidate_is_incumbent")
    if modeled is None and opposing is None:
        # Historical folds without personal-incumbency metadata: fail closed.
        return 0.0
    modeled_flag = bool(modeled) if pd.notna(modeled) else False
    opposing_flag = bool(opposing) if pd.notna(opposing) else False
    if modeled_flag and opposing_flag:
        # Ill-formed: two personal incumbents cannot share one seat.
        return 0.0
    if modeled_flag:
        return 1.0
    if opposing_flag:
        return -1.0
    return 0.0


def incumbency_provenance(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    """Compact provenance for audits / decomposition."""
    return {
        "modeled_candidate_is_incumbent": bool(
            row.get("modeled_candidate_is_incumbent")
        )
        if row.get("modeled_candidate_is_incumbent") is not None
        and pd.notna(row.get("modeled_candidate_is_incumbent"))
        else None,
        "opposing_candidate_is_incumbent": bool(
            row.get("opposing_candidate_is_incumbent")
        )
        if row.get("opposing_candidate_is_incumbent") is not None
        and pd.notna(row.get("opposing_candidate_is_incumbent"))
        else None,
        "sitting_senator_name": row.get("sitting_senator_name"),
        "personal_incumbency": personal_incumbency_signed(row),
        "held_by": row.get("held_by"),
        "incumbent_party": row.get("incumbent_party"),
        "is_open": bool(row.get("is_open")) if row.get("is_open") is not None else None,
        "basis": "explicit_candidate_personal_incumbency_v1",
    }
