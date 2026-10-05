"""Cheap final-status synchronization for the top-level validation report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.config import MODEL_VERSION, ROOT
from midterms.validation.report import refresh_validation_report_status

BUNDLE_ID = "eb-aaaaaaaaaaaaaaaa"
BUNDLE_SHA = "a" * 64
SPEC_SHA = "b" * 64
RUN_ID = "synthetic-current-run"
RELEASE_ID = f"truth_v1_v0.9.22_{BUNDLE_ID}"


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _release_report() -> dict:
    return {
        "ok": True,
        "release_id": RELEASE_ID,
        "model_version": MODEL_VERSION,
        "PUBLIC_LIVE_ENABLED": False,
        "publication_surface": "research_only",
        "lineage": {
            "model_version": MODEL_VERSION,
            "evidence_bundle_id": BUNDLE_ID,
            "evidence_bundle_sha256": BUNDLE_SHA,
            "validated_model_spec_sha256": SPEC_SHA,
        },
    }


def _artifacts(root: Path) -> Path:
    art = root / "artifacts"
    art.mkdir()
    release = _release_report()
    acceptance = {
        "model_version": MODEL_VERSION,
        "ok": True,
        "promotion_ok": True,
        "g1_g11_ok": True,
        "full_validation_ok": True,
        "n_pass": 11,
        "n_partial": 0,
        "n_fail": 0,
        "failures": [],
        "gates": {
            f"G{i}": {
                "id": f"G{i}",
                "status": "pass",
                "ok": True,
                **({"detail": {"release_identity": release}} if i == 10 else {}),
            }
            for i in range(1, 12)
        },
    }
    _write(
        art / "validation_report_latest.json",
        {
            "model_version": MODEL_VERSION,
            "generated_at": "2026-10-03T00:00:00+00:00",
            "primary_holdout": 2022,
            "statistical_sentinel": {"must_not_change": [1, 2, 3]},
            "acceptance_gates": {
                "ok": False,
                "n_pass": 10,
                "n_partial": 0,
                "n_fail": 1,
                "failures": ["G10", "COHERENCE"],
            },
            "limitations": ["synthetic"],
        },
    )
    _write(art / "acceptance_gates_latest.json", acceptance)
    _write(
        art / "run_coherence_latest.json",
        {
            "model_version": MODEL_VERSION,
            "forecast_run_id": RUN_ID,
            "forecast_publishable": True,
            "PUBLIC_LIVE_ENABLED": False,
            "ok": True,
        },
    )
    _write(
        art / "forecast_latest.json",
        {
            "model_version": MODEL_VERSION,
            "run_id": RUN_ID,
            "publishable": True,
            "publication_surface": "research_only",
            "evidence_bundle_id": BUNDLE_ID,
            "evidence_bundle_sha256": BUNDLE_SHA,
            "validated_model_spec_sha256": SPEC_SHA,
        },
    )
    _write(
        art / "evidence_eligibility_latest.json",
        {
            "model_version": MODEL_VERSION,
            "forecast_run_id": RUN_ID,
            "publishable": True,
        },
    )
    _write(
        art / "validated_model_spec_latest.json",
        {
            "model_version": MODEL_VERSION,
            "evidence_bundle_id": BUNDLE_ID,
            "evidence_bundle_sha256": BUNDLE_SHA,
            "spec_sha256": SPEC_SHA,
            "selected_structure_id": "pymc",
            "selected_poll_structure_id": "psc-synthetic",
        },
    )
    _write(
        art / "stack_weights_oof.json",
        {
            "source_model_version": MODEL_VERSION,
            "stack_weights": {"pymc": 0.25, "state_space": 0.75},
        },
    )
    _write(
        art / "stack_reliability_crossfit_latest.json",
        {
            "model_version": MODEL_VERSION,
            "raw_production_stack": {
                "n": 20,
                "brier": 0.1,
                "calibration_slope_intercept": {
                    "slope": 1.01,
                    "intercept": -0.01,
                },
                "reliability_overconfidence": {"n_overconfident": 0},
            },
        },
    )
    _write(
        art / "source_readiness_latest.json",
        {
            "model_version": MODEL_VERSION,
            "ready_for_expensive_rebuild": True,
            "blockers": [],
        },
    )
    _write(
        art / "current_race_poll_coverage_v0923.json",
        {
            "schema_version": "current-race-poll-coverage-v3",
            "summary": {
                "forecast_complete": True,
                "n_races": 35,
                "n_fail": 0,
            },
        },
    )
    _write(
        art / "non_major_adapter_validation_latest.json",
        {
            "model_version": MODEL_VERSION,
            "classification": "limited_validation_exception_model",
            "calibration_claim_allowed": False,
        },
    )
    _write(
        art / "alaska_rcv_validation_latest.json",
        {
            "model_version": MODEL_VERSION,
            "validation_class": "limited_validation_exception_model",
            "calibration_claim_allowed": False,
        },
    )
    _write(
        art / "historical_evidence_equivalence_v0923.json",
        {
            "model_version": MODEL_VERSION,
            "equivalent": True,
        },
    )
    return art


def test_status_refresh_replaces_stale_gates_without_touching_statistics(
    tmp_path: Path,
) -> None:
    art = _artifacts(tmp_path)
    result = refresh_validation_report_status(
        artifacts_dir=art,
        release_identity_report=_release_report(),
    )
    refreshed = json.loads((art / "validation_report_latest.json").read_text())
    assert refreshed["statistical_sentinel"] == {"must_not_change": [1, 2, 3]}
    assert refreshed["acceptance_gates"]["n_pass"] == 11
    assert refreshed["acceptance_gates"]["failures"] == []
    assert refreshed["current_status"]["evidence_bundle_id"] == BUNDLE_ID
    assert refreshed["current_status"]["validated_model_spec_sha256"] == SPEC_SHA
    assert refreshed["current_status"]["release_id"] == RELEASE_ID
    assert refreshed["current_status"]["run_coherence_ok"] is True
    assert refreshed["current_status"]["cycle_crossfit_reliability"]["n"] == 20
    assert refreshed["stack_weights_artifact"] == {
        "pymc": 0.25,
        "state_space": 0.75,
    }
    assert result["promotion_eligible"] is True
    markdown = (art / "validation_report_latest.md").read_text()
    assert "pass/partial/fail: 11/0/0" in markdown
    assert "failures: []" in markdown
    assert "Run coherence: True" in markdown


def test_status_refresh_fails_closed_on_lineage_disagreement(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    forecast_path = art / "forecast_latest.json"
    forecast = json.loads(forecast_path.read_text())
    forecast["validated_model_spec_sha256"] = "c" * 64
    _write(forecast_path, forecast)
    original = (art / "validation_report_latest.json").read_bytes()
    with pytest.raises(ValueError, match="validated model spec hashes differ"):
        refresh_validation_report_status(
            artifacts_dir=art,
            release_identity_report=_release_report(),
        )
    assert (art / "validation_report_latest.json").read_bytes() == original


def test_rebuild_refreshes_report_only_after_strict_acceptance() -> None:
    workflow = (ROOT / ".github" / "workflows" / "rebuild-research.yml").read_text(
        encoding="utf-8"
    )
    gates = workflow.index("acceptance-gates --strict")
    refresh = workflow.index("validation-report --refresh-status-only")
    upload = workflow.index("Upload generated model artifacts")
    assert gates < refresh < upload
