"""Publication contract for optional expert and market overlays."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR


OVERLAY_VALIDATION_VERSION = "overlay-validation-v1"
DEFAULT_PATH = ARTIFACTS_DIR / "overlay_validation.json"


def load_overlay_contracts(path: Path | None = None) -> dict[str, Any]:
    source = path or DEFAULT_PATH
    if not source.exists():
        return {
            "schema_version": OVERLAY_VALIDATION_VERSION,
            "status": "missing",
            "contracts": {},
            "artifact_sha256": None,
        }
    payload = json.loads(source.read_text(encoding="utf-8"))
    contracts = payload.get("contracts") or {}
    if not isinstance(contracts, dict):
        raise ValueError("overlay validation contracts must be keyed by overlay name")
    payload["artifact_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    return payload


def validate_overlay_contract(
    name: str,
    *,
    weight: float,
    path: Path | None = None,
) -> dict[str, Any]:
    """Validate provenance and OOS status; never infer validation from code presence."""
    payload = load_overlay_contracts(path)
    contract = (payload.get("contracts") or {}).get(name)
    reasons: list[str] = []
    if not contract:
        reasons.append("contract missing")
        contract = {}
    required = (
        "overlay_version", "historical_data_available", "tested_cycles",
        "tested_leads", "transformation", "tested_weight", "oos_metric_delta",
        "calibration_delta", "validation_status", "source_hashes",
        "timestamp_integrity", "publication_allowed",
    )
    missing = [key for key in required if key not in contract]
    if missing:
        reasons.append("missing fields: " + ", ".join(missing))
    if contract.get("validation_status") != "validated_nested_oos":
        reasons.append("validation status is not validated_nested_oos")
    if not bool(contract.get("timestamp_integrity")):
        reasons.append("timestamp/vintage integrity not established")
    if not bool(contract.get("publication_allowed")):
        reasons.append("publication use is not allowed")
    try:
        if abs(float(contract.get("tested_weight")) - float(weight)) > 1e-12:
            reasons.append("requested weight differs from validated weight")
    except (TypeError, ValueError):
        reasons.append("validated weight is unavailable")
    if not contract.get("source_hashes"):
        reasons.append("source hashes are unavailable")
    return {
        "name": name,
        "ok": not reasons,
        "policy": "core_only_when_unvalidated",
        "requested_weight": float(weight),
        "reasons": reasons,
        "contract": contract,
        "artifact_sha256": payload.get("artifact_sha256"),
    }


def publication_overlay_policy(
    *, rating_weight: float, market_weight: float, control_weight: float,
    path: Path | None = None,
) -> dict[str, Any]:
    checks = {
        "expert_ratings": validate_overlay_contract(
            "expert_ratings", weight=rating_weight, path=path
        ),
        "race_markets": validate_overlay_contract(
            "race_markets", weight=market_weight, path=path
        ),
        "control_market": validate_overlay_contract(
            "control_market", weight=control_weight, path=path
        ),
    }
    return {
        "schema_version": OVERLAY_VALIDATION_VERSION,
        "policy": "core_only_when_unvalidated",
        "checks": checks,
        "use_ratings": checks["expert_ratings"]["ok"],
        "use_race_markets": checks["race_markets"]["ok"],
        "use_control_market": checks["control_market"]["ok"],
        "empirical_validation_pending": [name for name, item in checks.items() if not item["ok"]],
    }
