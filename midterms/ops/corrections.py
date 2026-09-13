"""Correction workflow for published forecast artifacts (blueprint §11.3)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR


def register_correction(
    *,
    original_run_id: str,
    corrected_run_id: str,
    reason: str,
    first_affected_as_of: str,
    statistical_impact: dict[str, Any] | None = None,
    prevention_test: str | None = None,
) -> dict[str, Any]:
    """
    Pair original + corrected artifacts. Preserves the original under
    data/artifacts/corrections/{original_run_id}/.
    """
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    corr_dir = ARTIFACTS_DIR / "corrections" / original_run_id
    corr_dir.mkdir(parents=True, exist_ok=True)

    src = ARTIFACTS_DIR / f"forecast_{original_run_id}.json"
    if src.exists():
        shutil.copy2(src, corr_dir / "forecast_original.json")

    record = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "original_run_id": original_run_id,
        "corrected_run_id": corrected_run_id,
        "reason": reason,
        "first_affected_as_of": first_affected_as_of,
        "statistical_impact": statistical_impact or {},
        "prevention_test": prevention_test,
        "paths": {"archive_dir": str(corr_dir)},
    }
    path = MANIFESTS_DIR / "corrections.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    (corr_dir / "correction.json").write_text(json.dumps(record, indent=2))
    return record
