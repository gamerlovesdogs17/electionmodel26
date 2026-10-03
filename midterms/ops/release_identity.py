"""Versioned, immutable release identities for canonical truth/model lineage."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MODEL_VERSION,
    PUBLIC_LIVE_ENABLED,
    RAW_DIR,
    ROOT,
)

IDENTITY_SCHEMA_VERSION = "release-identity-v2"
TRUTH_SCHEMA_VERSION = "truth_v1"
LEGACY_V0921_RELEASE_IDENTITY_PATH = ARTIFACTS_DIR / "release_identity_v0921.json"
LEDGER_PATH = RAW_DIR / "external" / "official_senate_ledger.json"
EXPECTATIONS_PATH = RAW_DIR / "external" / "independent_chamber_expectations.json"
FTE_CSV = RAW_DIR / "external" / "certified" / "fte_senate.csv"

JSON_HASH_MODE = "canonical_json_sha256_v1"
RAW_HASH_MODE = "raw_sha256_v1"
_MODEL_VERSION_RE = re.compile(r"^(?P<family>.+)-(?P<version>v\d+\.\d+\.\d+)$")


def _version_suffix(model_version: str) -> str:
    match = _MODEL_VERSION_RE.fullmatch(model_version)
    if not match:
        raise ValueError(f"unsupported model version format: {model_version!r}")
    return match.group("version")


def release_identity_path(
    model_version: str,
    evidence_bundle_id: str,
    *,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
) -> Path:
    """Resolve one exact version/bundle identity; never fall back to another seal."""
    token = _version_suffix(model_version).replace(".", "")
    if not re.fullmatch(r"eb-[a-f0-9]{16}", evidence_bundle_id):
        raise ValueError(f"unsupported evidence bundle ID: {evidence_bundle_id!r}")
    return Path(artifacts_dir) / f"release_identity_{token}_{evidence_bundle_id}.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_sha256(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=ROOT
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _truth_paths(paths: Mapping[str, str | Path] | None = None) -> dict[str, Path]:
    if paths is not None:
        return {name: Path(path) for name, path in paths.items()}
    return {
        "official_senate_ledger.json": LEDGER_PATH,
        "independent_chamber_expectations.json": EXPECTATIONS_PATH,
        "fte_senate.csv": FTE_CSV,
    }


def _default_hash_mode(name: str) -> str:
    return JSON_HASH_MODE if name.lower().endswith(".json") else RAW_HASH_MODE


def current_truth_fingerprints(
    *,
    paths: Mapping[str, str | Path] | None = None,
    hash_modes: Mapping[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Fingerprint every required truth input with an explicit hash contract."""
    out: dict[str, dict[str, str]] = {}
    for name, path in _truth_paths(paths).items():
        if not path.is_file():
            continue
        mode = str((hash_modes or {}).get(name) or _default_hash_mode(name))
        if mode == JSON_HASH_MODE:
            digest = _canonical_json_sha256(path)
        elif mode == RAW_HASH_MODE:
            digest = _sha256(path)
        else:
            raise ValueError(
                f"unsupported release identity hash mode for {name}: {mode}"
            )
        out[name] = {"sha256": digest, "hash_mode": mode}
    return out


def current_truth_hashes() -> dict[str, str]:
    """Compatibility projection used by older callers."""
    return {name: item["sha256"] for name, item in current_truth_fingerprints().items()}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def current_model_lineage(
    *,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
) -> dict[str, Any]:
    """Resolve the validated, version-stable lineage sealed by this identity.

    Forecast/rebuild identity remains a separate G10 check. This release seal
    binds the model specification and evidence used to produce those runs, so a
    new daily forecast does not mutate an immutable model-version identity.
    """
    artifacts_dir = Path(artifacts_dir)
    spec_path = artifacts_dir / "validated_model_spec_latest.json"
    if not spec_path.is_file():
        raise ValueError("missing validated_model_spec_latest.json")
    spec = _load_json(spec_path)
    stable = dict(spec)
    expected_spec_sha = stable.pop("spec_sha256", None)
    from midterms.validation.validated_model_spec import canonical_sha256

    if expected_spec_sha != canonical_sha256(stable):
        raise ValueError("validated model spec semantic identity changed")
    if spec.get("model_version") != MODEL_VERSION:
        raise ValueError("validated model spec model version is stale")
    if not spec.get("production_research_eligible"):
        raise ValueError("validated model spec is not production/research eligible")
    checks = spec.get("lineage_checks") or {}
    if not checks or not all(bool(value) for value in checks.values()):
        raise ValueError("validated model spec contains unresolved lineage checks")
    return {
        "model_version": spec.get("model_version"),
        "validated_model_spec_sha256": expected_spec_sha,
        "evidence_bundle_id": spec.get("evidence_bundle_id"),
        "evidence_bundle_sha256": spec.get("evidence_bundle_sha256"),
        "source_readiness_sha256": spec.get("source_readiness_sha256"),
        "selected_structure_id": spec.get("selected_structure_id"),
        "selected_poll_structure_id": spec.get("selected_poll_structure_id"),
        "canonical_oof_sha256": spec.get("canonical_oof_sha256"),
        "canonical_frozen_draws_sha256": spec.get("canonical_frozen_draws_sha256"),
        "stack_weights_sha256": spec.get("stack_weights_sha256"),
        "uncertainty_calibration_sha256": spec.get("uncertainty_calibration_sha256"),
        "validated_code_commit": spec.get("code_commit_sha"),
    }


