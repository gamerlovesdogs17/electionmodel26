"""Senate institutional rules: LA jungle, GA runoffs, vacancies (blueprint §1.1 / §9.1)."""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

import pandas as pd

from midterms.evidence.schema import empty_race_row


@dataclass(frozen=True)
class InstitutionalContestRule:
    seat_id: str
    threshold: float = 0.50
    runoff_required: bool = True
    tie_policy: str = "unresolved"
    phase: str = "general"
    vacancy_status: str | None = None


def apply_institutional_rules_to_draws(
    first_round_shares: np.ndarray,
    candidate_ids: list[str],
    rule: InstitutionalContestRule,
    *,
    runoff_shares: np.ndarray | None = None,
    joint_draw_ids: np.ndarray | None = None,
) -> dict[str, Any]:
    """Apply threshold/runoff rules within each existing joint draw.

    A runoff transition is unresolved unless callers supply validated runoff
    draws.  Row alignment retains the same shared-shock draw identity.
    """
    shares = np.asarray(first_round_shares, dtype=float)
    if shares.ndim != 2 or shares.shape[1] != len(candidate_ids) or len(candidate_ids) < 2:
        raise ValueError("first_round_shares must be (draw, candidate)")
    if not np.isfinite(shares).all() or (shares < 0).any():
        raise ValueError("candidate shares must be finite and nonnegative")
    totals = shares.sum(axis=1)
    if np.any(totals <= 0):
        raise ValueError("each draw must have positive candidate mass")
    shares = shares / totals[:, None]
    n_draws = shares.shape[0]
    draw_ids = np.arange(n_draws) if joint_draw_ids is None else np.asarray(joint_draw_ids)
    if len(draw_ids) != n_draws:
        raise ValueError("joint_draw_ids length mismatch")
    runoff = np.zeros(n_draws, dtype=bool)
    winner = np.full(n_draws, -1, dtype=int)
    advanced = np.full((n_draws, 2), -1, dtype=int)
    for draw in range(n_draws):
        order = np.argsort(-shares[draw], kind="stable")
        top = order[0]
        tied = np.isclose(shares[draw, order[0]], shares[draw, order[1]])
        if shares[draw, top] >= rule.threshold and not tied:
            winner[draw] = top
            continue
        if not rule.runoff_required:
            if tied and rule.tie_policy == "unresolved":
                continue
            winner[draw] = min(order[:2]) if tied else top
            continue
        runoff[draw] = True
        advanced[draw] = order[:2]
    if runoff.any() and runoff_shares is not None:
        runoff_values = np.asarray(runoff_shares, dtype=float)
        if runoff_values.shape != shares.shape:
            raise ValueError("runoff_shares must preserve first-round draw/candidate shape")
        for draw in np.where(runoff)[0]:
            a, b = advanced[draw]
            va, vb = runoff_values[draw, a], runoff_values[draw, b]
            if not np.isfinite([va, vb]).all() or va < 0 or vb < 0 or va + vb <= 0:
                raise ValueError("runoff transition contains invalid candidate mass")
            if np.isclose(va, vb) and rule.tie_policy == "unresolved":
                continue
            winner[draw] = min(a, b) if np.isclose(va, vb) else (a if va > vb else b)
    return {
        "schema_version": "joint-institutional-rules-v1",
        "rule": asdict(rule),
        "joint_draw_ids": draw_ids.tolist(),
        "candidate_ids": list(candidate_ids),
        "winner_candidate_ids": [candidate_ids[i] if i >= 0 else None for i in winner],
        "runoff_required": runoff.tolist(),
        "runoff_advanced_candidate_ids": [
            [candidate_ids[a], candidate_ids[b]] if a >= 0 else [] for a, b in advanced
        ],
        "unresolved_draws": int(np.sum(winner < 0)),
        "transition_model": "provided_draws" if runoff_shares is not None else "disabled_unvalidated",
        "seat_count_per_draw": [1 if i >= 0 else 0 for i in winner],
    }


