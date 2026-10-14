"""Deterministic storage for research JSON blobs that exceed GitHub limits.

The statistical artifacts remain ordinary JSON at runtime.  Only their Git
representation is gzip-compressed.  The manifest binds the compressed bytes
to the exact runtime bytes so hydration cannot alter prediction lineage.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "large-research-artifact-storage-v1"
DEFAULT_MANIFEST = Path("data/artifacts/large_artifact_storage.json")
DEFAULT_PATHS = (
    Path("data/artifacts/nested_component_loo_selection.json"),
    Path("data/artifacts/nested_component_loo_canonical.json"),
)


class LargeArtifactError(ValueError):
    """Raised when a stored research artifact fails byte-level verification."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _compressed_path(path: Path) -> Path:
    return path.with_suffix(f"{path.suffix}.gz")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def pack_large_artifacts(
    *,
    paths: tuple[Path, ...] = DEFAULT_PATHS,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    """Compress exact JSON bytes, remove runtime copies, and write their seal."""
    entries: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"large research artifact is missing: {path}")
        raw = path.read_bytes()
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        compressed_path = _compressed_path(path)
        _atomic_write(compressed_path, compressed)
        entries.append(
            {
                "path": path.as_posix(),
                "compressed_path": compressed_path.as_posix(),
                "raw_bytes": len(raw),
                "raw_sha256": _sha256(raw),
                "compressed_bytes": len(compressed),
                "compressed_sha256": _sha256(compressed),
                "compression": "gzip-level-9-mtime-0",
            }
        )
        path.unlink()

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "entries": entries,
    }
    _atomic_write(
        manifest_path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return payload


def hydrate_large_artifacts(
    *, manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    """Restore and verify exact runtime JSON bytes from versioned gzip blobs."""
    if not manifest_path.is_file():
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "manifest_present": False,
            "hydrated": [],
        }
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise LargeArtifactError("large artifact storage schema changed")

    hydrated: list[str] = []
    for entry in payload.get("entries") or []:
        path = Path(str(entry.get("path") or ""))
        compressed_path = Path(str(entry.get("compressed_path") or ""))
        if not compressed_path.is_file():
            raise FileNotFoundError(f"compressed research artifact is missing: {compressed_path}")
        compressed = compressed_path.read_bytes()
        if _sha256(compressed) != entry.get("compressed_sha256"):
            raise LargeArtifactError(f"compressed research artifact changed: {compressed_path}")
        try:
            raw = gzip.decompress(compressed)
        except gzip.BadGzipFile as exc:
            raise LargeArtifactError(
                f"compressed research artifact is invalid: {compressed_path}"
            ) from exc
        if len(raw) != int(entry.get("raw_bytes") or -1):
            raise LargeArtifactError(f"research artifact size changed: {path}")
        if _sha256(raw) != entry.get("raw_sha256"):
            raise LargeArtifactError(f"research artifact content changed: {path}")
        if path.is_file():
            if path.read_bytes() != raw:
                raise LargeArtifactError(
                    f"existing hydrated research artifact differs from its seal: {path}"
                )
        else:
            _atomic_write(path, raw)
            hydrated.append(path.as_posix())
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "manifest_present": True,
        "hydrated": hydrated,
        "entries": len(payload.get("entries") or []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pack or hydrate byte-sealed large research JSON artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("hydrate", help="Restore exact runtime JSON files.")
    subparsers.add_parser("pack", help="Compress exact runtime JSON files for Git.")
    args = parser.parse_args(argv)
    report = (
        pack_large_artifacts()
        if args.command == "pack"
        else hydrate_large_artifacts()
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by workflows
    raise SystemExit(main())