def validate_release_identity_document(
    payload: Mapping[str, Any],
    *,
    expected_model_version: str,
    expected_evidence_bundle_id: str | None = None,
) -> list[str]:
    """Validate identity metadata without claiming current-input equivalence."""
    problems: list[str] = []
    version = _version_suffix(expected_model_version)
    expected_release_id = f"{TRUTH_SCHEMA_VERSION}_{version}"
    if expected_evidence_bundle_id:
        expected_release_id += f"_{expected_evidence_bundle_id}"
    if payload.get("model_version") != expected_model_version:
        problems.append(
            "release identity model_version does not match active model version"
        )
    if payload.get("release_id") != expected_release_id:
        problems.append(f"release_id must be {expected_release_id}")
    if payload.get("schema_version") != TRUTH_SCHEMA_VERSION:
        problems.append(f"schema_version must be {TRUTH_SCHEMA_VERSION}")
    config = payload.get("config") or {}
    if config.get("MODEL_VERSION") != expected_model_version:
        problems.append("release identity config MODEL_VERSION mismatch")
    if payload.get("PUBLIC_LIVE_ENABLED") is not False:
        problems.append("release identity must retain PUBLIC_LIVE_ENABLED=false")
    if payload.get("publication_surface") != "research_only":
        problems.append(
            "release identity must retain publication_surface=research_only"
        )
    if not payload.get("data_hashes"):
        problems.append("release identity has no canonical truth hashes")
    if (
        expected_evidence_bundle_id
        and (payload.get("lineage") or {}).get("evidence_bundle_id")
        != expected_evidence_bundle_id
    ):
        problems.append("release identity evidence bundle mismatch")
    return problems


