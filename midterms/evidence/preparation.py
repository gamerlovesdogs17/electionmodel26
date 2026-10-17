"""Evidence preparation orchestration with audit, safe refresh, and seal modes."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, MODEL_VERSION
from midterms.evidence.evidence_bundle import (
    build_evidence_bundle,
    write_evidence_bundle,
)
from midterms.evidence.source_readiness import (
    required_historical_cutoffs,
    write_source_readiness,
)

PREPARE_EVIDENCE_VERSION = "prepare-evidence-v3-transactional-source-refresh"


def _poll_snapshot_paths() -> tuple[Path, ...]:
    from midterms.config import NORMALIZED_DIR, RAW_DIR
    from midterms.evidence.live_generic_ballot import LIVE_GENERIC_BALLOT_PATH

    return (
        NORMALIZED_DIR / "polls.parquet",
        NORMALIZED_DIR / "polls_live_votehub.parquet",
        RAW_DIR / "external" / "votehub_us_senator.json",
        LIVE_GENERIC_BALLOT_PATH,
        RAW_DIR / "polls_live_votehub.csv",
        MANIFESTS_DIR / "fetch_votehub_us_senator.json",
        MANIFESTS_DIR / "fetch_votehub_generic_ballot_2026.json",
        MANIFESTS_DIR / "normalize_votehub_senate.json",
        MANIFESTS_DIR / "merge_live_polls.json",
    )


def _snapshot_poll_artifacts() -> dict[str, bytes | None]:
    return {
        str(path): (path.read_bytes() if path.is_file() else None)
        for path in _poll_snapshot_paths()
    }


def _restore_poll_artifacts(snapshot: dict[str, bytes | None]) -> list[str]:
    restored: list[str] = []
    for raw_path, payload in snapshot.items():
        path = Path(raw_path)
        if payload is None:
            if path.is_file():
                path.unlink()
                restored.append(f"removed:{path.as_posix()}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        restored.append(path.as_posix())
    return restored


def _finance_snapshot_paths() -> tuple[Path, ...]:
    """Every versioned artifact mutated by the official FEC refresh."""
    from midterms.config import NORMALIZED_DIR, RAW_DIR

    external = RAW_DIR / "external"
    paths: list[Path] = [
        NORMALIZED_DIR / "fec_candidate_committees.parquet",
        NORMALIZED_DIR / "fec_form3_reports.parquet",
        NORMALIZED_DIR / "fundraising_shares.parquet",
        MANIFESTS_DIR / "fundraising_shares.json",
        external / "fec_house_senate_reports_2026.csv",
    ]
    for cycle in (2018, 2020, 2022, 2024, 2026):
        paths.extend((
            external / f"fec_candidate_committee_linkage_{cycle}.zip",
            external / f"fec_form2_{cycle}.csv",
            external / f"fec_form3_reports_{cycle}.zip",
        ))
    return tuple(paths)


def _snapshot_artifacts(paths: tuple[Path, ...]) -> dict[str, bytes | None]:
    return {
        str(path): (path.read_bytes() if path.is_file() else None)
        for path in paths
    }


def _restore_artifacts(snapshot: dict[str, bytes | None]) -> list[str]:
    restored: list[str] = []
    for raw_path, payload in snapshot.items():
        path = Path(raw_path)
        if payload is None:
            if path.is_file():
                path.unlink()
                restored.append(f"removed:{path.as_posix()}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        restored.append(path.as_posix())
    return restored


def _refresh_official_finance(*, as_of: str) -> dict[str, Any]:
    """Refresh the receipt-safe FEC store as one recoverable transaction."""
    finance_snapshot = _snapshot_artifacts(_finance_snapshot_paths())
    try:
        from midterms.evidence.fec import prepare_official_finance_history

        finance = prepare_official_finance_history(current_as_of=as_of)
        if finance.get("production_eligible") is not True:
            incomplete = {
                str(label): {
                    "missing_race_ids": block.get("missing_race_ids") or [],
                    "excluded_race_ids": block.get("excluded_race_ids") or [],
                }
                for label, block in (finance.get("coverage") or {}).items()
                if not bool((block or {}).get("production_eligible"))
            }
            raise ValueError(
                "refreshed official finance store is not production eligible: "
                + json.dumps(incomplete, sort_keys=True)
            )
        return {"status": "refreshed", **finance}
    except Exception as exc:  # noqa: BLE001
        restored = _restore_artifacts(finance_snapshot)
        return {
            "status": "refresh_failed",
            "error": str(exc),
            "restored_paths": restored,
            "transactional_restore": True,
        }


def _coverage_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    coverage = readiness.get("forecast_coverage") or {}
    summary = coverage.get("summary") if isinstance(coverage, dict) else {}
    return summary if isinstance(summary, dict) else {}


def _forecast_incomplete(summary: dict[str, Any]) -> bool:
    return (
        summary.get("forecast_complete") is not True
        or int(summary.get("n_fail") or 0) > 0
    )


def _failing_forecast_races(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    races = (
        ((readiness.get("domains") or {}).get("polls") or {}).get("current_race_coverage")
        or {}
    ).get("races") or []
    # Prefer the nested polls coverage; fall back to top-level forecast_coverage races
    # when readiness was synthesized without the nested domain block.
    if not races:
        races = ((readiness.get("forecast_coverage") or {}).get("races") or [])
    out: list[dict[str, Any]] = []
    for row in races:
        if not isinstance(row, dict):
            continue
        if row.get("forecast_status") != "fail":
            continue
        out.append({
            "race_id": row.get("race_id"),
            "state": row.get("state"),
            "forecast_status": row.get("forecast_status"),
            "evidence_status": row.get("evidence_status"),
            "reasons": row.get("reasons") or [],
            "contest_structure": row.get("contest_structure"),
            "n_raw_current_polls": row.get("n_raw_current_polls"),
            "n_candidate_compatible_polls": row.get("n_candidate_compatible_polls"),
        })
    return out


def classify_strict_forecast_coverage(
    readiness: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify whether forecast incompleteness is blocking under --strict.

    Source/evidence readiness is handled separately. This only answers whether
    forecast_complete=false is an approved genuine-multiway withholdal or a
    hard coverage failure.
    """
    from midterms.evidence.current_candidates import load_current_candidate_registry
    from midterms.evidence.modeling_paths import (
        classify_forecast_coverage_failures,
        multiway_plurality_race_ids,
    )

    summary = _coverage_summary(readiness)
    incomplete = _forecast_incomplete(summary)
    failing = _failing_forecast_races(readiness)
    reg = registry if registry is not None else load_current_candidate_registry()
    multiway_ids = multiway_plurality_race_ids(reg)
    classified = classify_forecast_coverage_failures(
        failing, multiway_race_ids=multiway_ids,
    )
    n_fail = int(summary.get("n_fail") or 0)
    # Approved withholdal requires: incompleteness explained entirely by
    # registry-classified multiway races with passing evidence, and counts that
    # match the coverage summary (no silent missing failure rows).
    multiway_withheld_only = bool(
        incomplete
        and n_fail > 0
        and len(failing) == n_fail
        and classified["multiway_withheld_only"]
        and len(classified["approved_multiway_withheld"]) == n_fail
    )
    blocking_forecast_incomplete = bool(incomplete and not multiway_withheld_only)
    return {
        **classified,
        "forecast_incomplete": incomplete,
        "multiway_withheld_only": multiway_withheld_only,
        "blocking_forecast_incomplete": blocking_forecast_incomplete,
        "n_fail": n_fail,
        "summary": summary,
    }