def validate_joint_seat_accounting(
    contest_results: list[dict[str, Any]],
    *,
    held_seats: int,
    chamber_size: int = 100,
) -> dict[str, Any]:
    """Ensure phases do not double count one legal seat."""
    if not contest_results:
        totals = np.asarray([held_seats], dtype=int)
    else:
        n = len(contest_results[0]["seat_count_per_draw"])
        totals = np.full(n, int(held_seats), dtype=int)
        seat_ids: set[str] = set()
        for result in contest_results:
            seat_id = str((result.get("rule") or {}).get("seat_id") or "")
            if not seat_id or seat_id in seat_ids:
                raise ValueError("duplicate or missing seat_id in institutional results")
            seat_ids.add(seat_id)
            counts = np.asarray(result["seat_count_per_draw"], dtype=int)
            if len(counts) != n:
                raise ValueError("institutional draw counts are misaligned")
            totals += counts
    return {
        "ok": bool(np.all(totals == chamber_size)),
        "chamber_size": int(chamber_size),
        "totals": totals.tolist(),
        "no_double_count": True,
    }


def louisiana_needs_runoff(dem_share_all: float, rep_share_all: float, other_share_all: float = 0.0) -> bool:
    """Jungle primary: runoff if no candidate clears 50%."""
    return max(dem_share_all, rep_share_all, other_share_all) < 50.0 - 1e-9


def georgia_needs_runoff(dem_share_all: float, rep_share_all: float, other_share_all: float = 0.0) -> bool:
    """GA majority requirement for general → runoff if nobody ≥50%."""
    return max(dem_share_all, rep_share_all, other_share_all) < 50.0 - 1e-9


def maybe_materialize_runoff_rows(
    races: pd.DataFrame,
    multiway: list[dict[str, Any]] | None = None,
    *,
    as_of: date | None = None,
) -> pd.DataFrame:
    """
    If LA/GA multiway shares imply a runoff and election day has passed the
    general without a majority, add a `runoff` phase row (and mark general
    as runoff_pending only when shares are known and force runoff).

    For pre-election forecasts we keep the general active; runoff rows are
    created as inactive `runoff_pending` templates for LA/GA.
    """
    if races.empty:
        return races
    by_mw = {m["race_id"]: m for m in (multiway or [])}
    extra = []
    out = races.copy()
    for idx, row in out.iterrows():
        st = str(row["state"])
        if st not in {"LA", "GA"} or bool(row.get("not_up")):
            continue
        if str(row.get("election_phase") or "general") not in {"general", "special"}:
            continue
        rid = str(row["race_id"])
        mw = by_mw.get(rid)
        need = False
        if mw:
            fn = louisiana_needs_runoff if st == "LA" else georgia_needs_runoff
            need = fn(float(mw["dem_share_all"]), float(mw["rep_share_all"]), float(mw["other_share_all"]))
        # Always register a pending runoff template for LA/GA generals so rules are explicit
        ed = date.fromisoformat(str(row["election_day"])[:10])
        runoff_day = ed + timedelta(days=28)
        runoff_id = f"{rid}-runoff"
        if runoff_id in set(out["race_id"].astype(str)):
            continue
        phase = "runoff" if need and as_of and as_of > ed else "runoff_pending"
        extra.append(
            {
                **{c: row.get(c) for c in out.columns},
                "race_id": runoff_id,
                "election_phase": phase,
                "runoff_of": rid,
                "ballot_status": "nominated",
                "not_up": False,
                "election_day": runoff_day.isoformat(),
                "effective_election_day": runoff_day.isoformat(),
                "seat_class": str(row.get("seat_class") or "II") + "+runoff",
            }
        )
    if not extra:
        return out
    return pd.concat([out, pd.DataFrame(extra)], ignore_index=True)


def apply_vacancy_defaults(races: pd.DataFrame) -> pd.DataFrame:
    """Ensure specials carry vacancy_reason."""
    out = races.copy()
    if "vacancy_reason" not in out.columns:
        out["vacancy_reason"] = None
    if "seat_class" in out.columns:
        mask = out["seat_class"].astype(str).eq("special") & out["vacancy_reason"].isna()
        out.loc[mask, "vacancy_reason"] = "appointment"
    return out
