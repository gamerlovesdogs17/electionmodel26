"""Environment lock + snapshot freeze for byte-stable rebuilds."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, ROOT


def environment_lock() -> dict[str, Any]:
    pkgs = {}
    try:
        import importlib.metadata as md

        for name in ("numpy", "pandas", "scipy", "pymc", "pyarrow"):
            try:
                pkgs[name] = md.version(name)
            except md.PackageNotFoundError:
                pkgs[name] = None
    except Exception:  # noqa: BLE001
        pass
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "packages": pkgs,
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }


def snapshot_domain_hashes() -> dict[str, str]:
    """Hash normalized domain parquet/json files for the run manifest."""
    out = {}
    if not NORMALIZED_DIR.exists():
        return out
    for path in sorted(NORMALIZED_DIR.glob("*")):
        if path.suffix.lower() not in {".parquet", ".json", ".csv"}:
            continue
        out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def write_environment_lock() -> Path:
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFESTS_DIR / "environment_lock.json"
    path.write_text(json.dumps(environment_lock(), indent=2))
    return path
