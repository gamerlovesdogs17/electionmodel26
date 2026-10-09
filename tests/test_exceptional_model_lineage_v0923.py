"""Focused regression coverage for v0.9.23 exceptional-model promotion gates.

Mutates temporary fixture copies — never the canonical repository artifacts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from midterms.config import (
    ARTIFACTS_DIR,
    MANIFESTS_DIR,
    MODEL_VERSION,
    NORMALIZED_DIR,
    ROOT,
)
from midterms.evidence.current_candidates import CURRENT_CANDIDATE_REGISTRY_PATH
from midterms.model.alaska_rcv_adapter import (
    adapter_specification as alaska_specification,
)
from midterms.ops.release_identity import validate_release_identity_document
from midterms.ops.reproducibility import compare_forecast_artifacts
from midterms.ops.run_coherence import check_run_coherence
from midterms.validation.compliance_status import (
    authoritative_development_status,
    sync_compliance_status,
)
from midterms.validation.exceptional_model_lineage import (
    ALASKA_CLASS,
    NON_MAJOR_CLASS,
    build_exceptional_model_lineage,
    canonical_sha256,
    validate_alaska_artifact,
    validate_binary_non_major_artifact,
    validate_forecast_coverage,
    validate_forecast_model_paths,
    validate_historical_equivalence,
    verify_exceptional_promotion_inputs,
)
from midterms.validation.validated_model_spec import (
    ORDINARY_STATISTICAL_SPEC_FIELDS,
    ordinary_statistical_spec_sha256,
)

ARTIFACTS = ARTIFACTS_DIR
NON_MAJOR = ARTIFACTS / "non_major_adapter_validation_latest.json"
ALASKA = ARTIFACTS / "alaska_rcv_validation_latest.json"
COVERAGE = ARTIFACTS / "current_race_poll_coverage_v0923.json"
HISTORICAL = ARTIFACTS / "historical_evidence_equivalence_v0923.json"
ALASKA_MANIFEST = MANIFESTS_DIR / "alaska_rcv_sources.json"
ALASKA_NORMALIZED = NORMALIZED_DIR / "alaska_rcv_2026.json"
V0922_IDENTITY = ARTIFACTS / "release_identity_v0922_eb-3e91ca3a90628d69.json"


@pytest.fixture
def scratch_dir() -> Iterator[Path]:
    """Writable scratch space that does not depend on a locked pytest basetemp."""

    root = Path(tempfile.mkdtemp(prefix="em26_exceptional_"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _rehash_artifact(payload: dict[str, Any], field: str = "artifact_sha256") -> dict[str, Any]:
    body = dict(payload)
    body.pop(field, None)
    body[field] = canonical_sha256(body)
    return body


def _rehash_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    semantic = {
        key: body.get(key)
        for key in (
            "schema_version",
            "model_version",
            "as_of",
            "candidate_state_snapshot_sha256",
            "races",
        )
    }
    body["artifact_sha256"] = canonical_sha256(semantic)
    return body


def _rehash_normalized(payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    body.pop("semantic_sha256", None)
    body["semantic_sha256"] = canonical_sha256(body)
    return body


def _copy_promotion_bundle(scratch: Path) -> dict[str, Path]:
    artifacts = scratch / "artifacts"
    manifests = scratch / "manifests"
    normalized = scratch / "normalized"
    current = scratch / "current"
    artifacts.mkdir()
    manifests.mkdir()
    normalized.mkdir()
    current.mkdir()
    paths = {
        "artifacts": artifacts,
        "manifests": manifests,
        "normalized": normalized,
        "non_major": artifacts / "non_major_adapter_validation_latest.json",
        "alaska": artifacts / "alaska_rcv_validation_latest.json",
        "coverage": artifacts / "current_race_poll_coverage_v0923.json",
        "historical": artifacts / "historical_evidence_equivalence_v0923.json",
        "manifest": manifests / "alaska_rcv_sources.json",
        "normalized_alaska": normalized / "alaska_rcv_2026.json",
        "registry": current / "current_candidates_2026.json",
        "readiness": artifacts / "source_readiness_latest.json",
    }
    shutil.copy2(NON_MAJOR, paths["non_major"])
    shutil.copy2(ALASKA, paths["alaska"])
    shutil.copy2(COVERAGE, paths["coverage"])
    shutil.copy2(HISTORICAL, paths["historical"])
    shutil.copy2(ALASKA_MANIFEST, paths["manifest"])
    shutil.copy2(ALASKA_NORMALIZED, paths["normalized_alaska"])
    shutil.copy2(CURRENT_CANDIDATE_REGISTRY_PATH, paths["registry"])
    shutil.copy2(ARTIFACTS / "source_readiness_latest.json", paths["readiness"])
    # Rebind sealed v0.9.23 lineage fixtures to the current code identity so
    # mutation tests exercise the intended gate rather than a version mismatch.
    for key, rehash in (
        ("non_major", _rehash_artifact),
        ("alaska", _rehash_artifact),
        ("coverage", _rehash_coverage),
    ):
        payload = _load(paths[key])
        payload["model_version"] = MODEL_VERSION
        _write(paths[key], rehash(payload))
    hist = _load(paths["historical"])
    hist["candidate_model_version"] = MODEL_VERSION
    if "model_version" in hist:
        hist["model_version"] = MODEL_VERSION
    _write(paths["historical"], hist)
    return paths


def _lineage_kwargs(paths: dict[str, Path], **overrides: Any) -> dict[str, Any]:
    kwargs = {
        "artifacts_dir": paths["artifacts"],
        "manifests_dir": paths["manifests"],
        "candidate_registry_path": paths["registry"],
        "normalized_alaska_path": paths["normalized_alaska"],
        "verify_sources": False,
        "recompute_historical": False,
    }
    kwargs.update(overrides)
    return kwargs


def _expect_block(fn, *args, needle: str, **kwargs) -> None:
    with pytest.raises(ValueError, match=needle):
        fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Binary non-major
# ---------------------------------------------------------------------------


def test_missing_non_major_validation_blocks_strict(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    paths["non_major"].unlink()
    report = verify_exceptional_promotion_inputs(**_lineage_kwargs(paths))
    assert report["ok"] is False
    assert any("non_major" in f or "missing" in f for f in report["failures"])


def test_stale_non_major_model_version_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _rehash_artifact({**_load(paths["non_major"]), "model_version": "senate-hierarchical-v0.9.22"})
    _write(paths["non_major"], payload)
    _expect_block(
        validate_binary_non_major_artifact,
        paths["non_major"],
        verify_sources=False,
        needle="non-major model version is stale",
    )


def test_altered_non_major_adapter_spec_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _rehash_artifact({
        **_load(paths["non_major"]),
        "adapter_spec_version": "binary-non-major-adapter-v1",
    })
    _write(paths["non_major"], payload)
    _expect_block(
        validate_binary_non_major_artifact,
        paths["non_major"],
        verify_sources=False,
        needle="adapter spec version",
    )


def test_altered_non_major_semantic_hash_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["non_major"])
    payload["artifact_sha256"] = "0" * 64
    _write(paths["non_major"], payload)
    _expect_block(
        validate_binary_non_major_artifact,
        paths["non_major"],
        verify_sources=False,
        needle="semantic hash",
    )


def test_non_major_calibration_claim_enabled_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["non_major"])
    payload["calibration_claim_allowed"] = True
    _write(paths["non_major"], _rehash_artifact(payload))
    _expect_block(
        validate_binary_non_major_artifact,
        paths["non_major"],
        verify_sources=False,
        needle="calibration claim",
    )


def test_non_major_ordinary_oof_claim_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["non_major"])
    payload["ordinary_oof_touched"] = True
    _write(paths["non_major"], _rehash_artifact(payload))
    _expect_block(
        validate_binary_non_major_artifact,
        paths["non_major"],
        verify_sources=False,
        needle="ordinary OOF",
    )


# ---------------------------------------------------------------------------
# Alaska RCV
# ---------------------------------------------------------------------------


def test_missing_alaska_validation_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    paths["alaska"].unlink()
    report = verify_exceptional_promotion_inputs(**_lineage_kwargs(paths))
    assert report["ok"] is False
    assert any("alaska" in f.lower() or "missing" in f for f in report["failures"])


def test_stale_alaska_model_version_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _rehash_artifact({**_load(paths["alaska"]), "model_version": "senate-hierarchical-v0.9.22"})
    _write(paths["alaska"], payload)
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=False,
        needle="model version is stale",
    )


def test_alaska_adapter_spec_mismatch_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["alaska"])
    payload["adapter_specification"] = {
        **alaska_specification(),
        "reference_settings": {
            **alaska_specification()["reference_settings"],
            "transfer_concentration": 1.0,
        },
    }
    _write(paths["alaska"], _rehash_artifact(payload))
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=False,
        needle="adapter specification",
    )


def test_alaska_source_manifest_mismatch_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    manifest = _load(paths["manifest"])
    manifest["normalized_semantic_sha256"] = "0" * 64
    _write(paths["manifest"], manifest)
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=True,
        needle="manifest semantic lineage|normalized semantic",
    )


def test_alaska_official_source_hash_mismatch_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    # Point a copied manifest at a real ROOT source but with a wrong hash.
    manifest = _load(paths["manifest"])
    first = next(iter(manifest["sources"].values()))
    first["sha256"] = "0" * 64
    _write(paths["manifest"], manifest)
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=True,
        needle="official source hash",
    )


def test_alaska_candidate_identity_collision_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    normalized = _load(paths["normalized_alaska"])
    field = list(normalized["candidate_field"])
    field[1] = {**field[1], "candidate_id": field[0]["candidate_id"]}
    normalized["candidate_field"] = field
    normalized = _rehash_normalized(normalized)
    _write(paths["normalized_alaska"], normalized)
    alaska = _load(paths["alaska"])
    alaska["source_semantic_sha256"] = normalized["semantic_sha256"]
    _write(paths["alaska"], _rehash_artifact(alaska))
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=False,
        needle="identity collision",
    )


def test_alaska_sullivan_disambiguation_failure_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    normalized = _load(paths["normalized_alaska"])
    for row in normalized["candidate_field"]:
        if row.get("candidate_id") == "senate-2026-AK:daniel-j-sullivan-jr":
            row["name"] = "Dan S. Sullivan"
    normalized = _rehash_normalized(normalized)
    _write(paths["normalized_alaska"], normalized)
    alaska = _load(paths["alaska"])
    alaska["source_semantic_sha256"] = normalized["semantic_sha256"]
    _write(paths["alaska"], _rehash_artifact(alaska))
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=False,
        needle="Sullivan disambiguation",
    )


def test_alaska_mass_conservation_failure_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["alaska"])
    payload["aggregate"] = {**payload["aggregate"], "mass_conservation_all": False}
    _write(paths["alaska"], _rehash_artifact(payload))
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=False,
        needle="mass-conservation",
    )


def test_alaska_calibration_brier_overclaim_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["alaska"])
    payload["calibration_claim_allowed"] = True
    _write(paths["alaska"], _rehash_artifact(payload))
    _expect_block(
        validate_alaska_artifact,
        paths["alaska"],
        manifest_path=paths["manifest"],
        normalized_path=paths["normalized_alaska"],
        candidate_registry_path=paths["registry"],
        verify_sources=False,
        needle="calibration claim",
    )


# ---------------------------------------------------------------------------
# Forecast coverage / historical / warnings
# ---------------------------------------------------------------------------


def test_incomplete_current_race_coverage_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["coverage"])
    # Claim forecast_complete while unsupported multiway races remain failed.
    races = payload["races"]
    payload["summary"] = {
        "n_races": len(races),
        "n_pass": sum(r.get("forecast_status") == "pass" for r in races),
        "n_warning": sum(r.get("forecast_status") == "warning" for r in races),
        "n_fail": sum(r.get("forecast_status") == "fail" for r in races),
        "n_evidence_fail": sum(r.get("evidence_status") == "fail" for r in races),
        "evidence_ready": True,
        "forecast_complete": True,
        "source_and_model_coverage_separated": True,
    }
    _write(paths["coverage"], _rehash_coverage(payload))
    _expect_block(
        validate_forecast_coverage,
        paths["coverage"],
        needle="forecast_complete",
    )


def test_unsupported_predictive_model_blocks(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    payload = _load(paths["coverage"])
    for row in payload["races"]:
        if row.get("race_id") == "senate-2026-OH":
            row["probability_model_supported"] = False
            row["forecast_status"] = "fail"
    races = payload["races"]
    payload["summary"] = {
        "n_races": len(races),
        "n_pass": sum(r.get("forecast_status") == "pass" for r in races),
        "n_warning": sum(r.get("forecast_status") == "warning" for r in races),
        "n_fail": sum(r.get("forecast_status") == "fail" for r in races),
        "n_evidence_fail": 0,
        "evidence_ready": True,
        "forecast_complete": False,
        "source_and_model_coverage_separated": True,
    }
    _write(paths["coverage"], _rehash_coverage(payload))
    _expect_block(
        validate_forecast_coverage,
        paths["coverage"],
        needle="supported predictive model|hard failures",
    )


def test_classified_warnings_alone_are_permitted() -> None:
    coverage = validate_forecast_coverage(COVERAGE)
    assert coverage["summary"]["n_warning"] > 0
    # Fail-closed multiway races are explicit forecast fails, not silent gaps.
    # After principal-binary reclassification only MT remains genuine multiway.
    assert coverage["summary"]["n_fail_closed_multiway"] >= 1
    assert coverage["summary"]["forecast_complete"] is False


def test_evidence_readiness_and_forecast_coverage_remain_separate() -> None:
    coverage = validate_forecast_coverage(COVERAGE)
    assert coverage["summary"]["source_and_model_coverage_separated"] is True
    readiness = _load(ARTIFACTS / "source_readiness_latest.json")
    assert "ready_for_expensive_rebuild" in readiness
    # Source readiness artifact must not be the forecast-coverage authority.
    assert "n_pass" not in readiness
    assert coverage["summary"]["evidence_ready"] is True
    assert coverage["summary"]["forecast_complete"] is False


def test_historical_ordinary_evidence_equivalence_is_8_of_8() -> None:
    result = validate_historical_equivalence(HISTORICAL, recompute_current=False)
    assert result["formal_cutoffs_equivalent"] == 8
    assert result["classification"] == "historically_equivalent"
    assert result["ordinary_only"] is True


# ---------------------------------------------------------------------------
# Lineage / validated spec / release identity
# ---------------------------------------------------------------------------


def test_validated_lineage_contains_both_exceptional_models(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    lineage = build_exceptional_model_lineage(**_lineage_kwargs(paths))
    models = lineage["exceptional_models"]
    assert models["binary_non_major"]["validation_classification"] == NON_MAJOR_CLASS
    assert models["alaska_rcv"]["validation_classification"] == ALASKA_CLASS
    assert models["alaska_rcv"]["special_house_weak_diagnostic_visible"] is True
    assert models["alaska_rcv"]["special_house_actual_winner_frequency"] < 0.05
    assert models["alaska_rcv"]["first_choice_spec_version"]
    assert models["binary_non_major"]["selected_prior_id"]


def test_non_major_spec_change_alters_exceptional_lineage(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    before = build_exceptional_model_lineage(**_lineage_kwargs(paths))
    payload = _load(paths["non_major"])
    payload["selected_prior"] = {
        **payload["selected_prior"],
        "selection_reason": "mutated-for-test",
    }
    _write(paths["non_major"], _rehash_artifact(payload))
    after = build_exceptional_model_lineage(**_lineage_kwargs(paths))
    assert before["lineage_sha256"] != after["lineage_sha256"]


def test_alaska_spec_change_alters_exceptional_lineage(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    before = build_exceptional_model_lineage(**_lineage_kwargs(paths))
    payload = _load(paths["alaska"])
    # Touch a non-probability field that still enters the validation artifact hash.
    payload["historical_design"] = "mutated-for-test"
    _write(paths["alaska"], _rehash_artifact(payload))
    after = build_exceptional_model_lineage(**_lineage_kwargs(paths))
    assert before["lineage_sha256"] != after["lineage_sha256"]


def test_old_release_identity_fails_when_exceptional_hashes_missing() -> None:
    # Release identities must bind exceptional hashes; a stripped lineage fails.
    from midterms.ops.release_identity import _version_suffix

    tag = _version_suffix(MODEL_VERSION)
    payload = {
        "schema_version": "truth_v1",
        "release_id": f"truth_v1_{tag}_eb-test",
        "model_version": MODEL_VERSION,
        "PUBLIC_LIVE_ENABLED": False,
        "publication_surface": "research_only",
        "config": {"MODEL_VERSION": MODEL_VERSION},
        "data_hashes": {"x": "1"},
        "lineage": {
            "evidence_bundle_id": "eb-test",
            "validated_model_spec_sha256": "a" * 64,
        },
    }
    problems = validate_release_identity_document(
        payload,
        expected_model_version=MODEL_VERSION,
        expected_evidence_bundle_id="eb-test",
    )
    assert any("exceptional_model_lineage_sha256" in p for p in problems)


def test_modeling_paths_ak_ne_oh() -> None:
    coverage = validate_forecast_coverage(COVERAGE)
    paths = coverage["race_paths"]
    assert paths["senate-2026-AK"] == "alaska_rcv_adapter"
    assert paths["senate-2026-NE"] == "binary_non_major_adapter"
    assert paths["senate-2026-OH"] == "ordinary_stack"
    assert paths["senate-2026-ID"] == "binary_non_major_adapter"
    assert paths["senate-2026-MT"] == "multiway_plurality_adapter"
    assert paths["senate-2026-SD"] == "binary_non_major_adapter"
    assert paths["senate-2026-MT"] != "binary_non_major_adapter"


# ---------------------------------------------------------------------------
# Run coherence / independent rebuild
# ---------------------------------------------------------------------------


def test_run_coherence_fails_on_exceptional_lineage_drift(scratch_dir: Path) -> None:
    lineage = build_exceptional_model_lineage(
        verify_sources=False, recompute_historical=False,
    )
    spec = {
        "schema_version": "validated-model-spec-v2",
        "model_version": MODEL_VERSION,
        "spec_sha256": "b" * 64,
        "evidence_bundle_id": "eb-test",
        "evidence_bundle_sha256": "c" * 64,
        "exceptional_model_lineage": lineage,
        "exceptional_model_lineage_sha256": lineage["lineage_sha256"],
        "exceptional_models": lineage["exceptional_models"],
        "production_research_eligible": True,
        "lineage_checks": {"ok": True},
    }
    # Stabilize spec_sha256 for loaders that check it — write a self-hashed stub.
    body = dict(spec)
    body.pop("spec_sha256", None)
    from midterms.validation.validated_model_spec import canonical_sha256 as spec_hash

    body["spec_sha256"] = spec_hash(body)
    _write(scratch_dir / "validated_model_spec_latest.json", body)

    forecast = {
        "run_id": "run-x",
        "model_version": MODEL_VERSION,
        "publishable": True,
        "run_class": "publication",
        "publication_surface": "research_only",
        "evidence_bundle_id": "eb-test",
        "evidence_bundle_sha256": "c" * 64,
        "validated_model_spec_sha256": body["spec_sha256"],
        "exceptional_model_lineage_sha256": "0" * 64,
        "evidence_fingerprint": {"sha256": "abc"},
        "evidence_eligibility": {"publishable": True, "run_class": "publication"},
        "snapshot": {"snapshot_id": "snap"},
        "races": [
            {
                "race_id": rid,
                "modeling_path": path,
                "p_modeled_candidate": 0.5 if path != "alaska_rcv_adapter" else None,
                "candidate_probabilities": (
                    [
                        {"candidate_id": cid, "p_win": 0.25}
                        for cid in lineage["exceptional_models"]["alaska_rcv"][
                            "candidate_field_ids"
                        ]
                    ]
                    if path == "alaska_rcv_adapter"
                    else []
                ),
            }
            for rid, path in lineage["forecast_coverage"]["race_paths"].items()
        ],
    }
    _write(scratch_dir / "forecast_latest.json", forecast)
    _write(
        scratch_dir / "evidence_eligibility_latest.json",
        {
            "publishable": True,
            "run_class": "publication",
            "forecast_run_id": "run-x",
            "evidence_fingerprint": {"sha256": "abc"},
        },
    )
    report = check_run_coherence(artifacts_dir=scratch_dir)
    assert report["ok"] is False
    assert any("exceptional" in m.lower() for m in report["mismatches"])


def test_independent_rebuild_fails_when_modeling_path_differs() -> None:
    sealed = {
        "chamber": {"p_dem_majority": 0.5, "expected_dem_seats": 50.0},
        "races": [
            {"race_id": "senate-2026-NE", "p_modeled_candidate": 0.4, "modeling_path": "binary_non_major_adapter"},
            {"race_id": "senate-2026-OH", "p_modeled_candidate": 0.55, "modeling_path": "ordinary_stack"},
        ],
    }
    rebuilt = {
        "chamber": {"p_dem_majority": 0.5, "expected_dem_seats": 50.0},
        "races": [
            {"race_id": "senate-2026-NE", "p_modeled_candidate": 0.4, "modeling_path": "ordinary_stack"},
            {"race_id": "senate-2026-OH", "p_modeled_candidate": 0.55, "modeling_path": "ordinary_stack"},
        ],
    }
    cmp = compare_forecast_artifacts(sealed, rebuilt)
    assert cmp["ok"] is False
    path_check = next(c for c in cmp["checks"] if c["name"] == "modeling_path_identity")
    assert path_check["ok"] is False


def test_independent_rebuild_fails_when_exceptional_lineage_differs() -> None:
    sealed = {
        "validated_model_spec_sha256": "a" * 64,
        "exceptional_model_lineage_sha256": "b" * 64,
        "chamber": {"p_dem_majority": 0.5, "expected_dem_seats": 50.0},
        "races": [{"race_id": "senate-2026-OH", "p_modeled_candidate": 0.5, "modeling_path": "ordinary_stack"}],
    }
    rebuilt = {
        "validated_model_spec_sha256": "a" * 64,
        "exceptional_model_lineage_sha256": "c" * 64,
        "chamber": {"p_dem_majority": 0.5, "expected_dem_seats": 50.0},
        "races": [{"race_id": "senate-2026-OH", "p_modeled_candidate": 0.5, "modeling_path": "ordinary_stack"}],
    }
    cmp = compare_forecast_artifacts(sealed, rebuilt)
    assert cmp["ok"] is True  # numeric/path ok
    # Full independent rebuild report ANDs lineage identity separately.
    ok = (
        bool(cmp.get("ok"))
        and sealed.get("validated_model_spec_sha256") == rebuilt.get("validated_model_spec_sha256")
        and sealed.get("exceptional_model_lineage_sha256")
        == rebuilt.get("exceptional_model_lineage_sha256")
    )
    assert ok is False


def test_forecast_model_paths_validate_against_lineage(scratch_dir: Path) -> None:
    lineage = build_exceptional_model_lineage(
        verify_sources=False, recompute_historical=False,
    )
    spec = {
        "exceptional_model_lineage": lineage,
        "exceptional_model_lineage_sha256": lineage["lineage_sha256"],
    }
    races = []
    for rid, path in lineage["forecast_coverage"]["race_paths"].items():
        row: dict[str, Any] = {
            "race_id": rid,
            "modeling_path": path,
            "p_modeled_candidate": (
                None
                if path in {"alaska_rcv_adapter", "multiway_plurality_adapter"}
                else 0.5
            ),
            "win_probability_status": (
                "fail_closed" if path == "multiway_plurality_adapter" else "ok"
            ),
        }
        if path == "alaska_rcv_adapter":
            row["candidate_probabilities"] = [
                {"candidate_id": cid, "p_win": 1.0 / len(lineage["exceptional_models"]["alaska_rcv"]["candidate_field_ids"])}
                for cid in lineage["exceptional_models"]["alaska_rcv"]["candidate_field_ids"]
            ]
        races.append(row)
    forecast = {
        "exceptional_model_lineage_sha256": lineage["lineage_sha256"],
        "races": races,
    }
    assert validate_forecast_model_paths(forecast, spec)["ok"] is True
    # Wrong path for AK must fail.
    for row in races:
        if row["race_id"] == "senate-2026-AK":
            row["modeling_path"] = "ordinary_stack"
            row["p_modeled_candidate"] = 0.5
    with pytest.raises(ValueError, match="wrong modeling path|AK may use"):
        validate_forecast_model_paths(forecast, spec)


# ---------------------------------------------------------------------------
# Compliance / cache / frozen v0.9.22
# ---------------------------------------------------------------------------


def test_compliance_sync_detects_silent_drift(scratch_dir: Path) -> None:
    paths = _copy_promotion_bundle(scratch_dir)
    compliance = {
        "development_status": {
            "model_version": MODEL_VERSION,
            "source_readiness_ok": False,
            "forecast_coverage_ok": False,
        },
        "empirical_validation_gates": {},
    }
    path = scratch_dir / "BLUEPRINT_COMPLIANCE_AUDIT.json"
    _write(path, compliance)
    # Patch build to use our temp artifacts via sync's artifacts_dir.
    report = sync_compliance_status(
        path=path, artifacts_dir=paths["artifacts"], write=False,
    )
    assert report["ok"] is False
    written = sync_compliance_status(
        path=path, artifacts_dir=paths["artifacts"], write=True,
    )
    assert written["ok"] is True
    assert written["written"] is True
    status = _load(path)["development_status"]
    assert status["model_version"] == MODEL_VERSION
    assert status["non_major_adapter_classification"] == NON_MAJOR_CLASS
    assert status["alaska_rcv_adapter_classification"] == ALASKA_CLASS
    assert status["historical_equivalent_cutoffs"] == 8
    assert status["promotion_eligible"] is False


def test_ordinary_statistical_cache_key_ignores_exceptional_content() -> None:
    base = {key: f"value-{key}" for key in ORDINARY_STATISTICAL_SPEC_FIELDS}
    base["selected_poll_structure"] = {"study_effect": True}
    sha_a = ordinary_statistical_spec_sha256(base)
    noisy = {
        **base,
        "exceptional_model_lineage": {"lineage_sha256": "x" * 64},
        "exceptional_models": {"alaska_rcv": {"validation_sha256": "y" * 64}},
        "code_commit_sha": "deadbeef",
        "production_research_eligible": True,
    }
    sha_b = ordinary_statistical_spec_sha256(noisy)
    assert sha_a == sha_b
    changed = {**base, "selected_structure_id": "different"}
    assert ordinary_statistical_spec_sha256(changed) != sha_a


def test_frozen_v0922_release_identity_unchanged() -> None:
    assert V0922_IDENTITY.is_file()
    blob = (
        subprocess.check_output(["git", "hash-object", str(V0922_IDENTITY)])
        .decode()
        .strip()
    )
    # Stable across this task: file must remain a tracked, unmodified blob.
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--", str(V0922_IDENTITY)],
    ).decode().strip()
    assert status == "", f"v0.9.22 release identity was modified: {status}"
    assert len(blob) == 40
    payload = _load(V0922_IDENTITY)
    assert payload.get("model_version") == "senate-hierarchical-v0.9.22"


def test_workflow_keys_ordinary_statistical_not_exceptional_hashes() -> None:
    text = (ROOT / ".github" / "workflows" / "rebuild-research.yml").read_text(
        encoding="utf-8"
    )
    assert "ordinary_statistical_spec_sha256" in text
    assert "selection-oof-v0923-" in text
    assert "canonical-oof-v0923-" in text
    # Exceptional validation hashes must not appear in expensive OOF cache keys.
    for line in text.splitlines():
        if "key: selection-oof-" in line or "key: canonical-oof-" in line:
            assert "non_major_adapter_validation" not in line
            assert "alaska_rcv_validation" not in line
            assert "current_race_poll_coverage" not in line
            assert "historical_evidence_equivalence" not in line


def test_workflow_yaml_parses() -> None:
    payload = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "rebuild-research.yml").read_text(
            encoding="utf-8"
        )
    )
    assert "jobs" in payload
    assert "prepare_evidence" in payload["jobs"]
    assert "rebuild" in payload["jobs"]


def test_strict_promotion_inputs_pass_on_canonical_artifacts() -> None:
    report = verify_exceptional_promotion_inputs(
        verify_sources=True, recompute_historical=False,
    )
    assert report["ok"] is True
    lineage = report["lineage"]
    assert lineage["model_version"] == MODEL_VERSION
    assert lineage["historical_evidence_equivalence"]["formal_cutoffs_equivalent"] == 8


def test_authoritative_development_status_is_pending_promotion() -> None:
    status = authoritative_development_status()
    assert status["model_version"] == MODEL_VERSION
    assert status["promotion_eligible"] is False
    assert status["research_acceptance_ok"] is False
    assert status["source_readiness_ok"] is True
    # Multiway fail-closed races keep forecast coverage incomplete until a
    # historically supported multiway model exists.
    assert status["forecast_coverage_ok"] is False
