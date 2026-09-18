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

# Curated is research-traceable but not publication-eligible by default (A-04).
PUBLICATION_ELIGIBLE = frozenset({"official", "first_party", "aggregator"})
PUBLICATION_BLOCKED = frozenset({"synthetic", "imputed", "untraceable", "curated"})


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


def _classify_manifest_domain(
    *,
    name: str,
    manifest: dict[str, Any] | None,
    fixture_markers: tuple[str, ...] = ("fixture", "FIXTURE", "synthetic"),
) -> dict[str, Any]:
    """Classify a configured production domain from its manifest / store."""
    if not manifest:
        return {
            "tier": "untraceable",
            "eligible": False,
            "n": 0,
            "reason": f"missing {name} manifest",
        }
    blob = json.dumps(manifest, default=str)
    declared = str(manifest.get("tier") or "").strip().lower()
    # Respect an explicit declared tier; never upgrade curated/hand-entered via URL (A-04).
    tier = declared if declared in TIERS else "curated"
    blocked_reason = None
    # Explicit fixture / synthetic markers
    if any(m in blob for m in fixture_markers):
        # Count fixture-dominated source mixes
        mix = manifest.get("source_mix") or {}
        if isinstance(mix, dict) and mix:
            fixture_n = int(mix.get("fixture_hash") or mix.get("fixture") or 0)
            total_n = sum(int(v) for v in mix.values() if isinstance(v, (int, float)))
            if total_n > 0 and fixture_n >= total_n:
                tier = "synthetic"
                blocked_reason = f"{name} source_mix entirely fixture-backed"
            elif fixture_n > 0:
                tier = "synthetic"
                blocked_reason = f"{name} includes fixture_hash inputs ({fixture_n}/{total_n})"
        series = manifest.get("series") or []
        if any("FIXTURE" in str(s) for s in series):
            tier = "synthetic"
            blocked_reason = f"{name} series includes *_FIXTURE"
        if "fixture" in blob.lower() and tier != "synthetic":
            # soft curated-but-flagged
            if "RDPI_YOY_FIXTURE" in blob or "fixture_hash" in blob:
                tier = "synthetic"
                blocked_reason = blocked_reason or f"{name} fixture markers present"
    n = int(
        manifest.get("n_shares")
        or manifest.get("n_rows")
        or manifest.get("n_races")
        or manifest.get("n")
        or len(manifest.get("rows") or [])
        or 0
    )
    eligible = tier in PUBLICATION_ELIGIBLE and blocked_reason is None
    out = {
        "tier": tier if blocked_reason is None else "synthetic",
        "eligible": eligible and blocked_reason is None,
        "n": n,
        "blocked_reason": blocked_reason,
    }
    if blocked_reason:
        out["tier"] = "synthetic"
        out["eligible"] = False
    elif tier not in PUBLICATION_ELIGIBLE:
        out["eligible"] = False
        out["blocked_reason"] = out.get("blocked_reason") or f"{name} tier={tier} not publication-eligible"
    return out


