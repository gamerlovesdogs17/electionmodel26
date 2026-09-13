"""Environment lock + snapshot freeze for byte-stable rebuilds."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, ROOT, ARTIFACTS_DIR


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


def verify_rebuild(*, run_id: str | None = None) -> dict[str, Any]:
    """
    Compare current forecast_latest.json + draws hashes to a sealed release / manifest.

    Blueprint §11 byte-identical rebuild gate (lite): fail if hashes diverge.
    """
    from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR

    art_path = ARTIFACTS_DIR / "forecast_latest.json"
    if not art_path.exists():
        return {"ok": False, "error": "missing forecast_latest.json"}
    art = json.loads(art_path.read_text(encoding="utf-8"))
    rid = run_id or art.get("run_id")
    man_path = MANIFESTS_DIR / f"run_{rid}.json"
    if not man_path.exists():
        return {"ok": False, "error": f"missing run manifest {man_path}", "run_id": rid}
    man = json.loads(man_path.read_text(encoding="utf-8"))
    expected = (man.get("output_hashes") or {}).get("forecast_json")
    actual = hashlib.sha256(art_path.read_bytes()).hexdigest()
    ok = bool(expected) and expected == actual
    draws_ok = None
    draws_path = Path(str((man.get("paths") or {}).get("draws") or ""))
    if draws_path.exists() and (man.get("output_hashes") or {}).get("draws"):
        draws_ok = hashlib.sha256(draws_path.read_bytes()).hexdigest() == man["output_hashes"]["draws"]
        ok = ok and bool(draws_ok)
    return {
        "ok": ok,
        "run_id": rid,
        "forecast_hash_expected": expected,
        "forecast_hash_actual": actual,
        "draws_ok": draws_ok,
        "manifest": str(man_path),
    }


def write_environment_lock() -> Path:
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFESTS_DIR / "environment_lock.json"
    path.write_text(json.dumps(environment_lock(), indent=2))
    return path
