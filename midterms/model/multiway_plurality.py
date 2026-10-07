"""Generic multi-candidate plurality forecasting helpers (blueprint-faithful).

Production activation requires historical analog support. When unsupported, races
must fail closed on win probability rather than collapse to a binary adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

MULTIWAY_CONTEST_STRUCTURE = "multiway_plurality"
MULTIWAY_MODELING_PATH = "multiway_plurality_adapter"
MULTIWAY_SUPPORT_UNSUPPORTED = "unsupported"
ANALOG_SELECTION_RULE = {
    "id": "senate-plurality-multiway-analogs-v1",
    "predeclared_before_scoring": True,
    "include": [
        "US Senate general elections 1990–2024",
        "at least three ballot-qualified candidates",
        "plurality (not RCV, not majority-runoff) institutional rule",
    ],
    "exclude": [
        "Alaska RCV / IRV contests",
        "Louisiana jungle-primary / majority-threshold contests",
        "Georgia runoff-threshold contests",
        "same-party generals without multi-party field",
    ],
}


@dataclass(frozen=True)
class MultiwayCandidate:
    candidate_id: str
    candidate_name: str
    ballot_party: str
    caucus: str | None


def shares_sum_to_one(shares: np.ndarray, *, atol: float = 1e-9) -> bool:
    array = np.asarray(shares, dtype=float)
    if array.ndim != 2 or array.shape[1] < 2:
        return False
    if not np.isfinite(array).all() or (array < -atol).any():
        return False
    totals = array.sum(axis=1)
    return bool(np.all(np.abs(totals - 1.0) <= atol))


def plurality_winners(
    shares: np.ndarray,
    candidate_ids: list[str],
    *,
    residual_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return draw-wise plurality winners; residual/unknown caucus fails closed."""
    array = np.asarray(shares, dtype=float)
    if not shares_sum_to_one(array):
        raise ValueError("candidate shares must be finite, nonnegative, and sum to 1")
    if array.shape[1] != len(candidate_ids):
        raise ValueError("candidate_ids length must match share columns")
    residual_ids = residual_ids or set()
    order = np.argsort(-array, axis=1, kind="stable")
    top = order[:, 0]
    second = order[:, 1]
    tied = np.isclose(array[np.arange(len(array)), top], array[np.arange(len(array)), second])
    winners: list[str | None] = []
    fail_closed = np.zeros(len(array), dtype=bool)
    for draw, idx in enumerate(top):
        if tied[draw]:
            winners.append(None)
            fail_closed[draw] = True
            continue
        candidate_id = candidate_ids[int(idx)]
        if candidate_id in residual_ids:
            winners.append(None)
            fail_closed[draw] = True
            continue
        winners.append(candidate_id)
    return {
        "schema_version": "multiway-plurality-winners-v1",
        "candidate_ids": list(candidate_ids),
        "winner_candidate_ids": winners,
        "fail_closed_draws": fail_closed.tolist(),
        "n_fail_closed": int(fail_closed.sum()),
        "n_resolved": int((~fail_closed).sum()),
    }


def caucus_from_winners(
    winner_candidate_ids: list[str | None],
    caucus_by_candidate: dict[str, str | None],
) -> dict[str, Any]:
    """Map winners to caucus; missing/unknown caucus fails closed for that draw."""
    caucus_draws: list[str | None] = []
    fail_closed = []
    for winner in winner_candidate_ids:
        if winner is None:
            caucus_draws.append(None)
            fail_closed.append(True)
            continue
        caucus = caucus_by_candidate.get(winner)
        if caucus not in {"D", "R"}:
            caucus_draws.append(None)
            fail_closed.append(True)
        else:
            caucus_draws.append(caucus)
            fail_closed.append(False)
    return {
        "schema_version": "multiway-plurality-caucus-v1",
        "caucus_draws": caucus_draws,
        "fail_closed_draws": fail_closed,
        "n_fail_closed": int(sum(fail_closed)),
        "p_dem_caucus": (
            float(np.mean([c == "D" for c, failed in zip(caucus_draws, fail_closed) if not failed]))
            if any(not failed for failed in fail_closed)
            else None
        ),
    }


def historical_analog_support_report(*, n_analogs: int, min_analogs: int = 8) -> dict[str, Any]:
    """Classify whether a probability model is historically supportable."""
    supported = int(n_analogs) >= int(min_analogs)
    return {
        "schema_version": "multiway-plurality-analog-support-v1",
        "analog_selection_rule": ANALOG_SELECTION_RULE,
        "n_analogs": int(n_analogs),
        "min_analogs_for_probability_model": int(min_analogs),
        "probability_model_support_status": (
            "limited_supported" if supported else MULTIWAY_SUPPORT_UNSUPPORTED
        ),
        "win_probability_status": "ok" if supported else "fail_closed",
        "note": (
            "Analog set large enough for a cautious probability model"
            if supported
            else "Historical support too sparse; classify contest correctly and withhold win probability"
        ),
    }


def unsupported_multiway_race_payload(
    *,
    race_id: str,
    candidates: list[MultiwayCandidate],
    n_analogs: int = 0,
) -> dict[str, Any]:
    """Fail-closed forecast fields for a correctly classified multiway race."""
    support = historical_analog_support_report(n_analogs=n_analogs)
    return {
        "race_id": race_id,
        "contest_structure": MULTIWAY_CONTEST_STRUCTURE,
        "modeling_path": MULTIWAY_MODELING_PATH,
        "probability_model_support_status": support["probability_model_support_status"],
        "win_probability_status": support["win_probability_status"],
        "candidate_probabilities": [
            {
                "candidate_id": c.candidate_id,
                "candidate_name": c.candidate_name,
                "ballot_party": c.ballot_party,
                "caucus": c.caucus,
                "p_win": None,
            }
            for c in candidates
        ],
        "analog_support": support,
        "authoritative_binary_aliases": False,
    }
