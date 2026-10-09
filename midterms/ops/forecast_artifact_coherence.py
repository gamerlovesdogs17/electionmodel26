"""Sealed forecast artifacts are immutable; latest must be version-coherent."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MODEL_VERSION,
    PREVIOUS_SEALED_MODEL_VERSION,
    ROOT,
)

SEALED_V0923_NAME = "forecast_sealed_v0923_publication.json"
DEV_STUB_STATUS = "forecast_not_rebuilt_after_spec_change"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_sealed_publication_preserved(
    *,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    """Copy mixed/old forecast_latest into an immutable sealed name once."""
    art = artifacts_dir or ARTIFACTS_DIR
    latest = art / "forecast_latest.json"
    sealed = art / SEALED_V0923_NAME
    def _rel(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(ROOT.resolve()))
        except ValueError:
            return path.as_posix()

    if sealed.is_file():
        payload = json.loads(sealed.read_text(encoding="utf-8"))
        return {
            "sealed_path": _rel(sealed),
            "already_present": True,
            "model_version": payload.get("model_version"),
            "sha256": _sha256_file(sealed),
        }
    if not latest.is_file():
        return {"sealed_path": None, "already_present": False, "error": "no_forecast_latest"}
    payload = json.loads(latest.read_text(encoding="utf-8"))
    if payload.get("model_version") != PREVIOUS_SEALED_MODEL_VERSION:
        # Still preserve whatever publication object exists before stubbing.
        pass
    shutil.copy2(latest, sealed)
    return {
        "sealed_path": _rel(sealed),
        "already_present": False,
        "model_version": payload.get("model_version"),
        "sha256": _sha256_file(sealed),
    }


def write_development_forecast_stub(
    *,
    artifacts_dir: Path | None = None,
    also_web_public: bool = True,
) -> dict[str, Any]:
    """Replace forecast_latest with a coherent non-probability stub for current spec."""
    art = artifacts_dir or ARTIFACTS_DIR
    sealed_info = ensure_sealed_publication_preserved(artifacts_dir=art)
    stub = {
        "schema_version": "forecast-development-stub-v1",
        "status": DEV_STUB_STATUS,
        "model_version": MODEL_VERSION,
        "publishable": False,
        "run_class": "development",
        "races": [],
        "sealed_publication_artifact": sealed_info.get("sealed_path"),
        "sealed_publication_model_version": PREVIOUS_SEALED_MODEL_VERSION,
        "note": (
            "Probability-affecting specification changed after the sealed "
            f"{PREVIOUS_SEALED_MODEL_VERSION} publication. Old race probabilities "
            "are retained only under the sealed artifact name and must not be "
            "read as outputs of the current model version."
        ),
    }
    latest = art / "forecast_latest.json"
    latest.write_text(json.dumps(stub, indent=2) + "\n", encoding="utf-8")
    if also_web_public:
        web = ROOT / "web" / "public" / "data" / "forecast_latest.json"
        if web.parent.is_dir():
            web.write_text(json.dumps(stub, indent=2) + "\n", encoding="utf-8")
    return stub


def assert_forecast_latest_coherent(
    *,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    art = artifacts_dir or ARTIFACTS_DIR
    latest = art / "forecast_latest.json"
    if not latest.is_file():
        raise ValueError("forecast_latest.json missing")
    payload = json.loads(latest.read_text(encoding="utf-8"))
    problems = []
    if payload.get("model_version") != MODEL_VERSION:
        problems.append("model_version mismatch vs config")
    if payload.get("status") == DEV_STUB_STATUS:
        if payload.get("publishable") is True:
            problems.append("stub must not be publishable")
        if payload.get("races"):
            problems.append("stub must not carry race probability rows")
    else:
        # Full forecast must be single-version coherent.
        if payload.get("model_version") != MODEL_VERSION:
            problems.append("full forecast model_version stale")
    if problems:
        raise ValueError("; ".join(problems))
    return {"ok": True, "status": payload.get("status"), "model_version": payload.get("model_version")}