def refresh_safe_evidence(*, as_of: str) -> dict[str, Any]:
    """Run only adapters declared safe for unattended evidence preparation.

    Every adapter retains its own traceability and eligibility status. A failed
    refresh is recorded and cannot make the later readiness/seal stage pass.
    """
    results: dict[str, Any] = {}
    from midterms.evidence.presidential_results import build_vote_count_store

    try:
        results["presidential_prior"] = build_vote_count_store(fetch_missing=False)
    except Exception as exc:  # noqa: BLE001
        results["presidential_prior"] = {"status": "refresh_failed", "error": str(exc)}

    key = os.environ.get("FRED_API_KEY") or os.environ.get("ALFRED_API_KEY")
    from midterms.evidence.economics import SEALED_ALFRED_ARCHIVE

    if SEALED_ALFRED_ARCHIVE.is_file():
        try:
            from midterms.evidence.economics import ingest_alfred_vintage_archive

            results["economics"] = ingest_alfred_vintage_archive(SEALED_ALFRED_ARCHIVE)
        except Exception as exc:  # noqa: BLE001
            results["economics"] = {"status": "refresh_failed", "error": str(exc)}
    elif key:
        try:
            from midterms.evidence.economics import refresh_alfred_realtime_cutoffs

            results["economics"] = refresh_alfred_realtime_cutoffs({
                **required_historical_cutoffs(),
                "current": as_of,
            })
        except Exception as exc:  # noqa: BLE001
            results["economics"] = {"status": "refresh_failed", "error": str(exc)}
    else:
        results["economics"] = {
            "status": "missing_secret", "required_secret": "FRED_API_KEY",
        }

    try:
        from midterms.config import RAW_DIR
        from midterms.evidence.ingest import (
            fetch_votehub_polls,
            merge_live_polls_into_warehouse,
        )

        # Refresh only the two current polling products consumed by the
        # production snapshot. Historical MEDSL/FTE archives and rating
        # vintages have separate sealed preparation paths.
        fetched: list[dict[str, Any]] = []
        for poll_type, subject in (("us-senator", None), ("generic-ballot", "2026")):
            try:
                fetched.append(fetch_votehub_polls(
                    poll_type=poll_type,
                    subject=subject,
                    verify_advertised_subjects=(poll_type == "us-senator" and subject is None),
                    advertised_subject_year=2026 if poll_type == "us-senator" else None,
                ))
            except Exception as exc:  # noqa: BLE001
                fetched.append({
                    "name": f"votehub_{poll_type.replace('-', '_')}",
                    "error": str(exc),
                })
        results["poll_sources"] = fetched
        senator = next(
            (row for row in fetched if row.get("name") == "votehub_us_senator"), None
        )
        sealed_senator = RAW_DIR / "external" / "votehub_us_senator.json"
        senator_error = str((senator or {}).get("error") or "")
        source_coverage_inconsistent = senator_error.startswith(
            "VoteHub advertised-subject coverage failed:"
        )
        if senator and not senator.get("error"):
            results["polls"] = merge_live_polls_into_warehouse(
                election_id="senate-2026", replace_synthetic_for_election=True,
                payload_path=Path(str(senator["path"])),
            )
        elif sealed_senator.is_file() and not source_coverage_inconsistent:
            # Live VoteHub can be unreachable in CI. Rematerialize from the
            # sealed receipt so refresh-safe remains deterministic offline.
            results["polls"] = merge_live_polls_into_warehouse(
                election_id="senate-2026", replace_synthetic_for_election=True,
                payload_path=sealed_senator,
            )
            results["polls"] = {
                **results["polls"],
                "status": "sealed_fallback",
                "live_fetch_error": (senator or {}).get("error")
                or "VoteHub Senate response missing",
                "sealed_path": sealed_senator.as_posix(),
            }
        else:
            results["polls"] = {
                "status": (
                    "source_coverage_inconsistent"
                    if source_coverage_inconsistent
                    else "refresh_failed"
                ),
                "error": (senator or {}).get("error") or "VoteHub Senate response missing",
            }
    except Exception as exc:  # noqa: BLE001
        results["polls"] = {"status": "refresh_failed", "error": str(exc)}

    try:
        from midterms.evidence.approval import write_approval_store

        # Current approval is a hard production input.  A clean rebuild must
        # obtain it here rather than relying on a later test or forecast call
        # to mutate the sealed store.  The adapter records a fetch error and
        # readiness remains red when VoteHub is unavailable.
        results["approval"] = write_approval_store(
            prefer_votehub=True,
            require_live=True,
        )
    except Exception as exc:  # noqa: BLE001
        results["approval"] = {"status": "refresh_failed", "error": str(exc)}
    try:
        from midterms.evidence.ratings import prepare_vendored_pollster_rating_vintages

        results["pollster_ratings"] = prepare_vendored_pollster_rating_vintages()
    except Exception as exc:  # noqa: BLE001
        results["pollster_ratings"] = {"status": "refresh_failed", "error": str(exc)}
    # Finance is a hard production input with a 14-day retrieval-freshness
    # policy.  Refresh it before sealing, but treat the whole official FEC
    # source family as one transaction: a network/parser failure must not leave
    # a mixture of new raw receipts and old normalized data behind.
    results["finance"] = _refresh_official_finance(as_of=as_of)
    return {
        "preparation_version": PREPARE_EVIDENCE_VERSION,
        "mode": "refresh-safe",
        "as_of": as_of,
        "results": results,
    }


