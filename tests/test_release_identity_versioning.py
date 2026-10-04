"""Current-version release identity resolution and immutable history."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from midterms.config import ARTIFACTS_DIR
from midterms.ops.release_identity import (
    IDENTITY_SCHEMA_VERSION,
    LEGACY_V0921_RELEASE_IDENTITY_PATH,
    release_identity_path,
    validate_release_identity_document,
    verify_release_identity,
    write_release_identity,
)

V0921_GIT_BLOB_ID = "5ea7d4e99b3caba51170ba7f2bf6b7f0fe991bdd"
V0922 = "senate-hierarchical-v0.9.22"
TEST_BUNDLE_ID = "eb-aaaaaaaaaaaaaaaa"


def _truth_files(root: Path) -> dict[str, Path]:
    paths = {
        "official_senate_ledger.json": root / "ledger.json",
        "independent_chamber_expectations.json": root / "expectations.json",
        "fte_senate.csv": root / "fte.csv",
    }
    paths["official_senate_ledger.json"].write_text(
        '{"cycles":{"2024":{"value":1}}}\n', encoding="utf-8"
    )
    paths["independent_chamber_expectations.json"].write_text(
        '{"cycles":{"2024":{"seats":100}}}\n', encoding="utf-8"
    )
    paths["fte_senate.csv"].write_bytes(b"year,state,votes\n2024,ZZ,10\n")
    return paths


def _lineage(version: str = V0922) -> dict[str, object]:
    return {
        "model_version": version,
        "validated_model_spec_sha256": "a" * 64,
        "evidence_bundle_id": TEST_BUNDLE_ID,
        "evidence_bundle_sha256": "b" * 64,
        "selected_poll_structure_id": "psc-test",
        "canonical_oof_sha256": "c" * 64,
        "stack_weights_sha256": "d" * 64,
        "uncertainty_calibration_sha256": "e" * 64,
        "validated_code_commit": "f" * 40,
    }


def test_historical_v0921_identity_is_unchanged_and_well_formed() -> None:
    # Git's blob ID is stable across checkout newline conventions.
    blob = (
        subprocess.check_output(
            ["git", "hash-object", str(LEGACY_V0921_RELEASE_IDENTITY_PATH)]
        )
        .decode()
        .strip()
    )
    assert blob == V0921_GIT_BLOB_ID
    payload = json.loads(LEGACY_V0921_RELEASE_IDENTITY_PATH.read_text(encoding="utf-8"))
    assert (
        validate_release_identity_document(
            payload, expected_model_version="senate-hierarchical-v0.9.21"
        )
        == []
    )


def test_resolver_is_version_derived_and_has_no_older_fallback(tmp_path: Path) -> None:
    assert release_identity_path(
        V0922, TEST_BUNDLE_ID, artifacts_dir=tmp_path
    ).name == ("release_identity_v0922_eb-aaaaaaaaaaaaaaaa.json")
    assert (
        release_identity_path(
            "senate-hierarchical-v0.9.23",
            TEST_BUNDLE_ID,
            artifacts_dir=tmp_path,
        ).name
        == "release_identity_v0923_eb-aaaaaaaaaaaaaaaa.json"
    )
    (tmp_path / "release_identity_v0921.json").write_text("{}", encoding="utf-8")
    (tmp_path / "release_identity_v0922_eb-bbbbbbbbbbbbbbbb.json").write_text(
        "{}", encoding="utf-8"
    )
    report = verify_release_identity(
        expected_model_version=V0922,
        artifacts_dir=tmp_path,
        truth_paths=_truth_files(tmp_path),
        expected_lineage=_lineage(),
    )
    assert report["ok"] is False
    assert "release_identity_v0922_eb-aaaaaaaaaaaaaaaa.json" in report["error"]
    assert report["fallback_attempted"] is False


def test_current_identity_round_trip_is_semantic_and_deterministic(
    tmp_path: Path,
) -> None:
    truth = _truth_files(tmp_path)
    lineage = _lineage()
    path = release_identity_path(V0922, TEST_BUNDLE_ID, artifacts_dir=tmp_path)
    written = write_release_identity(
        out_path=path,
        model_version=V0922,
        artifacts_dir=tmp_path,
        truth_paths=truth,
        lineage=lineage,
        seal_source_commit="1" * 40,
    )
    assert written["identity_schema_version"] == IDENTITY_SCHEMA_VERSION
    assert written["release_id"] == "truth_v1_v0.9.22_eb-aaaaaaaaaaaaaaaa"
    report = verify_release_identity(
        path=path,
        expected_model_version=V0922,
        artifacts_dir=tmp_path,
        truth_paths=truth,
        expected_lineage=lineage,
    )
    assert report["ok"] is True, report
    assert report["lineage"] == lineage

    # JSON ordering and whitespace are not changes to the sealed evidence.
    payload = json.loads(truth["official_senate_ledger.json"].read_text())
    truth["official_senate_ledger.json"].write_text(
        json.dumps(payload, sort_keys=True, indent=4), encoding="utf-8"
    )
    assert (
        verify_release_identity(
            path=path,
            expected_model_version=V0922,
            artifacts_dir=tmp_path,
            truth_paths=truth,
            expected_lineage=lineage,
        )["ok"]
        is True
    )


def test_material_truth_or_lineage_change_fails_closed(tmp_path: Path) -> None:
    truth = _truth_files(tmp_path)
    lineage = _lineage()
    path = release_identity_path(V0922, TEST_BUNDLE_ID, artifacts_dir=tmp_path)
    write_release_identity(
        out_path=path,
        model_version=V0922,
        artifacts_dir=tmp_path,
        truth_paths=truth,
        lineage=lineage,
    )
    truth["official_senate_ledger.json"].write_text(
        '{"cycles":{"2024":{"value":2}}}', encoding="utf-8"
    )
    changed = verify_release_identity(
        path=path,
        expected_model_version=V0922,
        artifacts_dir=tmp_path,
        truth_paths=truth,
        expected_lineage=lineage,
    )
    assert changed["ok"] is False
    assert "official_senate_ledger.json" in changed["mismatches"]

    truth = _truth_files(tmp_path)
    changed_lineage = dict(lineage)
    changed_lineage["stack_weights_sha256"] = "9" * 64
    stale = verify_release_identity(
        path=path,
        expected_model_version=V0922,
        artifacts_dir=tmp_path,
        truth_paths=truth,
        expected_lineage=changed_lineage,
    )
    assert stale["ok"] is False
    assert "stack_weights_sha256" in stale["lineage_mismatches"]


def test_active_version_mismatch_fails_loudly(tmp_path: Path) -> None:
    truth = _truth_files(tmp_path)
    path = release_identity_path(V0922, TEST_BUNDLE_ID, artifacts_dir=tmp_path)
    write_release_identity(
        out_path=path,
        model_version=V0922,
        artifacts_dir=tmp_path,
        truth_paths=truth,
        lineage=_lineage(),
    )
    report = verify_release_identity(
        path=path,
        expected_model_version="senate-hierarchical-v0.9.23",
        artifacts_dir=tmp_path,
        truth_paths=truth,
        expected_lineage=_lineage("senate-hierarchical-v0.9.23"),
    )
    assert report["ok"] is False
    assert any("model_version" in item for item in report["problems"])


def test_repository_current_identity_path_is_not_historical() -> None:
    # Path construction must stay version-derived. Until the v0.9.23 rebuild
    # seals a current validated model spec, resolve the bundle id from the
    # frozen v0.9.22 release identity rather than current_model_lineage().
    sealed = json.loads(
        (
            ARTIFACTS_DIR / "release_identity_v0922_eb-3e91ca3a90628d69.json"
        ).read_text(encoding="utf-8")
    )
    bundle_id = str((sealed.get("lineage") or {}).get("evidence_bundle_id"))
    path = release_identity_path(V0922, bundle_id)
    assert path.parent == ARTIFACTS_DIR
    assert path.name == f"release_identity_v0922_{bundle_id}.json"
    assert path != LEGACY_V0921_RELEASE_IDENTITY_PATH
    current_path = release_identity_path(
        "senate-hierarchical-v0.9.23", bundle_id,
    )
    assert "v0923" in current_path.name
    assert current_path != path
