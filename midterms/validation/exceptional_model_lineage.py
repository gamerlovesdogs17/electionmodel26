"""Semantic promotion authority for v0.9.23 exceptional model paths.

The ordinary stack, binary non-major adapter, and Alaska RCV adapter have
different statistical targets and validation classes.  This module keeps those
claims separate while binding every probability-affecting exceptional contract
into one deterministic lineage block for the validated model specification.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MANIFESTS_DIR,
    MODEL_VERSION,
    NORMALIZED_DIR,
    ROOT,
)
from midterms.evidence.current_candidates import (
    CURRENT_CANDIDATE_REGISTRY_PATH,
    load_current_candidate_registry,
)
from midterms.evidence.non_major_contract import (
    NON_MAJOR_ADAPTER_METHOD,
    NON_MAJOR_TARGET,
)
from midterms.model.alaska_rcv_adapter import (
    adapter_specification as alaska_specification,
)
from midterms.model.non_major_adapter import (
    ADAPTER_SPEC_VERSION as NON_MAJOR_SPEC_VERSION,
)
from midterms.model.non_major_adapter import (
    COMMON_VARIANCE_SHARE,
    POLL_ERROR_FLOOR,
    PRIOR_SD,
    STRUCTURAL_PREDICTIVE_SD,
    STRUCTURAL_PRIOR_METHOD,
)

LINEAGE_SCHEMA_VERSION = "exceptional-model-lineage-v1"
NON_MAJOR_CLASS = "limited_validation_exception_model"
ALASKA_CLASS = "limited_validation_alaska_rcv_model"
NON_MAJOR_RACE_IDS = {
    "senate-2026-ID", "senate-2026-MT", "senate-2026-NE", "senate-2026-SD",
}
ALASKA_RACE_ID = "senate-2026-AK"


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_json_sha256(path: str | Path) -> str:
    return canonical_sha256(json.loads(Path(path).read_text(encoding="utf-8")))


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing required promotion artifact: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid promotion artifact {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"promotion artifact is not a JSON object: {path.name}")
    return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _self_hash(payload: dict[str, Any], field: str) -> bool:
    semantic = dict(payload)
    stored = semantic.pop(field, None)
    return bool(stored and stored == canonical_sha256(semantic))


def binary_non_major_implementation_spec() -> dict[str, Any]:
    """Canonical implementation contract, excluding empirical results."""

    return {
        "adapter_spec_version": NON_MAJOR_SPEC_VERSION,
        "method": NON_MAJOR_ADAPTER_METHOD,
        "target": NON_MAJOR_TARGET,
        "structural_prior_method": STRUCTURAL_PRIOR_METHOD,
        "default_prior_sd": PRIOR_SD,
        "poll_error_floor": POLL_ERROR_FLOOR,
        "structural_predictive_sd": STRUCTURAL_PREDICTIVE_SD,
        "common_variance_share": COMMON_VARIANCE_SHARE,
    }


def _verify_file_lineage(source_lineage: dict[str, Any]) -> None:
    for name, block in source_lineage.items():
        path_value = str((block or {}).get("path") or "")
        expected = str((block or {}).get("sha256") or "")
        _require(bool(path_value and expected), f"non-major source lineage incomplete: {name}")
        path = (ROOT / path_value).resolve()
        _require(path.is_relative_to(ROOT.resolve()), f"non-major source path unsafe: {name}")
        _require(path.is_file(), f"non-major source missing: {name}")
        _require(
            hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            f"non-major source hash changed: {name}",
        )


def validate_binary_non_major_artifact(
    path: str | Path,
    *,
    verify_sources: bool = True,
) -> dict[str, Any]:
    payload = _read(Path(path))
    _require(payload.get("model_version") == MODEL_VERSION, "non-major model version is stale")
    _require(
        payload.get("adapter_spec_version") == NON_MAJOR_SPEC_VERSION,
        "non-major adapter spec version changed",
    )
    _require(payload.get("classification") == NON_MAJOR_CLASS, "non-major validation class changed")
    _require(payload.get("target") == NON_MAJOR_TARGET, "non-major target changed")
    _require(_self_hash(payload, "artifact_sha256"), "non-major artifact semantic hash changed")
    cases = payload.get("cases") or []
    aggregate = payload.get("aggregate") or {}
    _require(bool(cases) and int(aggregate.get("n") or 0) == len(cases), "non-major historical cases/aggregate incomplete")
    _require(payload.get("ordinary_oof_touched") is False, "non-major adapter falsely claims ordinary OOF")
    _require(payload.get("calibration_claim_allowed") is False, "non-major calibration claim must remain disabled")
    _require(aggregate.get("calibration_claim_allowed") is False, "non-major aggregate calibration claim must remain disabled")
    _require((payload.get("leave_one_race_out_integrity") or {}).get("safe") is True, "non-major validation leakage check failed")
    selected = payload.get("selected_prior") or {}
    selected_id = str(payload.get("selected_prior_spec_id") or "")
    _require(bool(selected_id) and selected.get("id") == selected_id, "non-major selected prior is unidentified")
    _require(float(selected.get("prior_sd")) == PRIOR_SD, "non-major selected prior differs from implementation")
    common = payload.get("common_shock_sensitivity") or {}
    _require(float(common.get("retained_share")) == COMMON_VARIANCE_SHARE, "non-major common shock differs from implementation")
    records = payload.get("selected_current_adapter_records") or {}
    _require(set(records) == NON_MAJOR_RACE_IDS, "non-major current race coverage changed")
    _require(all((row or {}).get("race_id") == race_id for race_id, row in records.items()), "non-major current adapter records are malformed")
    if verify_sources:
        _verify_file_lineage(payload.get("source_lineage") or {})
    implementation = binary_non_major_implementation_spec()
    return {
        "adapter_spec_version": NON_MAJOR_SPEC_VERSION,
        "implementation_spec": implementation,
        "implementation_spec_sha256": canonical_sha256(implementation),
        "validation_classification": NON_MAJOR_CLASS,
        "validation_sha256": canonical_json_sha256(path),
        "validation_artifact_sha256": payload["artifact_sha256"],
        "selected_prior_id": selected_id,
        "selected_prior": selected,
        "common_shock_share": COMMON_VARIANCE_SHARE,
        "target": NON_MAJOR_TARGET,
        "race_ids": sorted(records),
        "calibration_claim_allowed": False,
        "ordinary_oof_validates_adapter": False,
    }


def _validate_alaska_sources(
    *,
    manifest_path: Path,
    normalized_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read(manifest_path)
    normalized = _read(normalized_path)
    _require(manifest.get("validation_class") == ALASKA_CLASS, "Alaska source validation class changed")
    _require(manifest.get("production_eligible") is True, "Alaska source manifest is not production eligible")
    semantic = dict(normalized)
    stored_semantic = semantic.pop("semantic_sha256", None)
    _require(stored_semantic == canonical_sha256(semantic), "Alaska normalized semantic hash changed")
    _require(manifest.get("normalized_semantic_sha256") == stored_semantic, "Alaska manifest semantic lineage changed")
    _require(
        hashlib.sha256(normalized_path.read_bytes()).hexdigest() == manifest.get("normalized_sha256"),
        "Alaska normalized byte hash changed",
    )
    sources = manifest.get("sources") or {}
    _require(bool(sources), "Alaska official-source lineage is incomplete")
    for name, block in sources.items():
        source = (ROOT / str((block or {}).get("path") or "")).resolve()
        _require(source.is_relative_to(ROOT.resolve()), f"Alaska source path unsafe: {name}")
        _require(source.is_file(), f"Alaska official source missing: {name}")
        _require(bool((block or {}).get("source_url") and (block or {}).get("retrieved_at")), f"Alaska source provenance incomplete: {name}")
        _require(hashlib.sha256(source.read_bytes()).hexdigest() == block.get("sha256"), f"Alaska official source hash changed: {name}")
    return manifest, normalized


def validate_alaska_artifact(
    path: str | Path,
    *,
    manifest_path: str | Path = MANIFESTS_DIR / "alaska_rcv_sources.json",
    normalized_path: str | Path = NORMALIZED_DIR / "alaska_rcv_2026.json",
    candidate_registry_path: str | Path = CURRENT_CANDIDATE_REGISTRY_PATH,
    verify_sources: bool = True,
) -> dict[str, Any]:
    payload = _read(Path(path))
    expected_spec = alaska_specification()
    _require(payload.get("model_version") == MODEL_VERSION, "Alaska validation model version is stale")
    _require(payload.get("adapter_spec_version") == expected_spec["adapter_spec_version"], "Alaska adapter spec version changed")
    _require(payload.get("adapter_specification") == expected_spec, "Alaska adapter specification changed")
    _require(payload.get("validation_class") == ALASKA_CLASS, "Alaska validation class changed")
    _require(_self_hash(payload, "artifact_sha256"), "Alaska validation semantic hash changed")
    _require(payload.get("ordinary_oof_touched") is False, "Alaska falsely claims ordinary OOF validation")
    _require(payload.get("calibration_claim_allowed") is False, "Alaska calibration claim must remain disabled")
    cases = payload.get("historical_cases") or []
    aggregate = payload.get("aggregate") or {}
    _require(len(cases) == int(aggregate.get("n_analogs") or 0) > 0, "Alaska historical validation is incomplete")
    _require(all(row.get("held_out_from_transfer_fit") is True for row in cases), "Alaska held-out transfer validation is incomplete")
    _require(all(row.get("probability_or_calibration_claim") is False for row in cases), "Alaska historical diagnostics overclaim calibration")
    _require(aggregate.get("mass_conservation_all") is True, "Alaska mass-conservation check failed")
    # Surface known weak historical diagnostic — never hide low winner frequency.
    special_house = next(
        (row for row in cases if str(row.get("analog_id") or "") == "2022_special_house"),
        None,
    )
    _require(special_house is not None, "Alaska 2022 special House historical diagnostic is absent")
    special_freq = float(special_house.get("simulated_actual_winner_frequency") or -1.0)
    _require(0.0 <= special_freq <= 1.0, "Alaska 2022 special House winner frequency is malformed")

    _manifest, normalized = (
        _validate_alaska_sources(
            manifest_path=Path(manifest_path),
            normalized_path=Path(normalized_path),
        )
        if verify_sources
        else (_read(Path(manifest_path)), _read(Path(normalized_path)))
    )
    _require(payload.get("source_semantic_sha256") == normalized.get("semantic_sha256"), "Alaska validation source lineage changed")
    candidate_field = normalized.get("candidate_field") or []
    field_ids = [str(row.get("candidate_id") or "") for row in candidate_field]
    _require(len(field_ids) == 4 and len(set(field_ids)) == 4, "Alaska candidate identity collision exists")
    _require(f"{ALASKA_RACE_ID}:dan-s-sullivan" in field_ids, "Alaska incumbent Sullivan identity missing")
    _require(f"{ALASKA_RACE_ID}:daniel-j-sullivan-jr" in field_ids, "Alaska Daniel Sullivan identity missing")
    names = {str(row.get("candidate_id")): str(row.get("name")) for row in candidate_field}
    _require(names[f"{ALASKA_RACE_ID}:dan-s-sullivan"] == "Dan S. Sullivan", "Alaska incumbent Sullivan disambiguation failed")
    _require(names[f"{ALASKA_RACE_ID}:daniel-j-sullivan-jr"] == "Daniel J. Sullivan Jr.", "Alaska Daniel Sullivan disambiguation failed")

    registry = load_current_candidate_registry(Path(candidate_registry_path))
    registry_row = next((row for row in registry["races"] if row.get("race_id") == ALASKA_RACE_ID), None)
    _require(registry_row is not None, "Alaska current candidate registry row is missing")
    _require(set(registry_row.get("rcv_candidate_ids") or []) == set(field_ids), "Alaska registry candidate field differs from official evidence")
    reference = payload.get("reference_current_diagnostic") or {}
    p_win = reference.get("candidate_p_win") or {}
    output_ids = set(p_win)
    expected_output_ids = {*field_ids, f"{ALASKA_RACE_ID}:write-in-aggregate"}
    _require(output_ids == expected_output_ids, "Alaska current candidate probability output is incomplete")
    _require(abs(sum(float(value) for value in p_win.values()) - 1.0) < 1e-9, "Alaska current candidate probabilities do not conserve mass")
    return {
        "adapter_spec_version": expected_spec["adapter_spec_version"],
        "implementation_spec": expected_spec,
        "implementation_spec_sha256": canonical_sha256(expected_spec),
        "validation_classification": ALASKA_CLASS,
        "validation_sha256": canonical_json_sha256(path),
        "validation_artifact_sha256": payload["artifact_sha256"],
        "source_manifest_sha256": canonical_json_sha256(manifest_path),
        "source_semantic_sha256": normalized.get("semantic_sha256"),
        "candidate_registry_sha256": registry["registry_sha256"],
        "candidate_field_ids": sorted(expected_output_ids),
        "candidate_field_sha256": canonical_sha256(sorted(expected_output_ids)),
        "first_choice_spec_version": expected_spec["first_choice_spec_version"],
        "transfer_model_spec_version": expected_spec["transfer_model_spec_version"],
        "tabulation_spec_version": expected_spec["tabulation_spec_version"],
        "sensitivity_spec_version": expected_spec["sensitivity_spec_version"],
        "caucus_policy_version": expected_spec["caucus_policy_version"],
        "sullivan_disambiguation": True,
        "mass_conservation": True,
        "calibration_claim_allowed": False,
        "ordinary_oof_validates_adapter": False,
        # Known weak diagnostic: 2022 special House actual-winner frequency is very low.
        "special_house_actual_winner_frequency": special_freq,
        "special_house_weak_diagnostic_visible": True,
    }


def validate_forecast_coverage(path: str | Path) -> dict[str, Any]:
    payload = _read(Path(path))
    _require(payload.get("model_version") == MODEL_VERSION, "forecast coverage model version is stale")
    semantic = {
        key: payload.get(key)
        for key in (
            "schema_version", "model_version", "as_of",
            "candidate_state_snapshot_sha256", "races",
        )
    }
    _require(payload.get("artifact_sha256") == canonical_sha256(semantic), "forecast coverage semantic hash changed")
    races = payload.get("races") or []
    _require(bool(races), "forecast coverage contains no active races")
    _require(len({row.get("race_id") for row in races}) == len(races), "forecast coverage has duplicate race IDs")
    _require(all(row.get("candidate_identity_resolved") is True for row in races), "one or more active race identities are unresolved")
    _require(all(row.get("evidence_status") == "pass" for row in races), "forecast coverage has evidence failures")
    _require(all(row.get("forecast_status") in {"pass", "warning"} for row in races), "forecast coverage has hard failures")
    _require(all(row.get("probability_model_supported") is True for row in races), "active race lacks a supported predictive model")
    for row in races:
        if row.get("forecast_status") == "warning":
            _require(bool(row.get("reasons")), f"forecast warning is unclassified: {row.get('race_id')}")
    summary = payload.get("summary") or {}
    recomputed = {
        "n_races": len(races),
        "n_pass": sum(row.get("forecast_status") == "pass" for row in races),
        "n_warning": sum(row.get("forecast_status") == "warning" for row in races),
        "n_fail": sum(row.get("forecast_status") == "fail" for row in races),
        "n_evidence_fail": sum(row.get("evidence_status") == "fail" for row in races),
    }
    _require(all(int(summary.get(key, -1)) == value for key, value in recomputed.items()), "forecast coverage summary counts changed")
    _require(summary.get("evidence_ready") is True, "forecast coverage evidence_ready is false")
    _require(summary.get("forecast_complete") is True, "forecast coverage forecast_complete is false")
    _require(summary.get("source_and_model_coverage_separated") is True, "source readiness and model coverage are conflated")
    return {
        "artifact_sha256": payload["artifact_sha256"],
        "document_sha256": canonical_json_sha256(path),
        "as_of": payload.get("as_of"),
        "summary": recomputed | {
            "evidence_ready": True,
            "forecast_complete": True,
            "source_and_model_coverage_separated": True,
        },
        "active_race_ids": sorted(str(row["race_id"]) for row in races),
        "race_paths": {
            str(row["race_id"]): (
                "alaska_rcv_adapter"
                if row.get("contest_structure") == "ranked_choice_multiway"
                else "binary_non_major_adapter"
                if row.get("contest_structure") == "non_major_party_vs_republican"
                else "ordinary_stack"
            )
            for row in races
        },
    }


def validate_historical_equivalence(
    path: str | Path,
    *,
    recompute_current: bool = True,
) -> dict[str, Any]:
    payload = _read(Path(path))
    semantic = dict(payload)
    stored = semantic.pop("comparison_sha256", None)
    _require(stored == canonical_sha256(semantic), "historical equivalence semantic hash changed")
    rows = payload.get("formal_cutoffs") or []
    _require(payload.get("candidate_model_version") == MODEL_VERSION, "historical equivalence candidate version is stale")
    _require(payload.get("reference_model_version") == "senate-hierarchical-v0.9.22", "historical equivalence reference changed")
    _require(payload.get("classification") == "historically_equivalent", "ordinary historical evidence is not equivalent")
    _require(len(rows) == 8, "historical equivalence does not cover all formal cutoffs")
    _require(all(row.get("equivalent") is True and not row.get("changed_sections") for row in rows), "historical equivalence contains changed cutoffs")
    if recompute_current:
        from midterms.validation.historical_evidence_equivalence import (
            build_historical_projection,
        )

        current = build_historical_projection()
        actual = {
            key: value.get("semantic_sha256")
            for key, value in (current.get("cutoffs") or {}).items()
        }
        expected = {row["cutoff"]: row.get("after_semantic_sha256") for row in rows}
        _require(actual == expected, "current historical projection differs from equivalence artifact")
    return {
        "classification": "historically_equivalent",
        "formal_cutoffs_equivalent": len(rows),
        "comparison_sha256": stored,
        "document_sha256": canonical_json_sha256(path),
        "ordinary_only": True,
        "exceptional_models_validated_separately": True,
    }


def build_exceptional_model_lineage(
    *,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
    manifests_dir: str | Path = MANIFESTS_DIR,
    candidate_registry_path: str | Path = CURRENT_CANDIDATE_REGISTRY_PATH,
    normalized_alaska_path: str | Path = NORMALIZED_DIR / "alaska_rcv_2026.json",
    verify_sources: bool = True,
    recompute_historical: bool = True,
) -> dict[str, Any]:
    artifacts = Path(artifacts_dir)
    manifests = Path(manifests_dir)
    binary = validate_binary_non_major_artifact(
        artifacts / "non_major_adapter_validation_latest.json",
        verify_sources=verify_sources,
    )
    alaska = validate_alaska_artifact(
        artifacts / "alaska_rcv_validation_latest.json",
        manifest_path=manifests / "alaska_rcv_sources.json",
        normalized_path=normalized_alaska_path,
        candidate_registry_path=candidate_registry_path,
        verify_sources=verify_sources,
    )
    coverage = validate_forecast_coverage(
        artifacts / "current_race_poll_coverage_v0923.json",
    )
    historical = validate_historical_equivalence(
        artifacts / "historical_evidence_equivalence_v0923.json",
        recompute_current=recompute_historical,
    )
    payload = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "exceptional_models": {
            "binary_non_major": binary,
            "alaska_rcv": alaska,
        },
        "forecast_coverage": coverage,
        "historical_evidence_equivalence": historical,
    }
    payload["lineage_sha256"] = canonical_sha256(payload)
    return payload


def verify_exceptional_model_lineage(
    expected: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    current = build_exceptional_model_lineage(**kwargs)
    _require(expected.get("schema_version") == LINEAGE_SCHEMA_VERSION, "validated spec exceptional lineage schema is stale")
    _require(expected.get("lineage_sha256") == current.get("lineage_sha256"), "validated spec exceptional model lineage changed")
    _require(expected == current, "validated spec exceptional lineage content changed")
    return current


def validate_forecast_model_paths(
    forecast: dict[str, Any],
    validated_spec: dict[str, Any],
) -> dict[str, Any]:
    lineage = validated_spec.get("exceptional_model_lineage") or {}
    _require(lineage.get("lineage_sha256") == forecast.get("exceptional_model_lineage_sha256"), "forecast exceptional lineage differs from validated model spec")
    coverage = lineage.get("forecast_coverage") or {}
    expected_paths = coverage.get("race_paths") or {}
    rows = forecast.get("races") or []
    actual = {str(row.get("race_id")): row for row in rows}
    _require(set(actual) == set(expected_paths), "forecast race set differs from sealed coverage")
    for race_id, expected_path in expected_paths.items():
        row = actual[race_id]
        _require(row.get("modeling_path") == expected_path, f"{race_id} was produced by the wrong modeling path")
        if expected_path == "alaska_rcv_adapter":
            candidate_ids = {
                str(item.get("candidate_id")) for item in row.get("candidate_probabilities") or []
            }
            expected_ids = set(
                ((lineage.get("exceptional_models") or {}).get("alaska_rcv") or {}).get(
                    "candidate_field_ids"
                ) or []
            )
            _require(candidate_ids == expected_ids, "Alaska forecast candidate field differs from sealed model identity")
            _require(row.get("p_modeled_candidate") is None, "Alaska RCV output was reduced to an ordinary binary target")
        else:
            _require(row.get("p_modeled_candidate") is not None, f"{race_id} lacks predictive draws")
    _require(expected_paths.get("senate-2026-NE") == "binary_non_major_adapter", "NE may not use the ordinary model")
    _require(expected_paths.get("senate-2026-AK") == "alaska_rcv_adapter", "AK may use only the Alaska RCV model")
    _require(expected_paths.get("senate-2026-OH") == "ordinary_stack", "OH must remain in the ordinary stack")
    return {"ok": True, "n_races": len(actual), "race_paths_sha256": canonical_sha256(expected_paths)}


def verify_exceptional_promotion_inputs(**kwargs: Any) -> dict[str, Any]:
    try:
        lineage = build_exceptional_model_lineage(**kwargs)
        return {"ok": True, "lineage": lineage, "failures": []}
    except Exception as exc:  # noqa: BLE001 — fail closed with the exact gate error
        return {"ok": False, "lineage": None, "failures": [f"{type(exc).__name__}: {exc}"]}
