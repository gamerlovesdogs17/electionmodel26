from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from midterms.ops.large_artifacts import (
    LargeArtifactError,
    hydrate_large_artifacts,
    pack_large_artifacts,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_large_artifact_pack_hydrate_preserves_exact_bytes(tmp_path: Path):
    first = tmp_path / "selection.json"
    second = tmp_path / "canonical.json"
    first.write_bytes(b'{"draws":[1.0,NaN,-2.5]}\n')
    second.write_bytes(b'{"draws":[3.25,4.5]}')
    originals = {first: _sha(first), second: _sha(second)}
    manifest = tmp_path / "storage.json"

    packed = pack_large_artifacts(paths=(first, second), manifest_path=manifest)
    compressed_hashes = {
        entry["path"]: entry["compressed_sha256"] for entry in packed["entries"]
    }

    assert not first.exists() and not second.exists()
    hydrated = hydrate_large_artifacts(manifest_path=manifest)
    assert hydrated["hydrated"] == [first.as_posix(), second.as_posix()]
    assert {_sha(path) for path in originals} == set(originals.values())

    repacked = pack_large_artifacts(paths=(first, second), manifest_path=manifest)
    assert {
        entry["path"]: entry["compressed_sha256"] for entry in repacked["entries"]
    } == compressed_hashes


def test_hydration_rejects_compressed_mutation(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"value":1}', encoding="utf-8")
    manifest = tmp_path / "storage.json"
    payload = pack_large_artifacts(paths=(artifact,), manifest_path=manifest)
    compressed = Path(payload["entries"][0]["compressed_path"])
    compressed.write_bytes(compressed.read_bytes() + b"tamper")

    with pytest.raises(LargeArtifactError, match="compressed research artifact changed"):
        hydrate_large_artifacts(manifest_path=manifest)


def test_hydration_rejects_conflicting_runtime_copy(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"value":1}', encoding="utf-8")
    manifest = tmp_path / "storage.json"
    pack_large_artifacts(paths=(artifact,), manifest_path=manifest)
    artifact.write_text('{"value":2}', encoding="utf-8")

    with pytest.raises(LargeArtifactError, match="differs from its seal"):
        hydrate_large_artifacts(manifest_path=manifest)