def seal_evidence(*, election_id: str, as_of: str) -> dict[str, Any]:
    readiness = write_source_readiness(election_id=election_id, as_of=as_of)
    if not readiness["ready_for_expensive_rebuild"]:
        raise ValueError(
            "required evidence sources are not ready: "
            + "; ".join(
                f"{row['domain']}={row['status']}" for row in readiness["blockers"]
            )
        )
    from midterms.evidence.ratings import seal_living_pollster_ratings_retrieval
    from midterms.evidence.warehouse import Warehouse

    # Pin living ratings retrieval before fingerprinting so git checkout mtimes
    # cannot change current_snapshot_id after the seal commit.
    seal_living_pollster_ratings_retrieval(retrieved_at=as_of)
    warehouse = Warehouse(ensure_fixtures=False)
    current = warehouse.build_as_of(as_of, election_id)
    historical: dict[str, str] = {}
    for label, cutoff in required_historical_cutoffs().items():
        historical[label] = warehouse.build_as_of(
            cutoff, "-".join(label.split("-")[:2]),
        ).snapshot_id
    bundle = build_evidence_bundle(
        as_of=as_of,
        current_snapshot_id=current.snapshot_id,
        historical_snapshot_ids=historical,
        domains=readiness["domains"],
        model_version=MODEL_VERSION,
    )
    path = write_evidence_bundle(bundle)
    pointer = {
        "schema_version": "evidence-bundle-pointer-v1",
        "model_version": MODEL_VERSION,
        "as_of": as_of,
        "evidence_bundle_id": bundle["evidence_bundle_id"],
        "evidence_bundle_sha256": bundle["evidence_bundle_sha256"],
        "manifest": path.name,
    }
    (MANIFESTS_DIR / "evidence_bundle_latest.json").write_text(
        json.dumps(pointer, indent=2), encoding="utf-8",
    )
    return {**pointer, "path": str(path)}


