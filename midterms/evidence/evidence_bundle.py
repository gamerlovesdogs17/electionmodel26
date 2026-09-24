"""Content-addressed evidence bundle manifests for expensive rebuilds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, MODEL_VERSION
from midterms.evidence.freshness import FRESHNESS_POLICY_VERSION
from midterms.evidence.source_registry import SOURCE_PREPARATION_REGISTRY_VERSION

EVIDENCE_BUNDLE_SCHEMA_VERSION = "evidence-bundle-v1"
NON_SEMANTIC_KEYS = frozenset({
    "path", "absolute_path", "local_path", "mtime", "modified_at", "generated_at",
    "checked_at", "username", "workspace",
})


def _semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _semantic(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in NON_SEMANTIC_KEYS
        }
    if isinstance(value, (list, tuple, set)):
        items = [_semantic(item) for item in value]
        # Bundle lists describe sets of source identities; canonical sorting
        # prevents filesystem or dataframe row order from changing identity.
        return sorted(
            items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), default=str),
        )
    return value


def canonical_bundle_sha256(payload: dict[str, Any]) -> str:
    clean = _semantic(payload)
    blob = json.dumps(clean, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_evidence_bundle(
    *,
    as_of: str,
    current_snapshot_id: str,
    historical_snapshot_ids: dict[str, str],
    domains: dict[str, dict[str, Any]],
    model_version: str = MODEL_VERSION,
) -> dict[str, Any]:
    """Build a portable manifest from already-verified source identities."""
    domain_bindings: dict[str, Any] = {}
    for name, block in sorted(domains.items()):
        domain_bindings[name] = {
            key: block.get(key)
            for key in (
                "status", "semantic_sha256", "normalized_sha256", "manifest_sha256",
                "snapshot_sha256", "parser_version", "source_hashes", "vintage_ids",
                "required_for_core", "required_for_historical_validation",
            )
            if block.get(key) is not None
        }
    body = {
        "schema_version": EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "model_version": model_version,
        "as_of": as_of,
        "current_snapshot_id": current_snapshot_id,
        "historical_snapshot_ids": dict(sorted(historical_snapshot_ids.items())),
        "domains": domain_bindings,
        "source_preparation_registry_version": SOURCE_PREPARATION_REGISTRY_VERSION,
        "freshness_policy_version": FRESHNESS_POLICY_VERSION,
    }
    digest = canonical_bundle_sha256(body)
    return {
        **body,
        "evidence_bundle_sha256": digest,
        "evidence_bundle_id": f"eb-{digest[:16]}",
    }


def verify_evidence_bundle(payload: dict[str, Any], *, expected_id: str | None = None) -> dict[str, Any]:
    body = {
        key: value for key, value in payload.items()
        if key not in {"evidence_bundle_sha256", "evidence_bundle_id"}
    }
    actual = canonical_bundle_sha256(body)
    expected_sha = str(payload.get("evidence_bundle_sha256") or "")
    actual_id = f"eb-{actual[:16]}"
    ok = actual == expected_sha and payload.get("evidence_bundle_id") == actual_id
    if expected_id is not None:
        ok = ok and actual_id == expected_id
    return {
        "ok": ok,
        "actual_sha256": actual,
        "actual_id": actual_id,
        "expected_sha256": expected_sha,
        "expected_id": expected_id or payload.get("evidence_bundle_id"),
    }


def write_evidence_bundle(payload: dict[str, Any], *, directory: Path | None = None) -> Path:
    verification = verify_evidence_bundle(payload)
    if not verification["ok"]:
        raise ValueError("evidence bundle identity is invalid")
    directory = directory or MANIFESTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"evidence_bundle_{payload['evidence_bundle_id']}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
