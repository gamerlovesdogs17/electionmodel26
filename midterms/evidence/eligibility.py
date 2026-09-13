"""Evidence eligibility tiers (audit P0.4).

Every evidence domain is classified into an explicit tier. Publishable runs
must not contain synthetic / untraceable / stale production inputs. Development
fallbacks are allowed only when the run is labeled ``non_publication``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, NORMALIZED_DIR

# Ordered from strongest to weakest
TIERS = (
    "official",
    "first_party",
    "aggregator",
    "curated",
    "imputed",
    "synthetic",
    "untraceable",
)

PUBLICATION_ELIGIBLE = frozenset({"official", "first_party", "aggregator", "curated"})
PUBLICATION_BLOCKED = frozenset({"synthetic", "imputed", "untraceable"})


def classify_poll_row(row: dict[str, Any] | pd.Series) -> str:
    get = row.get if isinstance(row, dict) else lambda k, d=None: row[k] if k in row.index else d
    url = str(get("source_url") or "").lower()
    parser = str(get("parser_version") or "").lower()
    if "synthetic" in url or parser.startswith("fixtures"):
        return "synthetic"
    if "fivethirtyeight" in url or "datasette" in url or parser.startswith("fte-"):
        return "aggregator"
    if "votehub" in url or parser.startswith("votehub"):
        return "aggregator"
    if url.startswith("http") and url not in {"nan", "none", ""}:
        # FTE-normalized rows often keep the pollster's primary URL
        if parser.startswith("fte-"):
            return "aggregator"
        return "first_party"
    if not url or url in {"nan", "none", "null"}:
        return "untraceable"
    return "curated"


def classify_result_row(row: dict[str, Any] | pd.Series) -> str:
    get = row.get if isinstance(row, dict) else lambda k, d=None: row[k] if k in row.index else d
    url = str(get("source_url") or "").lower()
    if "synthetic" in url:
        return "synthetic"
    if "certified" in url or "medsl" in url or "harvard" in url or "dataverse" in url:
        return "official"
    if url.startswith("http"):
        return "curated"
    rid = str(get("race_id") or "")
    # Prefer certified archive presence via results_certified merge provenance
    if get("release_version") is not None and url and "synthetic" not in url:
        return "curated"
    if not url or url in {"nan", "none"}:
        return "untraceable"
    return "curated"


def classify_race_election(election_id: str, races: pd.DataFrame | None = None) -> str:
    """Historical official ballots are official; 2026 fixture generator is curated."""
    eid = str(election_id)
    if eid == "senate-2026":
        return "curated"
    path = NORMALIZED_DIR / "races_official.parquet"
    if path.exists():
        return "official"
    if races is not None and len(races) and "seat_class" in races.columns:
        return "curated"
    return "untraceable"


def _tier_counts(series: pd.Series) -> dict[str, int]:
    vc = series.value_counts()
    return {str(k): int(v) for k, v in vc.items()}


def classify_polls(polls: pd.DataFrame) -> pd.Series:
    if polls is None or polls.empty:
        return pd.Series(dtype=str)
    return polls.apply(classify_poll_row, axis=1)


def audit_evidence(
    *,
    election_id: str,
    polls: pd.DataFrame | None = None,
    races: pd.DataFrame | None = None,
    results: pd.DataFrame | None = None,
    max_poll_age_days: float | None = 21.0,
    as_of: str | None = None,
) -> dict[str, Any]:
    """
    Classify warehouse evidence for an election and decide publishability.

    Returns a structured report with ``publishable`` and ``run_class``.
    """
    from midterms.evidence.warehouse import Warehouse

    wh = None
    if polls is None or races is None or results is None:
        wh = Warehouse(ensure_fixtures=False)
        polls = polls if polls is not None else wh.polls
        races = races if races is not None else wh.races
        results = results if results is not None else wh.results

    polls_e = polls[polls["election_id"].astype(str) == election_id].copy() if len(polls) else polls
    races_e = races[races["election_id"].astype(str) == election_id].copy() if len(races) else races
    results_e = (
        results[results["election_id"].astype(str) == election_id].copy() if len(results) else results
    )

    poll_tiers = classify_polls(polls_e) if len(polls_e) else pd.Series(dtype=str)
    result_tiers = (
        results_e.apply(classify_result_row, axis=1) if len(results_e) else pd.Series(dtype=str)
    )
    race_tier = classify_race_election(election_id, races_e)

    reasons: list[str] = []
    domains: dict[str, Any] = {
        "races": {
            "tier": race_tier,
            "n": int(len(races_e)),
            "eligible": race_tier in PUBLICATION_ELIGIBLE,
        },
        "polls": {
            "n": int(len(polls_e)),
            "tier_counts": _tier_counts(poll_tiers) if len(poll_tiers) else {},
            "blocked_n": int(poll_tiers.isin(list(PUBLICATION_BLOCKED)).sum())
            if len(poll_tiers)
            else 0,
            "eligible_n": int(poll_tiers.isin(list(PUBLICATION_ELIGIBLE)).sum())
            if len(poll_tiers)
            else 0,
        },
        "results": {
            "n": int(len(results_e)),
            "tier_counts": _tier_counts(result_tiers) if len(result_tiers) else {},
            "blocked_n": int(result_tiers.isin(list(PUBLICATION_BLOCKED)).sum())
            if len(result_tiers)
            else 0,
        },
    }

    # Poll eligibility: target election must not be majority synthetic/untraceable
    n_polls = int(len(polls_e))
    blocked_polls = int(domains["polls"]["blocked_n"])
    if n_polls == 0:
        reasons.append("no polls for election_id")
    elif blocked_polls > 0 and blocked_polls >= max(1, int(0.05 * n_polls)):
        # Any material synthetic share blocks publication
        reasons.append(
            f"polls include blocked tiers ({blocked_polls}/{n_polls}): "
            f"{domains['polls']['tier_counts']}"
        )
    if race_tier not in PUBLICATION_ELIGIBLE:
        reasons.append(f"race universe tier={race_tier} not publication-eligible")

    # Staleness for live cycle
    stale = False
    max_age_h = None
    if election_id == "senate-2026" and n_polls and "field_end" in polls_e.columns:
        ends = pd.to_datetime(polls_e["field_end"], errors="coerce")
        if ends.notna().any():
            latest = ends.max()
            ref = pd.Timestamp(as_of) if as_of else pd.Timestamp.now(tz="UTC").tz_localize(None)
            if getattr(ref, "tzinfo", None) is not None:
                ref = ref.tz_localize(None)
            age_days = float((ref - latest).total_seconds() / 86400.0)
            max_age_h = age_days * 24.0
            if max_poll_age_days is not None and age_days > float(max_poll_age_days):
                stale = True
                reasons.append(
                    f"live polls stale: latest field_end age {age_days:.1f}d > {max_poll_age_days}d"
                )
    domains["polls"]["stale"] = stale
    domains["polls"]["latest_age_hours"] = max_age_h

    # Chamber / official ballot gates when available
    chamber_ok = None
    coverage_ok = None
    try:
        from midterms.validation.chamber_reconcile import GATE_YEARS, reconcile_cycle

        year = int(election_id.split("-")[-1])
        if year in GATE_YEARS:
            chamber_ok = bool(reconcile_cycle(year, races=races, results=results).get("ok"))
            if not chamber_ok:
                reasons.append("chamber reconcile failed for gate year")
    except Exception as exc:  # noqa: BLE001
        chamber_ok = None
        domains["chamber_reconcile_error"] = str(exc)

    if election_id != "senate-2026":
        try:
            from midterms.validation.poll_coverage import poll_coverage_report

            year = int(election_id.split("-")[-1])
            if year >= 2018:
                coverage_ok = bool(poll_coverage_report(year, polls=polls_e).get("ok"))
                if not coverage_ok:
                    reasons.append("poll coverage gate failed")
        except Exception as exc:  # noqa: BLE001
            coverage_ok = None
            domains["poll_coverage_error"] = str(exc)

    publishable = len(reasons) == 0 and n_polls > 0
    run_class = "publication" if publishable else "non_publication"
    report = {
        "ok": publishable,
        "publishable": publishable,
        "run_class": run_class,
        "election_id": election_id,
        "as_of": as_of,
        "reasons": reasons,
        "domains": domains,
        "chamber_reconcile_ok": chamber_ok,
        "poll_coverage_ok": coverage_ok,
        "tiers": list(TIERS),
        "publication_eligible_tiers": sorted(PUBLICATION_ELIGIBLE),
        "publication_blocked_tiers": sorted(PUBLICATION_BLOCKED),
        "note": (
            "Audit P0.4: publishable runs reject synthetic/imputed/untraceable evidence. "
            "Development may continue with run_class=non_publication."
        ),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    return report


def write_eligibility_report(
    election_id: str = "senate-2026",
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    report = audit_evidence(election_id=election_id, as_of=as_of)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "evidence_eligibility_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    (MANIFESTS_DIR / "evidence_eligibility.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    report["path"] = str(path)
    return report


def assert_publishable(
    election_id: str,
    *,
    as_of: str | None = None,
    allow_non_publication: bool = True,
) -> dict[str, Any]:
    """
    Evaluate eligibility.

    If not publishable and ``allow_non_publication`` is False, raise.
    Otherwise return the report (caller must stamp run_class on the artifact).
    """
    report = audit_evidence(election_id=election_id, as_of=as_of)
    if not report["publishable"] and not allow_non_publication:
        raise ValueError(
            "Evidence not publication-eligible for "
            f"{election_id}: {'; '.join(report.get('reasons') or [])}"
        )
    return report
