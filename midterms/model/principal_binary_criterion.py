"""Predeclared principal-binary-with-minors criterion from historical analogs.

Thresholds are fixed from the 2014–2024 candidate-level plurality analog set
before inspecting 2026 probabilities. Coverage before 2014 is explicitly absent.
"""

from __future__ import annotations

from typing import Any

# Predeclared from historical candidate-level plurality races (2014–2024):
# top-two combined share p10 ≈ 0.923; third-place share p90 ≈ 0.050.
# Transparent band separating typical Libertarian/Green residuals from
# ME/NM/ND-style material thirds (third ≳ 0.08–0.10).
PRINCIPAL_BINARY_MIN_TOP_TWO_SHARE = 0.90
PRINCIPAL_BINARY_MAX_THIRD_SHARE = 0.08
PRINCIPAL_POLL_SHARE_FLOOR = 0.05
CRITERION_ID = "principal-binary-with-minors-v1-historical-2014-2024"


def classify_share_profile(
    *,
    top_two_combined_share: float,
    third_place_share: float,
) -> str:
    """Return principal_binary_with_minor_residual or genuine_multiway_plurality."""
    if (
        float(top_two_combined_share) >= PRINCIPAL_BINARY_MIN_TOP_TWO_SHARE
        and float(third_place_share) <= PRINCIPAL_BINARY_MAX_THIRD_SHARE
    ):
        return "principal_binary_with_minor_residual"
    return "genuine_multiway_plurality"


def summarize_analog_share_distribution(analogs: list[dict[str, Any]]) -> dict[str, Any]:
    """Distribution diagnostics used to justify the predeclared thresholds."""
    top2: list[float] = []
    third: list[float] = []
    residual: list[float] = []
    sep_w: list[float] = []
    sep_ru: list[float] = []
    class_counts = {
        "ballot_multiway": 0,
        "principal_binary_with_minor_residual": 0,
        "materially_multiway": 0,
    }
    for analog in analogs:
        cands = sorted(analog.get("candidates") or [], key=lambda c: -float(c["share"]))
        if len(cands) < 3:
            continue
        t2 = float(cands[0]["share"]) + float(cands[1]["share"])
        t3 = float(cands[2]["share"])
        res = 1.0 - t2
        top2.append(t2)
        third.append(t3)
        residual.append(res)
        sep_w.append(float(cands[0]["share"]) - t3)
        sep_ru.append(float(cands[1]["share"]) - t3)
        class_counts["ballot_multiway"] += 1
        label = classify_share_profile(top_two_combined_share=t2, third_place_share=t3)
        if label == "principal_binary_with_minor_residual":
            class_counts["principal_binary_with_minor_residual"] += 1
        else:
            class_counts["materially_multiway"] += 1

    def _pct(vals: list[float], p: float) -> float | None:
        if not vals:
            return None
        ordered = sorted(vals)
        idx = round((len(ordered) - 1) * p / 100.0)
        return float(ordered[idx])

    return {
        "criterion_id": CRITERION_ID,
        "thresholds": {
            "min_top_two_combined_share": PRINCIPAL_BINARY_MIN_TOP_TWO_SHARE,
            "max_third_place_share": PRINCIPAL_BINARY_MAX_THIRD_SHARE,
            "principal_poll_share_floor": PRINCIPAL_POLL_SHARE_FLOOR,
        },
        "n_analogs": len(top2),
        "class_counts": class_counts,
        "top_two_combined_share": {
            "min": _pct(top2, 0),
            "p10": _pct(top2, 10),
            "p50": _pct(top2, 50),
            "p90": _pct(top2, 90),
            "max": _pct(top2, 100),
        },
        "third_place_share": {
            "min": _pct(third, 0),
            "p10": _pct(third, 10),
            "p50": _pct(third, 50),
            "p90": _pct(third, 90),
            "max": _pct(third, 100),
        },
        "residual_share": {
            "min": _pct(residual, 0),
            "p10": _pct(residual, 10),
            "p50": _pct(residual, 50),
            "p90": _pct(residual, 90),
            "max": _pct(residual, 100),
        },
        "winner_minus_third": {
            "min": _pct(sep_w, 0),
            "p50": _pct(sep_w, 50),
            "max": _pct(sep_w, 100),
        },
        "runner_up_minus_third": {
            "min": _pct(sep_ru, 0),
            "p50": _pct(sep_ru, 50),
            "max": _pct(sep_ru, 100),
        },
        "coverage_note": (
            "Candidate-level certified results available for 2014–2024 only; "
            "1990–2012 coverage gap remains explicit."
        ),
    }


__all__ = [
    "CRITERION_ID",
    "PRINCIPAL_BINARY_MAX_THIRD_SHARE",
    "PRINCIPAL_BINARY_MIN_TOP_TWO_SHARE",
    "PRINCIPAL_POLL_SHARE_FLOOR",
    "classify_share_profile",
    "summarize_analog_share_distribution",
]
