"""Historical poll coverage + nominee identity gates (audit P0.3)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, LEAD_DAYS, MANIFESTS_DIR, NORMALIZED_DIR
from midterms.evidence.official_ballot import CYCLE_META, contested_contests, election_day


def _load_polls(election_id: str) -> pd.DataFrame:
    for path in (
        NORMALIZED_DIR / "polls_fte_historical.parquet",
        NORMALIZED_DIR / "polls_historical.parquet",
        NORMALIZED_DIR / "polls.parquet",
    ):
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        g = df[df["election_id"].astype(str) == election_id].copy()
        if len(g):
            return g
    return pd.DataFrame()


def _as_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def infer_nominees(polls: pd.DataFrame, *, election_day_d: date, window_days: int = 45) -> dict[str, dict[str, Any]]:
    """
    Infer general-election D/R nominees per race_id from late, non-hypothetical polls.
    """
    if polls.empty:
        return {}
    start = election_day_d - timedelta(days=window_days)
    g = polls.copy()
    g["_end"] = g["field_end"].map(_as_date) if "field_end" in g.columns else None
    g = g[g["_end"].notna()]
    g = g[(g["_end"] >= start) & (g["_end"] <= election_day_d)]
    if "hypothetical" in g.columns:
        hyp = g["hypothetical"].fillna(False)
        g = g[~hyp.astype(bool)]
    if "exclusion_status" in g.columns:
        g = g[g["exclusion_status"].astype(str).str.lower().ne("exclude")]
    out: dict[str, dict[str, Any]] = {}
    for rid, block in g.groupby(g["race_id"].astype(str)):
        dem_name = (
            block["dem_candidate_name"].dropna().astype(str).value_counts().index[0]
            if "dem_candidate_name" in block.columns and block["dem_candidate_name"].notna().any()
            else None
        )
        rep_name = (
            block["rep_candidate_name"].dropna().astype(str).value_counts().index[0]
            if "rep_candidate_name" in block.columns and block["rep_candidate_name"].notna().any()
            else None
        )
        dem_id = (
            block["dem_candidate_id"].dropna().astype(str).value_counts().index[0]
            if "dem_candidate_id" in block.columns and block["dem_candidate_id"].notna().any()
            else None
        )
        rep_id = (
            block["rep_candidate_id"].dropna().astype(str).value_counts().index[0]
            if "rep_candidate_id" in block.columns and block["rep_candidate_id"].notna().any()
            else None
        )
        out[str(rid)] = {
            "dem_candidate_id": dem_id,
            "dem_candidate_name": dem_name,
            "rep_candidate_id": rep_id,
            "rep_candidate_name": rep_name,
            "n_late_polls": int(len(block)),
        }
    return out


def coverage_for_lead(
    polls: pd.DataFrame,
    *,
    race_ids: set[str],
    as_of: date,
    election_day_d: date,
    nominees: dict[str, dict[str, Any]],
    polled_universe: set[str],
    min_polls_per_contest: int = 1,
    lead_days: int | None = None,
) -> dict[str, Any]:
    """Coverage diagnostics for one as-of / lead-time snapshot."""
    expected = set(polled_universe) & set(race_ids)
    if not expected:
        expected = set(race_ids)

    if polls.empty:
        return {
            "as_of": as_of.isoformat(),
            "lead_days": lead_days,
            "n_polls": 0,
            "n_contests_with_polls": 0,
            "n_contests_expected": len(expected),
            "missing_contests": sorted(expected),
            "nominee_mismatch_races": sorted(expected),
            "ok": False,
        }

    g = polls.copy()
    g["_end"] = g["field_end"].map(_as_date)
    g["_pub"] = g["published_at"].map(_as_date) if "published_at" in g.columns else g["_end"]
    g = g[g["_end"].notna()]
    g = g[(g["_end"] <= as_of) & (g["_pub"].isna() | (g["_pub"] <= as_of))]
    if "hypothetical" in g.columns:
        hyp = g["hypothetical"].fillna(False)
        g = g[~hyp.astype(bool)]
    if "exclusion_status" in g.columns:
        g = g[g["exclusion_status"].astype(str).str.lower().ne("exclude")]

    by_race = g.groupby(g["race_id"].astype(str)).size().to_dict() if len(g) else {}
    missing = sorted(rid for rid in expected if int(by_race.get(rid, 0)) < min_polls_per_contest)
    covered = len(expected) - len(missing)
    coverage_share = float(covered / len(expected)) if expected else 0.0

    # Nominee identity only enforced once nominees are typically settled
    mismatches: list[str] = []
    enforce_nominees = as_of >= (election_day_d - timedelta(days=60))
    late_cut = election_day_d - timedelta(days=45)
    if enforce_nominees:
        late = g[g["_end"] >= late_cut]
        for rid in sorted(expected):
            nom = nominees.get(rid) or {}
            block = late[late["race_id"].astype(str) == rid]
            if block.empty or not nom:
                continue
            if nom.get("dem_candidate_name") and "dem_candidate_name" in block.columns:
                share = (
                    block["dem_candidate_name"].astype(str) == str(nom["dem_candidate_name"])
                ).mean()
                if float(share) < 0.5:
                    mismatches.append(rid)
                    continue
            if nom.get("rep_candidate_name") and "rep_candidate_name" in block.columns:
                share = (
                    block["rep_candidate_name"].astype(str) == str(nom["rep_candidate_name"])
                ).mean()
                if float(share) < 0.5:
                    mismatches.append(rid)

    # Recency canary: close leads must include recently fielded polls (not only early ones)
    recency_ok = True
    median_end = None
    max_end = None
    n_recent = 0
    if len(g) and lead_days is not None:
        ends = sorted(d for d in g["_end"] if d is not None)
        median_end = ends[len(ends) // 2]
        max_end = ends[-1]
        recent_cut = as_of - timedelta(days=21)
        n_recent = int(sum(1 for d in ends if d >= recent_cut))
        if lead_days <= 7:
            recency_ok = max_end >= (election_day_d - timedelta(days=21)) and n_recent >= 5
        elif lead_days <= 30:
            recency_ok = max_end >= (election_day_d - timedelta(days=45)) and n_recent >= 3

    # Thresholds: early leads thinner; never require unpolled safe seats
    if lead_days is not None and lead_days >= 90:
        min_share = 0.40
    elif lead_days is not None and lead_days >= 60:
        min_share = 0.55
    else:
        min_share = 0.70

    ok = (
        coverage_share >= min_share
        and len(mismatches) == 0
        and recency_ok
        and int(len(g)) > 0
    )
    return {
        "as_of": as_of.isoformat(),
        "lead_days": lead_days,
        "n_polls": int(len(g)),
        "n_contests_with_polls": int(covered),
        "n_contests_expected": len(expected),
        "coverage_share": coverage_share,
        "min_share_required": min_share,
        "missing_contests": missing[:20],
        "n_missing_contests": len(missing),
        "nominee_mismatch_races": mismatches[:20],
        "n_nominee_mismatches": len(mismatches),
        "median_field_end": median_end.isoformat() if median_end else None,
        "max_field_end": max_end.isoformat() if max_end else None,
        "n_recent_21d": n_recent,
        "recency_ok": recency_ok,
        "ok": ok,
    }


def poll_coverage_report(
    year: int,
    *,
    lead_days: tuple[int, ...] | None = None,
    polls: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Per-cycle coverage by official contest + nominee at each lead time."""
    election_id = f"senate-{year}"
    lead_days = lead_days or tuple(d for d in LEAD_DAYS if d in (90, 60, 30, 7))
    contests = contested_contests(year)
    race_ids = {c["race_id"] for c in contests}
    ed = election_day(year)
    polls = polls if polls is not None else _load_polls(election_id)
    nominees = infer_nominees(polls, election_day_d=ed)

    # Universe: contests that received at least one non-hypothetical poll in-cycle
    polled_universe: set[str] = set()
    if len(polls):
        g = polls.copy()
        g["_end"] = g["field_end"].map(_as_date)
        if "hypothetical" in g.columns:
            hyp = g["hypothetical"].fillna(False)
            g = g[~hyp.astype(bool)]
        window_start = ed - timedelta(days=150)
        g = g[g["_end"].notna() & (g["_end"] >= window_start) & (g["_end"] <= ed)]
        polled_universe = set(g["race_id"].astype(str).unique()) & race_ids

    by_lead = {}
    for lead in lead_days:
        as_of = ed - timedelta(days=int(lead))
        by_lead[str(lead)] = coverage_for_lead(
            polls,
            race_ids=race_ids,
            as_of=as_of,
            election_day_d=ed,
            nominees=nominees,
            polled_universe=polled_universe,
            lead_days=int(lead),
        )

    n_with_identity = 0
    if len(polls) and "dem_candidate_name" in polls.columns:
        n_with_identity = int(polls["dem_candidate_name"].notna().sum())

    ok = (
        all(v.get("ok") for v in by_lead.values())
        and len(nominees) >= max(5, len(polled_universe) // 2 if polled_universe else 5)
        and n_with_identity >= 50
    )
    report = {
        "ok": ok,
        "year": year,
        "election_id": election_id,
        "n_official_contests": len(race_ids),
        "n_polled_universe": len(polled_universe),
        "n_poll_rows": int(len(polls)),
        "n_polls_with_candidate_identity": n_with_identity,
        "n_inferred_nominee_races": len(nominees),
        "nominees_sample": {k: nominees[k] for k in list(nominees)[:8]},
        "by_lead": by_lead,
        "failures": [f"lead_{k}" for k, v in by_lead.items() if not v.get("ok")],
        "note": (
            "Coverage scored on contests with in-cycle non-hypothetical polls "
            "(safe unpolled seats excluded). Nominee checks apply near Election Day; "
            "close leads require recent median field dates."
        ),
        "meta_source": (CYCLE_META.get(year) or {}).get("source"),
    }
    return report


def write_poll_coverage_report(
    years: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    years = years or (2018, 2020, 2022, 2024)
    by = {str(y): poll_coverage_report(y) for y in years}
    summary = {
        "ok": all(v.get("ok") for v in by.values()),
        "years": list(by.keys()),
        "by_cycle": by,
        "failures": [y for y, v in by.items() if not v.get("ok")],
        "generated_note": "audit P0.3 historical poll coverage gate",
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "poll_coverage_latest.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    (MANIFESTS_DIR / "poll_coverage.json").write_text(json.dumps(summary, indent=2, default=str))
    summary["path"] = str(path)
    return summary


def assert_poll_coverage(
    election_id: str,
    *,
    allow_thin: bool = False,
) -> dict[str, Any]:
    """Fail closed when official-contest / nominee coverage is insufficient."""
    year = int(str(election_id).split("-")[-1])
    report = poll_coverage_report(year)
    if not report.get("ok") and not allow_thin:
        raise ValueError(
            f"Historical poll coverage failed for {election_id}: "
            f"failures={report.get('failures')} missing_sample="
            f"{(report.get('by_lead') or {}).get('30', {}).get('missing_contests', [])[:5]}"
        )
    return report
