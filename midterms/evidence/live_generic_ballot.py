"""Canonical live 2026 generic-ballot input contract.

Exactly one VoteHub file supplies the live national GB number used by the
forecast, and the same file's hash must be recorded in source/model lineage.
The unscoped ``votehub_generic_ballot.json`` fetch is a broader archive and
must not silently become the live production input.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, RAW_DIR, ROOT

LIVE_GENERIC_BALLOT_FILENAME = "votehub_generic_ballot_2026.json"
LIVE_GENERIC_BALLOT_PATH = RAW_DIR / "external" / LIVE_GENERIC_BALLOT_FILENAME
LIVE_GENERIC_BALLOT_FETCH_MANIFEST = MANIFESTS_DIR / "fetch_votehub_generic_ballot_2026.json"
ARCHIVE_GENERIC_BALLOT_FILENAME = "votehub_generic_ballot.json"


def live_generic_ballot_path() -> Path:
    return LIVE_GENERIC_BALLOT_PATH


def live_generic_ballot_lineage() -> dict[str, Any]:
    path = LIVE_GENERIC_BALLOT_PATH
    manifest = LIVE_GENERIC_BALLOT_FETCH_MANIFEST
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    manifest_sha = None
    manifest_recorded = None
    if manifest.is_file():
        import json

        payload = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        manifest_recorded = payload.get("sha256") or (payload.get("receipt") or {}).get("sha256")
    rel = path.resolve().relative_to(ROOT.resolve()).as_posix() if path.is_file() else str(path)
    return {
        "canonical_filename": LIVE_GENERIC_BALLOT_FILENAME,
        "path": rel,
        "file_sha256": file_sha,
        "fetch_manifest": (
            manifest.resolve().relative_to(ROOT.resolve()).as_posix()
            if manifest.is_file()
            else str(manifest)
        ),
        "fetch_manifest_sha256": manifest_sha,
        "fetch_manifest_recorded_raw_sha256": manifest_recorded,
        "file_matches_fetch_receipt": bool(
            file_sha and manifest_recorded and file_sha == manifest_recorded
        ),
        "archive_filename_not_used_for_live": ARCHIVE_GENERIC_BALLOT_FILENAME,
    }


def assert_live_generic_ballot_lineage_coherent() -> dict[str, Any]:
    lineage = live_generic_ballot_lineage()
    if not lineage["file_sha256"]:
        raise ValueError(f"canonical live GB file missing: {LIVE_GENERIC_BALLOT_FILENAME}")
    if not lineage["file_matches_fetch_receipt"]:
        raise ValueError(
            "live GB file hash does not match fetch receipt: "
            f"file={lineage['file_sha256']} receipt={lineage['fetch_manifest_recorded_raw_sha256']}"
        )
    return lineage
