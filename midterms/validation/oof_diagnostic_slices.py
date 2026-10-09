"""Predeclared diagnostic slices for historical OOF scoring/reporting."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

SLICE_SCHEMA = "oof-diagnostic-slices-v1"

# Absolute gap (pp) between structural prior and poll signal for disagreement slice.
PRIOR_POLL_DISAGREE_ABS_PP = 8.0


def enop_bucket(enop: float) -> str:
    if enop <= 0:
        return "zero_polls"
    if enop < 1.5:
        return "very_low_enop"
    if enop < 4.0:
        return "moderate_enop"
    return "high_enop"


def competitiveness_bucket(structural_abs_margin: float) -> str:
    m = abs(float(structural_abs_margin))
    if m < 5:
        return "tossup_band"
    if m < 12:
        return "lean_band"
    return "likely_safe_band"


def assign_diagnostic_slices(
    races: pd.DataFrame,
    *,
    enop_by_race: dict[str, float] | None = None,
    poll_signal_by_race: dict[str, float] | None = None,
    lead_days: int | None = None,
    contest_category_by_race: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Attach predeclared slice labels without using held-out certified results."""
    enop_by_race = enop_by_race or {}
    poll_signal_by_race = poll_signal_by_race or {}
    contest_category_by_race = contest_category_by_race or {}
    out = []
    for _, row in races.iterrows():
        rid = str(row.get("race_id") or "")
        prior = float(row.get("prior_lean") or 0.0)
        enop = float(enop_by_race.get(rid, 0.0))
        poll_sig = poll_signal_by_race.get(rid)
        disagree = None
        if poll_sig is not None and np.isfinite(prior) and np.isfinite(poll_sig):
            disagree = abs(float(poll_sig) - prior) >= PRIOR_POLL_DISAGREE_ABS_PP
        incumbent = bool(row.get("modeled_candidate_is_incumbent")) or bool(
            row.get("opposing_candidate_is_incumbent")
        )
        seat = "personal_incumbent" if incumbent else "open_non_incumbent"
        lead_bucket = None
        if lead_days is not None:
            lead_bucket = f"T{int(lead_days)}"
        out.append(
            {
                "race_id": rid,
                "poll_coverage": enop_bucket(enop),
                "prior_poll_disagreement": disagree,
                "seat_status": seat,
                "competitiveness": competitiveness_bucket(prior),
                "lead_time": lead_bucket,
                "contest_structure": contest_category_by_race.get(rid, "ordinary_d_v_r"),
                "enop": enop,
                "structural_prior": prior,
                "poll_signal": poll_sig,
                "disagreement_threshold_pp": PRIOR_POLL_DISAGREE_ABS_PP,
            }
        )
    return out


def slice_registry() -> dict[str, Any]:
    return {
        "schema_version": SLICE_SCHEMA,
        "poll_coverage": ["zero_polls", "very_low_enop", "moderate_enop", "high_enop"],
        "prior_poll_disagreement_abs_pp": PRIOR_POLL_DISAGREE_ABS_PP,
        "seat_status": ["personal_incumbent", "open_non_incumbent"],
        "competitiveness": ["tossup_band", "lean_band", "likely_safe_band"],
        "lead_time": ["T60", "T30"],
        "contest_structure": [
            "ordinary_d_v_r",
            "candidate_neutral_binary",
            "multiway",
        ],
        "note": "Diagnostics only; not separate state-specific models.",
    }