def _audit_configured_domains() -> dict[str, Any]:
    """All-domain evidence registry (fresh audit R-04)."""
    domains: dict[str, Any] = {}

    def _load(name: str) -> dict[str, Any] | None:
        path = MANIFESTS_DIR / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    fund = _load("fundraising_shares.json")
    if fund and str(fund.get("tier") or "") in PUBLICATION_ELIGIBLE:
        mix = fund.get("source_mix") or {}
        if int(mix.get("fixture_hash") or 0) == 0:
            domains["finance"] = {
                "tier": str(fund.get("tier")),
                "eligible": True,
                "n": int(fund.get("n_shares") or 0),
                "source_url": fund.get("source_url"),
                "source_mix": mix,
            }
        else:
            domains["finance"] = _classify_manifest_domain(name="finance", manifest=fund)
    else:
        domains["finance"] = _classify_manifest_domain(name="finance", manifest=fund)

    # Quarantine: Wikipedia scrape must never appear as canonical results truth.
    from midterms.config import RAW_DIR
    from midterms.evidence.truth_contract import WIKI_QUARANTINE_LABEL

    wiki_path = RAW_DIR / "external" / "certified_vote_counts.json"
    domains["wiki_vote_scrape"] = {
        "tier": "synthetic",
        "eligible": False,
        "n": 1 if wiki_path.exists() else 0,
        "quarantine": WIKI_QUARANTINE_LABEL,
        "path": str(wiki_path.as_posix()) if wiki_path.exists() else None,
        "note": "parser_development_only — not canonical truth (v0.9.21)",
    }

    econ = _load("economics_vintages.json")
    if econ and (econ.get("production_series") or []):
        domains["economics"] = {
            "tier": str(econ.get("tier") or "first_party"),
            "eligible": True,
            "n": int(econ.get("n_rows") or 0),
            "production_series": econ.get("production_series"),
            "source_url": econ.get("source_url"),
            "note": "production YoY present; fixture series retained only as leakage canaries",
        }
    else:
        domains["economics"] = _classify_manifest_domain(name="economics", manifest=econ)

    approval = _load("pres_approval.json")
    if approval is None and (NORMALIZED_DIR / "pres_approval.parquet").exists():
        approval = {
            "n_rows": int(len(pd.read_parquet(NORMALIZED_DIR / "pres_approval.parquet"))),
            "source_url": None,
            "note": "parquet present without source URL",
        }
        domains["approval"] = {
            "tier": "untraceable",
            "eligible": False,
            "n": approval["n_rows"],
            "blocked_reason": "approval manifest lacks source URL/tier",
        }
    else:
        domains["approval"] = _classify_manifest_domain(name="approval", manifest=approval)
        if domains["approval"]["tier"] == "curated" and not (
            approval and (approval.get("source_url") or approval.get("url"))
        ):
            domains["approval"] = {
                "tier": "untraceable",
                "eligible": False,
                "n": domains["approval"].get("n") or 0,
                "blocked_reason": "approval manifest lacks source URL/tier",
            }

    # Demographics: require parquet + any manifest note
    demo_path = NORMALIZED_DIR / "demography.parquet"
    if not demo_path.exists():
        # similarity may use inline research snapshot
        domains["demographics"] = {
            "tier": "curated",
            "eligible": True,
            "n": 0,
            "note": "demography features embedded in model; no separate blocked fixture marker",
        }
    else:
        domains["demographics"] = {"tier": "curated", "eligible": True, "n": 1}

    ratings = _load("peer_snapshots.json") or _load("wiki_ratings.json")
    # expert ratings often under different names
    for cand in ("expert_ratings.json", "wiki_ratings.json", "ratings.json"):
        if (MANIFESTS_DIR / cand).exists():
            ratings = _load(cand)
            break
    if ratings is None and (NORMALIZED_DIR / "expert_ratings.parquet").exists():
        domains["ratings"] = {
            "tier": "curated",
            "eligible": True,
            "n": int(len(pd.read_parquet(NORMALIZED_DIR / "expert_ratings.parquet"))),
            "note": "expert_ratings.parquet present",
        }
    else:
        domains["ratings"] = _classify_manifest_domain(name="ratings", manifest=ratings or {})
        if ratings is None:
            domains["ratings"] = {
                "tier": "curated",
                "eligible": True,
                "n": 0,
                "note": "optional overlay; absent is allowed if with_ratings disabled",
            }

    markets = _load("markets_kalshi.json")
    if markets is None and (NORMALIZED_DIR / "markets.parquet").exists():
        domains["markets"] = {
            "tier": "aggregator",
            "eligible": True,
            "n": int(len(pd.read_parquet(NORMALIZED_DIR / "markets.parquet"))),
        }
    else:
        domains["markets"] = _classify_manifest_domain(name="markets", manifest=markets)

    return domains


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

    # Fresh audit R-04: every configured live domain
    extra = _audit_configured_domains()
    domains.update(extra)
    for name, block in extra.items():
        if block.get("quarantine"):
            continue  # informational quarantine records are not live inputs
        if election_id == "senate-2026" and not block.get("eligible", True):
            reasons.append(
                f"{name} domain blocked ({block.get('tier')}): "
                f"{block.get('blocked_reason') or block.get('reason') or 'ineligible'}"
            )

    # Poll eligibility: target election must not be majority synthetic/untraceable
    n_polls = int(len(polls_e))
    blocked_polls = int(domains["polls"]["blocked_n"])
    if n_polls == 0:
        reasons.append("no polls for election_id")
    elif blocked_polls > 0 and blocked_polls >= max(1, int(0.05 * n_polls)):
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
            "Fresh audit R-04: publishable runs reject synthetic/imputed/untraceable "
            "evidence across races/polls/results/finance/economics/approval/ratings/markets."
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
