"""Semantic identities for resumable expensive rebuild stages."""

from __future__ import annotations

import hashlib
import json
from typing import Any

REBUILD_KEY_SCHEMA_VERSION = "research-rebuild-stage-keys-v1"


def _key(prefix: str, payload: dict[str, Any]) -> str:
    body = json.dumps(
        {"schema_version": REBUILD_KEY_SCHEMA_VERSION, **payload},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return f"{prefix}-{hashlib.sha256(body.encode('utf-8')).hexdigest()[:24]}"


def historical_validation_key(
    *,
    model_version: str,
    code_revision: str,
    evidence_bundle_id: str,
    years: list[int],
    leads: list[int],
    model_specification: dict[str, Any],
    inference_settings: dict[str, Any],
    seed_protocol: dict[str, Any],
) -> str:
    return _key("historical", {
        "model_version": model_version,
        "code_revision": code_revision,
        "evidence_bundle_id": evidence_bundle_id,
        "years": sorted(years),
        "leads": sorted(leads),
        "model_specification": model_specification,
        "inference_settings": inference_settings,
        "seed_protocol": seed_protocol,
    })


def stack_stage_key(*, oof_fingerprint: str, stack_protocol_version: str) -> str:
    return _key("stack", {
        "oof_fingerprint": oof_fingerprint,
        "stack_protocol_version": stack_protocol_version,
    })


def current_forecast_key(
    *,
    model_version: str,
    selected_model_specification: dict[str, Any],
    current_snapshot_id: str,
    inference_settings: dict[str, Any],
    seed: int,
) -> str:
    return _key("forecast", {
        "model_version": model_version,
        "selected_model_specification": selected_model_specification,
        "current_snapshot_id": current_snapshot_id,
        "inference_settings": inference_settings,
        "seed": seed,
    })
