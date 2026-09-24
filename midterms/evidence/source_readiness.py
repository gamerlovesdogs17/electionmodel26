"""Cheap, fail-closed source readiness audit for the v0.9.22 rebuild."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, MODEL_VERSION, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.candidate_timeline import (
    apply_candidate_timeline,
    audit_candidate_timeline,
)
from midterms.evidence.demographic_vintages import select_demographic_vintage
from midterms.evidence.economics import audit_realtime_economic_coverage
from midterms.evidence.official_ballot import election_day
from midterms.evidence.source_registry import source_preparation_registry
from midterms.evidence.warehouse import dataframe_semantic_sha256

SOURCE_READINESS_SCHEMA_VERSION = "source-readiness-v1"
HISTORICAL_YEARS = (2018, 2020, 2022, 2024)
FORMAL_LEADS = (60, 30)


def required_historical_cutoffs() -> dict[str, date]:
    return {
        f"senate-{year}-lead-{lead}": election_day(year) - timedelta(days=lead)
        for year in HISTORICAL_YEARS for lead in FORMAL_LEADS
    }


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _manifest(path: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if not path.is_file():
        return None, None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, _sha(path), "invalid_hash"
    return payload, _sha(path), None


def _domain_base(registry_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_for_core": bool(registry_row["hard_for_current_run"]),
        "required_for_historical_validation": bool(registry_row["hard_for_historical_run"]),
        "adapter": registry_row["adapter"],
        "status": "adapter_not_configured",
        "reasons": [],
    }


def _cutoff_counts(frame: pd.DataFrame, *, date_column: str) -> dict[str, int]:
    values = pd.to_datetime(frame[date_column], errors="coerce").dt.date
    out: dict[str, int] = {}
    for label, cutoff in required_historical_cutoffs().items():
        election_id = "-".join(label.split("-")[:2])
        mask = frame.get("election_id", pd.Series("", index=frame.index)).astype(str).eq(election_id)
        out[label] = int((mask & values.notna() & (values <= cutoff)).sum())
    return out


def evaluate_readiness_gate(
    domains: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Apply hard/optional domain roles to already-classified source states."""
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    optional_disabled: list[str] = []
    for name, block in domains.items():
        if block["status"] == "optional_disabled":
            optional_disabled.append(name)
            continue
        hard = block["required_for_core"] or block["required_for_historical_validation"]
        target = blockers if hard and block["status"] != "ready" else warnings
        if block["status"] != "ready":
            target.append({"domain": name, "status": block["status"], "reasons": block.get("reasons") or []})
    return blockers, warnings, sorted(optional_disabled)


