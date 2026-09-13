"""Senate institutional rules: LA jungle, GA runoffs, vacancies (blueprint §1.1 / §9.1)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from midterms.evidence.schema import empty_race_row


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