def prepare_evidence(
    *,
    election_id: str,
    as_of: str,
    mode: str,
    strict: bool = False,
) -> dict[str, Any]:
    if mode not in {"audit", "refresh-safe", "seal"}:
        raise ValueError("prepare-evidence mode must be audit, refresh-safe, or seal")
    poll_snapshot: dict[str, bytes | None] | None = None
    if mode == "refresh-safe":
        # Live VoteHub can introduce incompatible current polls that turn an
        # identity-sensitive warning into a hard forecast coverage failure.
        # Keep the pre-refresh sealed poll artifacts so we can roll back.
        poll_snapshot = _snapshot_poll_artifacts()
    refresh = refresh_safe_evidence(as_of=as_of) if mode == "refresh-safe" else None
    if mode == "seal":
        return {"mode": mode, "bundle": seal_evidence(election_id=election_id, as_of=as_of)}
    readiness = write_source_readiness(election_id=election_id, as_of=as_of)
    result = {"mode": mode, "refresh": refresh, "readiness": readiness}
    refresh_results = (refresh or {}).get("results") or {}
    polls = refresh_results.get("polls") or {}
    finance = refresh_results.get("finance") or {}
    coverage_state = classify_strict_forecast_coverage(readiness)
    coverage_summary = coverage_state["summary"]
    blocking_forecast_incomplete = bool(coverage_state["blocking_forecast_incomplete"])
    multiway_withheld_only = bool(coverage_state["multiway_withheld_only"])
    readiness_red = not readiness["ready_for_expensive_rebuild"]
    poll_domain_red = any(
        str(blocker.get("domain") or "") in {"polls", "generic_ballot"}
        for blocker in (readiness.get("blockers") or [])
        if isinstance(blocker, dict)
    )
    warnings: list[str] = []

    poll_status = polls.get("status")
    polls_may_have_mutated = poll_status not in {
        "refresh_failed",
        "source_coverage_inconsistent",
    }
    # Roll back live polls only when they create a *blocking* coverage/evidence
    # failure. Approved fail-closed multiway withholdal alone must not revert a
    # healthy poll refresh (that produced the false reverted_coverage_regression
    # signal when MT was intentionally unsupported).
    if (
        mode == "refresh-safe"
        and poll_snapshot is not None
        and polls_may_have_mutated
        and (blocking_forecast_incomplete or poll_domain_red)
    ):
        restored = _restore_poll_artifacts(poll_snapshot)
        readiness = write_source_readiness(election_id=election_id, as_of=as_of)
        result["readiness"] = readiness
        coverage_state = classify_strict_forecast_coverage(readiness)
        coverage_summary = coverage_state["summary"]
        blocking_forecast_incomplete = bool(coverage_state["blocking_forecast_incomplete"])
        multiway_withheld_only = bool(coverage_state["multiway_withheld_only"])
        readiness_red = not readiness["ready_for_expensive_rebuild"]
        poll_domain_red = any(
            str(blocker.get("domain") or "") in {"polls", "generic_ballot"}
            for blocker in (readiness.get("blockers") or [])
            if isinstance(blocker, dict)
        )
        polls = {
            **polls,
            "status": "reverted_coverage_regression",
            "restored_paths": restored,
            "live_fetch_status": polls.get("status"),
            "live_fetch_error": polls.get("error") or polls.get("live_fetch_error"),
        }
        if isinstance(refresh, dict):
            results = dict(refresh.get("results") or {})
            results["polls"] = polls
            refresh = {**refresh, "results": results}
            result["refresh"] = refresh
            refresh_results = results
        warnings.append("poll_refresh_reverted_coverage_regression")
        if not blocking_forecast_incomplete and not readiness_red:
            warnings.append("poll_refresh_rollback_restored_green_gates")

    poll_refresh_failed = polls.get("status") == "refresh_failed"
    poll_source_coverage_inconsistent = polls.get("status") == "source_coverage_inconsistent"
    poll_used_sealed_fallback = polls.get("status") == "sealed_fallback"
    poll_reverted = polls.get("status") == "reverted_coverage_regression"
    finance_refresh_failed = finance.get("status") == "refresh_failed"
    finance_fallback_freshness: dict[str, Any] | None = None
    finance_refresh_blocks = False
    if finance_refresh_failed:
        from midterms.evidence.eligibility import domain_freshness_from_provenance

        manifest_path = MANIFESTS_DIR / "fundraising_shares.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file() else None
        )
        finance_fallback_freshness = domain_freshness_from_provenance(
            "finance",
            checked_at=datetime.now(UTC).isoformat(),
            manifest=manifest,
            source_available=manifest is not None,
        )
        finance_refresh_blocks = finance_fallback_freshness.get("status") != "fresh"
    # Live VoteHub outages / regressive live pulls must not fail a rebuild when
    # sealed sources remain green after rollback (multiway-only incompleteness
    # is not a red source gate).
    poll_refresh_blocks = poll_source_coverage_inconsistent or (
        poll_refresh_failed and (readiness_red or blocking_forecast_incomplete)
    )
    if poll_refresh_failed and not poll_refresh_blocks:
        warnings.append("poll_refresh_failed_nonblocking")
    if poll_source_coverage_inconsistent:
        warnings.append("poll_source_coverage_inconsistent")
    if poll_used_sealed_fallback:
        warnings.append("poll_refresh_used_sealed_fallback")
    if poll_reverted and (blocking_forecast_incomplete or readiness_red):
        # Rollback could not restore green gates; keep the regression visible.
        warnings.append("poll_refresh_rollback_still_red")
    if finance_refresh_failed and not finance_refresh_blocks:
        warnings.append("finance_refresh_failed_fresh_fallback_retained")
    if finance_refresh_blocks:
        warnings.append("finance_refresh_failed_stale_fallback")
    if multiway_withheld_only:
        warnings.append("forecast_coverage_multiway_withheld_only")
    if warnings:
        result["strict_warnings"] = sorted(set(warnings))
    result["multiway_withheld"] = multiway_withheld_only
    result["approved_multiway_withheld_race_ids"] = list(
        coverage_state.get("approved_multiway_withheld_race_ids") or []
    )
    result["unexpected_forecast_failure_race_ids"] = list(
        coverage_state.get("unexpected_forecast_failure_race_ids") or []
    )
    failing_races = _failing_forecast_races(readiness)
    if strict and (
        readiness_red
        or blocking_forecast_incomplete
        or poll_refresh_blocks
        or finance_refresh_blocks
    ):
        result["strict_failure"] = True
        result["strict_failure_reasons"] = [
            reason
            for reason, active in (
                ("source_readiness_not_green", readiness_red),
                ("poll_source_coverage_inconsistent", poll_source_coverage_inconsistent),
                (
                    "poll_refresh_failed",
                    poll_refresh_blocks and not poll_source_coverage_inconsistent,
                ),
                ("forecast_coverage_incomplete", blocking_forecast_incomplete),
                ("finance_refresh_failed_stale_fallback", finance_refresh_blocks),
            )
            if active
        ]
        result["strict_failure_detail"] = {
            "blockers": readiness.get("blockers") or [],
            "forecast_coverage": coverage_summary,
            "failing_forecast_races": failing_races,
            "approved_multiway_withheld_race_ids": result[
                "approved_multiway_withheld_race_ids"
            ],
            "unexpected_forecast_failure_race_ids": result[
                "unexpected_forecast_failure_race_ids"
            ],
            "polls_refresh": {
                "status": polls.get("status"),
                "error": polls.get("error") or polls.get("live_fetch_error"),
                "restored_paths": polls.get("restored_paths"),
            },
            "finance_refresh": {
                "status": finance.get("status"),
                "error": finance.get("error"),
                "transactional_restore": finance.get("transactional_restore"),
                "fallback_freshness": finance_fallback_freshness,
            },
        }
    return result


def verify_bundle_file(path: str | Path, *, expected_id: str | None = None) -> dict[str, Any]:
    from midterms.evidence.evidence_bundle import verify_evidence_bundle

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return verify_evidence_bundle(payload, expected_id=expected_id)


def load_latest_evidence_bundle(*, expected_id: str | None = None) -> dict[str, Any]:
    from midterms.evidence.evidence_bundle import verify_evidence_bundle

    pointer_path = MANIFESTS_DIR / "evidence_bundle_latest.json"
    if not pointer_path.is_file():
        raise FileNotFoundError("sealed evidence bundle pointer is missing")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    bundle_path = MANIFESTS_DIR / str(pointer.get("manifest") or "")
    if not bundle_path.is_file():
        raise FileNotFoundError(f"sealed evidence bundle is missing: {bundle_path.name}")
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    verification = verify_evidence_bundle(
        payload, expected_id=expected_id or pointer.get("evidence_bundle_id"),
    )
    if not verification["ok"]:
        raise ValueError(f"sealed evidence bundle failed verification: {verification}")
    return {**payload, "manifest_path": str(bundle_path)}
