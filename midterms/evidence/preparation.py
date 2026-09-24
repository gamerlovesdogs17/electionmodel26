"""Evidence preparation orchestration with audit, safe refresh, and seal modes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, MODEL_VERSION
from midterms.evidence.evidence_bundle import build_evidence_bundle, write_evidence_bundle
from midterms.evidence.source_readiness import required_historical_cutoffs, write_source_readiness

PREPARE_EVIDENCE_VERSION = "prepare-evidence-v1"


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
    if key:
        try:
            from midterms.evidence.economics import try_refresh_alfred

            results["economics"] = try_refresh_alfred(as_of=as_of)
        except Exception as exc:  # noqa: BLE001
            results["economics"] = {"status": "refresh_failed", "error": str(exc)}
    else:
        results["economics"] = {
            "status": "missing_secret", "required_secret": "FRED_API_KEY",
        }

    try:
        from midterms.evidence.ingest import try_fetch_preferred

        fetched = try_fetch_preferred()
        results["poll_sources"] = fetched
        senator = next(
            (row for row in fetched if row.get("name") == "votehub_us_senator"), None
        )
        if senator and not senator.get("error"):
            from midterms.evidence.ingest import merge_live_polls_into_warehouse

            results["polls"] = merge_live_polls_into_warehouse(
                election_id="senate-2026", replace_synthetic_for_election=True,
                payload_path=Path(str(senator["path"])),
            )
        else:
            results["polls"] = {
                "status": "refresh_failed",
                "error": (senator or {}).get("error") or "VoteHub Senate response missing",
            }
    except Exception as exc:  # noqa: BLE001
        results["polls"] = {"status": "refresh_failed", "error": str(exc)}

    try:
        from midterms.evidence.approval import write_approval_store

        results["approval"] = write_approval_store(prefer_votehub=True)
    except Exception as exc:  # noqa: BLE001
        results["approval"] = {"status": "refresh_failed", "error": str(exc)}
    results["finance"] = {
        "status": "adapter_not_configured",
        "reason": "unattended finance refresh remains disabled until sealed-store preservation is guaranteed",
    }
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
    from midterms.evidence.warehouse import Warehouse

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
    refresh = refresh_safe_evidence(as_of=as_of) if mode == "refresh-safe" else None
    if mode == "seal":
        return {"mode": mode, "bundle": seal_evidence(election_id=election_id, as_of=as_of)}
    readiness = write_source_readiness(election_id=election_id, as_of=as_of)
    result = {"mode": mode, "refresh": refresh, "readiness": readiness}
    if strict and not readiness["ready_for_expensive_rebuild"]:
        result["strict_failure"] = True
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