def write_release_identity(
    *,
    notes: list[str] | None = None,
    out_path: str | Path | None = None,
    model_version: str = MODEL_VERSION,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
    truth_paths: Mapping[str, str | Path] | None = None,
    lineage: Mapping[str, Any] | None = None,
    seal_source_commit: str | None = None,
) -> dict[str, Any]:
    """Write the immutable identity for one empirically validated model version."""
    artifacts_dir = Path(artifacts_dir)
    resolved_lineage = dict(
        lineage or current_model_lineage(artifacts_dir=artifacts_dir)
    )
    if resolved_lineage.get("model_version") != model_version:
        raise ValueError("release identity lineage model version mismatch")
    evidence_bundle_id = str(resolved_lineage.get("evidence_bundle_id") or "")
    path = Path(
        out_path
        or release_identity_path(
            model_version,
            evidence_bundle_id,
            artifacts_dir=artifacts_dir,
        )
    )
    if path.exists():
        report = verify_release_identity(
            path=path,
            expected_model_version=model_version,
            artifacts_dir=artifacts_dir,
            truth_paths=truth_paths,
            expected_lineage=lineage,
        )
        if report.get("ok"):
            return {**_load_json(path), "path": str(path), "existing": True}
        raise FileExistsError(
            f"release identity is immutable and does not verify: {path}: "
            f"{report.get('problems') or report.get('mismatches')}"
        )

    fingerprints = current_truth_fingerprints(paths=truth_paths)
    required = set(_truth_paths(truth_paths))
    if set(fingerprints) != required:
        missing = sorted(required - set(fingerprints))
        raise ValueError(f"missing canonical truth inputs: {missing}")
    version = _version_suffix(model_version)
    manifest = {
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "release_id": f"{TRUTH_SCHEMA_VERSION}_{version}_{evidence_bundle_id}",
        "model_version": model_version,
        "generated_at": datetime.now(UTC).isoformat(),
        # This is the code revision empirically frozen by the validated spec.
        "code_commit": resolved_lineage.get("validated_code_commit"),
        # The commit containing the already-generated canonical artifacts being sealed.
        "seal_source_commit": seal_source_commit or _git_commit(),
        "PUBLIC_LIVE_ENABLED": PUBLIC_LIVE_ENABLED,
        "publication_surface": "research_only",
        "schema_version": TRUTH_SCHEMA_VERSION,
        "data_hashes": {name: item["sha256"] for name, item in fingerprints.items()},
        "data_hash_modes": {
            name: item["hash_mode"] for name, item in fingerprints.items()
        },
        "lineage": resolved_lineage,
        "config": {
            "MODEL_VERSION": model_version,
            "PUBLIC_LIVE_ENABLED": PUBLIC_LIVE_ENABLED,
        },
        "notes": notes
        or [
            "Immutable current-model release identity; historical version seals are retained.",
            "JSON truth inputs use canonical semantic hashes; binary/text source archives keep explicit raw hashes.",
            "Forecast-run reproducibility is verified separately by G10 lite and independent rebuild checks.",
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return {**manifest, "path": str(path), "existing": False}


def verify_release_identity(
    *,
    path: str | Path | None = None,
    expected_model_version: str = MODEL_VERSION,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
    truth_paths: Mapping[str, str | Path] | None = None,
    expected_lineage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed unless the exact active-version identity and lineage verify."""
    artifacts_dir = Path(artifacts_dir)
    try:
        current_lineage = dict(
            expected_lineage or current_model_lineage(artifacts_dir=artifacts_dir)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"cannot resolve current model lineage: {exc}",
            "expected_model_version": expected_model_version,
            "fallback_attempted": False,
        }
    evidence_bundle_id = str(current_lineage.get("evidence_bundle_id") or "")
    try:
        path = Path(
            path
            or release_identity_path(
                expected_model_version,
                evidence_bundle_id,
                artifacts_dir=artifacts_dir,
            )
        )
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "expected_model_version": expected_model_version,
            "fallback_attempted": False,
        }
    if not path.exists():
        return {
            "ok": False,
            "error": f"missing release identity {path}",
            "expected_model_version": expected_model_version,
            "fallback_attempted": False,
        }
    try:
        sealed = _load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"invalid release identity {path}: {exc}"}

    problems = validate_release_identity_document(
        sealed,
        expected_model_version=expected_model_version,
        expected_evidence_bundle_id=evidence_bundle_id,
    )
    if sealed.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
        problems.append(f"identity_schema_version must be {IDENTITY_SCHEMA_VERSION}")
    expected = sealed.get("data_hashes") or {}
    modes = sealed.get("data_hash_modes") or {}
    required = set(_truth_paths(truth_paths))
    if set(expected) != required:
        problems.append("release identity truth input set is incomplete or unexpected")
    if set(modes) != required:
        problems.append(
            "release identity truth hash modes are incomplete or unexpected"
        )
    try:
        actual_fp = current_truth_fingerprints(
            paths=truth_paths,
            hash_modes={name: str(modes.get(name) or "") for name in expected},
        )
    except ValueError as exc:
        actual_fp = {}
        problems.append(str(exc))
    actual = {name: item["sha256"] for name, item in actual_fp.items()}
    mismatches: dict[str, dict[str, str]] = {}
    for name in sorted(required | set(expected)):
        exp = expected.get(name)
        got = actual.get(name)
        if got != exp:
            mismatches[name] = {"expected": str(exp or ""), "actual": str(got or "")}

    sealed_lineage = sealed.get("lineage") or {}
    lineage_mismatches: dict[str, dict[str, Any]] = {}
    for name in sorted(set(sealed_lineage) | set(current_lineage)):
        exp = sealed_lineage.get(name)
        got = current_lineage.get(name)
        if exp != got:
            lineage_mismatches[name] = {"expected": exp, "actual": got}
    if not sealed_lineage:
        problems.append("release identity has no validated model lineage")

    ok = not problems and not mismatches and not lineage_mismatches
    return {
        "ok": ok,
        "path": str(path),
        "release_id": sealed.get("release_id"),
        "identity_schema_version": sealed.get("identity_schema_version"),
        "expected_model_version": expected_model_version,
        "model_version": sealed.get("model_version"),
        "problems": problems,
        "mismatches": mismatches,
        "lineage_mismatches": lineage_mismatches,
        "PUBLIC_LIVE_ENABLED": sealed.get("PUBLIC_LIVE_ENABLED"),
        "publication_surface": sealed.get("publication_surface"),
        "live_locked": sealed.get("PUBLIC_LIVE_ENABLED") is False,
        "promotion_blocked": (not ok) or bool(sealed.get("PUBLIC_LIVE_ENABLED")),
        "fallback_attempted": False,
    }
