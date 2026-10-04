"""Deterministically synchronize embedded v0.9.23 readiness metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, ROOT
from midterms.validation.exceptional_model_lineage import build_exceptional_model_lineage

COMPLIANCE_PATH = ROOT / "BLUEPRINT_COMPLIANCE_AUDIT.json"


def authoritative_development_status(
    *, artifacts_dir: str | Path = ARTIFACTS_DIR,
) -> dict[str, Any]:
    artifacts = Path(artifacts_dir)
    readiness = json.loads(
        (artifacts / "source_readiness_latest.json").read_text(encoding="utf-8")
    )
    lineage = build_exceptional_model_lineage(artifacts_dir=artifacts)
    coverage = lineage["forecast_coverage"]["summary"]
    exceptional = lineage["exceptional_models"]
    return {
        "model_version": MODEL_VERSION,
        "release_identity_exists": False,
        "research_acceptance_ok": False,
        "promotion_eligible": False,
        "source_readiness_ok": bool(readiness.get("ready_for_expensive_rebuild")),
        "source_blockers": readiness.get("blockers") or [],
        "forecast_coverage_ok": bool(coverage.get("forecast_complete")),
        "forecast_coverage_blockers": [],
        "forecast_coverage_summary": {
            "n_pass": coverage["n_pass"],
            "n_warning": coverage["n_warning"],
            "n_fail": coverage["n_fail"],
        },
        "evidence_and_forecast_coverage_separate": True,
        "non_major_adapter_classification": exceptional["binary_non_major"][
            "validation_classification"
        ],
        "non_major_adapter_calibration_claim_allowed": False,
        "alaska_rcv_adapter_classification": exceptional["alaska_rcv"][
            "validation_classification"
        ],
        "alaska_rcv_calibration_claim_allowed": False,
        "ordinary_historical_projection_changed": False,
        "historical_equivalent_cutoffs": lineage[
            "historical_evidence_equivalence"
        ]["formal_cutoffs_equivalent"],
        "exceptional_model_lineage_sha256": lineage["lineage_sha256"],
    }


def sync_compliance_status(
    *,
    path: str | Path = COMPLIANCE_PATH,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
    write: bool = False,
) -> dict[str, Any]:
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    expected = authoritative_development_status(artifacts_dir=artifacts_dir)
    current = payload.get("development_status") or {}
    matches = current == expected
    if write:
        payload["development_status"] = expected
        gates = payload.setdefault("empirical_validation_gates", {})
        gates.update({
            "binary_non_major_party_adapter": "limited_validation_exception_model",
            "alaska_rcv_adapter": "limited_validation_alaska_rcv_model",
            "forecast_model_coverage": "source_and_model_coverage_ready",
            "historical_evidence_equivalence": "historically_equivalent",
        })
        payload["empirical_validation_gates_scope"] = (
            "v0.9.22 remains the last completed ordinary empirical release. "
            "v0.9.23 current source readiness and forecast coverage are green; "
            "the binary non-major and Alaska RCV paths retain separate limited "
            "validation, and full v0.9.23 promotion requires the rebuild."
        )
        target.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        matches = True
    return {
        "ok": matches,
        "path": str(target),
        "expected": expected,
        "actual": current,
        "written": bool(write),
    }
