"""Immutable release-identity seals for truth artifacts (v0.9.21 audit P0/P1)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MODEL_VERSION,
    PUBLIC_LIVE_ENABLED,
    RAW_DIR,
    ROOT,
)

RELEASE_IDENTITY_PATH = ARTIFACTS_DIR / "release_identity_v0921.json"
LEDGER_PATH = RAW_DIR / "external" / "official_senate_ledger.json"
EXPECTATIONS_PATH = RAW_DIR / "external" / "independent_chamber_expectations.json"
FTE_CSV = RAW_DIR / "external" / "certified" / "fte_senate.csv"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=ROOT
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def current_truth_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, path in (
        ("official_senate_ledger.json", LEDGER_PATH),
        ("independent_chamber_expectations.json", EXPECTATIONS_PATH),
        ("fte_senate.csv", FTE_CSV),
    ):
        if path.exists():
            out[key] = _sha256(path)
    return out


def write_release_identity(*, notes: list[str] | None = None) -> dict[str, Any]:
    """Write release identity *after* a clean rebuild (bind current hashes)."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "release_id": "truth_v1_v0.9.21",
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": _git_commit(),
        "PUBLIC_LIVE_ENABLED": PUBLIC_LIVE_ENABLED,
        "publication_surface": "research_only",
        "schema_version": "truth_v1",
        "data_hashes": current_truth_hashes(),
        "config": {
            "MODEL_VERSION": MODEL_VERSION,
            "PUBLIC_LIVE_ENABLED": PUBLIC_LIVE_ENABLED,
        },
        "notes": notes
        or [
            "Immutable truth-integration identity; resealed after clean rebuild.",
            "Wikipedia certified_vote_counts.json quarantined (parser_development_only).",
            "Runoff event dates + available_at day-after bound (audit 19 Sep 2026 P0).",
        ],
    }
    RELEASE_IDENTITY_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify_release_identity(*, path: Path | None = None) -> dict[str, Any]:
    """Fail closed when sealed hashes diverge from on-disk truth artifacts."""
    path = path or RELEASE_IDENTITY_PATH
    if not path.exists():
        return {"ok": False, "error": f"missing release identity {path}"}
    sealed = json.loads(path.read_text(encoding="utf-8"))
    expected = sealed.get("data_hashes") or {}
    actual = current_truth_hashes()
    mismatches: dict[str, dict[str, str]] = {}
    for key, exp in expected.items():
        got = actual.get(key)
        if got != exp:
            mismatches[key] = {"expected": exp, "actual": got or ""}
    ok = not mismatches and bool(expected)
    return {
        "ok": ok,
        "path": str(path),
        "mismatches": mismatches,
        "PUBLIC_LIVE_ENABLED": sealed.get("PUBLIC_LIVE_ENABLED"),
        "model_version": sealed.get("model_version"),
        "live_locked": sealed.get("PUBLIC_LIVE_ENABLED") is False,
        "promotion_blocked": (not ok) or bool(sealed.get("PUBLIC_LIVE_ENABLED")),
    }