def audit_source_readiness(
    *,
    election_id: str = "senate-2026",
    as_of: str | date,
    normalized_dir: Path = NORMALIZED_DIR,
    manifests_dir: Path = MANIFESTS_DIR,
    raw_dir: Path = RAW_DIR,
    enabled_optional_features: set[str] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Inspect source coverage without fetching data or fitting a model."""
    cutoff = pd.Timestamp(as_of).date()
    registry = source_preparation_registry(enabled_optional_features=enabled_optional_features)
    domains = {
        name: _domain_base(row) for name, row in registry["domains"].items()
    }
    historical_cutoffs = {
        label: value.isoformat() for label, value in required_historical_cutoffs().items()
    }

    polls_path = normalized_dir / "polls.parquet"
    if polls_path.is_file():
        polls = pd.read_parquet(polls_path)
        counts = _cutoff_counts(polls, date_column="available_at")
        current = polls[polls["election_id"].astype(str).eq(election_id)].copy()
        current_dates = pd.to_datetime(current["available_at"], errors="coerce").dt.date
        current = current[current_dates.notna() & (current_dates <= cutoff)]
        missing_cutoffs = [label for label, count in counts.items() if count == 0]
        status = "ready" if len(current) and not missing_cutoffs else "incomplete_coverage"
        domains["polls"].update({
            "status": status,
            "n_current_rows": int(len(current)),
            "historical_cutoff_counts": counts,
            "missing_cutoffs": missing_cutoffs,
            "semantic_sha256": dataframe_semantic_sha256(polls),
            "reasons": [] if status == "ready" else ["poll rows are missing at required current/historical cutoffs"],
        })
    else:
        domains["polls"].update(status="missing", reasons=["data/normalized/polls.parquet is missing"])

    races_path = normalized_dir / "races_official.parquet"
    races = pd.read_parquet(races_path) if races_path.is_file() else pd.DataFrame()
    needed_elections = {election_id, *(f"senate-{year}" for year in HISTORICAL_YEARS)}
    present_elections = set(races.get("election_id", pd.Series(dtype=str)).astype(str))
    missing_elections = sorted(needed_elections - present_elections)
    domains["races"].update({
        "status": "ready" if len(races) and not missing_elections else ("missing" if races.empty else "incomplete_coverage"),
        "missing_election_ids": missing_elections,
        "semantic_sha256": dataframe_semantic_sha256(races) if len(races) else None,
        "reasons": [] if len(races) and not missing_elections else ["official race universe lacks required cycles"],
    })

    timeline_path = normalized_dir / "candidate_timeline.parquet"
    timeline = pd.read_parquet(timeline_path) if timeline_path.is_file() else pd.DataFrame()
    timeline_cutoffs = {**historical_cutoffs, f"{election_id}-current": cutoff.isoformat()}
    timeline_rows: dict[str, Any] = {}
    missing_timeline: list[dict[str, Any]] = []
    for label, cutoff_value in timeline_cutoffs.items():
        target_election = election_id if label.endswith("-current") else "-".join(label.split("-")[:2])
        subset = races[races.get("election_id", pd.Series("", index=races.index)).astype(str).eq(target_election)].copy()
        if subset.empty:
            audit = {
                "status": "missing", "publication_eligible": False,
                "n_required_races": 0, "n_point_in_time": 0,
                "reasons": ["required race universe is missing"],
            }
        else:
            applied, metadata = apply_candidate_timeline(subset, timeline, as_of=cutoff_value)
            audit = audit_candidate_timeline(applied, metadata)
        timeline_rows[label] = audit
        if not audit.get("publication_eligible"):
            required_mask = ~subset.get("not_up", pd.Series(False, index=subset.index)).fillna(False).astype(bool)
            missing_timeline.append({
                "cutoff": label,
                "as_of": cutoff_value,
                "race_ids": sorted(subset.loc[required_mask, "race_id"].astype(str).unique()) if len(subset) else [],
                "reasons": audit.get("reasons") or [],
            })
    timeline_manifest, timeline_manifest_sha, timeline_manifest_error = _manifest(
        manifests_dir / "candidate_timeline.json"
    )
    timeline_status = "ready" if timeline_rows and not missing_timeline else (
        "missing" if not timeline_path.is_file() else "incomplete_coverage"
    )
    if timeline_manifest_error and timeline_path.is_file():
        timeline_status = timeline_manifest_error
    domains["candidate_timeline"].update({
        "status": timeline_status,
        "cutoffs": timeline_rows,
        "missing_coverage": missing_timeline,
        "manifest_sha256": timeline_manifest_sha,
        "semantic_sha256": (
            (timeline_manifest or {}).get("normalized_semantic_sha256")
            if timeline_manifest else None
        ),
        "parser_version": (timeline_manifest or {}).get("parser_version") if timeline_manifest else None,
        "reasons": [] if timeline_status == "ready" else [
            "source-backed candidate identity is incomplete at required cutoffs"
        ],
    })

    demo_path = normalized_dir / "demographic_vintages.parquet"
    demo_manifest, demo_manifest_sha, demo_manifest_error = _manifest(
        manifests_dir / "demographic_vintages.json"
    )
    demo_cutoffs: dict[str, Any] = {}
    if demo_path.is_file() and not demo_manifest_error:
        demo = pd.read_parquet(demo_path)
        for label, cutoff_value in {**historical_cutoffs, "current": cutoff.isoformat()}.items():
            selected_rows, selected = select_demographic_vintage(demo, as_of=cutoff_value)
            election_for_cutoff = (
                election_id if label == "current" else "-".join(label.split("-")[:2])
            )
            required_races = races[
                races.get("election_id", pd.Series(index=races.index, dtype=str)).astype(str)
                == election_for_cutoff
            ]
            required_states = sorted(required_races["state"].dropna().astype(str).unique())
            covered_states = set(selected_rows["state"].astype(str))
            missing_states = sorted(set(required_states) - covered_states)
            selected.update({
                "required_states": required_states,
                "missing_states": missing_states,
                "production_eligible": bool(selected.get("production_eligible"))
                and not missing_states,
            })
            if missing_states:
                selected["status"] = "incomplete_coverage"
            demo_cutoffs[label] = selected
        demo_missing = [label for label, block in demo_cutoffs.items() if not block["production_eligible"]]
        demo_status = "ready" if not demo_missing else "incomplete_coverage"
    else:
        demo_missing = [*historical_cutoffs, "current"]
        demo_status = demo_manifest_error or (
            "incomplete_coverage" if (normalized_dir / "demography.parquet").is_file() else "missing"
        )
    domains["demographics"].update({
        "status": demo_status,
        "cutoffs": demo_cutoffs,
        "missing_cutoffs": demo_missing,
        "manifest_sha256": demo_manifest_sha,
        "semantic_sha256": (demo_manifest or {}).get("normalized_semantic_sha256") if demo_manifest else None,
        "parser_version": (demo_manifest or {}).get("parser_version") if demo_manifest else None,
        "reasons": [] if demo_status == "ready" else [
            "cycle-aware demographic vintages lack verified official release dates/coverage"
        ],
    })

    econ_path = normalized_dir / "economics_vintages.parquet"
    econ_manifest, econ_manifest_sha, econ_manifest_error = _manifest(
        manifests_dir / "economics_vintages.json"
    )
    env = environ if environ is not None else os.environ
    if econ_path.is_file() and not econ_manifest_error:
        economics = pd.read_parquet(econ_path)
        econ_audit = audit_realtime_economic_coverage(
            economics,
            cutoffs={**historical_cutoffs, "current": cutoff.isoformat()},
            api_key_available=bool(env.get("FRED_API_KEY") or env.get("ALFRED_API_KEY")),
        )
        econ_status = econ_audit["status"]
    else:
        econ_audit = {}
        econ_status = econ_manifest_error or "missing"
    domains["economics"].update({
        "status": econ_status,
        "coverage": econ_audit,
        "manifest_sha256": econ_manifest_sha,
        "semantic_sha256": (econ_manifest or {}).get("normalized_semantic_sha256") if econ_manifest else None,
        "parser_version": (econ_manifest or {}).get("parser_version") if econ_manifest else None,
        "reasons": [] if econ_status == "ready" else [
            "ALFRED real-time vintages are unavailable for one or more required cutoffs"
        ],
    })

    # Remaining declared domains: record verifiable stores and explicitly call
    # out historical gaps instead of upgrading existence to full readiness.
    simple = {
        "pollster_ratings": ("pollster_ratings.parquet", "pollster_ratings.json"),
        "presidential_prior": ("presidential_vote_counts.parquet", "presidential_vote_sources.json"),
        "finance": ("fundraising_shares.parquet", "fundraising_shares.json"),
        "approval": ("pres_approval.parquet", "pres_approval.json"),
        "official_results": ("results_certified.parquet", "official_senate_ballots.json"),
    }
    for name, (data_name, manifest_name) in simple.items():
        data_path = normalized_dir / data_name
        manifest, manifest_sha, manifest_error = _manifest(manifests_dir / manifest_name)
        status = manifest_error or ("ready" if data_path.is_file() else "missing")
        reasons: list[str] = []
        semantic = None
        frame = pd.DataFrame()
        if data_path.is_file():
            frame = pd.read_parquet(data_path)
            semantic = dataframe_semantic_sha256(frame)
        if name == "pollster_ratings" and len(frame):
            available = pd.to_datetime(frame.get("available_at"), errors="coerce").dt.date
            if not any(available.notna() & (available <= required_historical_cutoffs()["senate-2018-lead-60"])):
                status = "incomplete_coverage"
                reasons.append("no source-backed pollster ratings were available for historical cutoffs")
        elif name == "finance" and len(frame):
            elections = set(frame.get("election_id", pd.Series(dtype=str)).astype(str))
            required = {f"senate-{year}" for year in HISTORICAL_YEARS}
            if not required.issubset(elections):
                status = "incomplete_coverage"
                reasons.append("source-backed finance rows are absent for historical validation cycles")
        elif name == "approval" and len(frame):
            historical = frame[pd.to_numeric(frame.get("year"), errors="coerce").isin(HISTORICAL_YEARS)]
            if historical.empty or historical.get("source", pd.Series(dtype=str)).astype(str).str.contains("curated", case=False).any():
                status = "untraceable"
                reasons.append("historical approval rows are curated snapshots rather than immutable source-backed vintages")
        domains[name].update({
            "status": status,
            "manifest_sha256": manifest_sha,
            "semantic_sha256": semantic,
            "parser_version": (manifest or {}).get("parser_version") if manifest else None,
            "reasons": reasons or ([] if status == "ready" else [f"{name} store or manifest is unavailable"]),
        })

    generic_path = raw_dir / "external" / "votehub_generic_ballot_2026.json"
    domains["generic_ballot"].update({
        "status": "ready" if generic_path.is_file() and domains["polls"]["status"] == "ready" else "incomplete_coverage",
        "semantic_sha256": _sha(generic_path),
        "historical_method": "derived_from_frozen_senate_poll_deviation",
        "reasons": [] if generic_path.is_file() and domains["polls"]["status"] == "ready" else [
            "current generic-ballot source or historical poll-derived context is unavailable"
        ],
    })

    for optional in ("expert_ratings", "markets"):
        registry_row = registry["domains"][optional]
        if not registry_row["enabled"]:
            domains[optional].update({
                "status": "optional_disabled",
                "reasons": [f"{registry_row['optional_feature_dependency']} is disabled"],
            })
        else:
            output = registry_row.get("normalized_output")
            manifest_output = registry_row.get("manifest_output")
            data_path = (normalized_dir.parent.parent / output) if output else None
            manifest_path = (manifests_dir.parent.parent / manifest_output) if manifest_output else None
            domains[optional].update({
                "status": "ready" if data_path and data_path.is_file() and manifest_path and manifest_path.is_file() else "missing",
                "reasons": [] if data_path and data_path.is_file() and manifest_path and manifest_path.is_file() else [
                    f"enabled optional domain {optional} lacks a sealed store"
                ],
            })

    blockers, warnings, optional_disabled = evaluate_readiness_gate(domains)
    return {
        "schema_version": SOURCE_READINESS_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "election_id": election_id,
        "as_of": cutoff.isoformat(),
        "ready_for_expensive_rebuild": not blockers,
        "source_preparation_registry_version": registry["registry_version"],
        "effective_registry": registry,
        "domains": domains,
        "historical_cutoffs": historical_cutoffs,
        "blockers": blockers,
        "warnings": warnings,
        "optional_disabled_domains": sorted(optional_disabled),
    }


def write_source_readiness(**kwargs: Any) -> dict[str, Any]:
    report = audit_source_readiness(**kwargs)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "source_readiness_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report
