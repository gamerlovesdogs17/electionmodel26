"""Operational health checks + append-only release index (blueprint §11.3)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, MODEL_VERSION


def _load_latest_artifact() -> dict[str, Any]:
    path = ARTIFACTS_DIR / "forecast_latest.json"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}")
    return json.loads(path.read_text())


def monitor_check(*, min_polls: int = 50, min_enop: float = 5.0) -> dict[str, Any]:
    """
    Fail-closed health checks for the latest forecast artifact / run manifest.
    Exit-friendly: `ok` False when any check fails.
    """
    checks: list[dict[str, Any]] = []
    art = _load_latest_artifact()
    chamber = art.get("chamber") or {}
    races = art.get("races") or []
    snap = art.get("snapshot") or {}
    diag = art.get("diagnostics") or {}

    def add(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("has_races", len(races) >= 30, len(races))
    add("model_version_present", bool(art.get("model_version")), art.get("model_version"))
    p_dem = float(chamber.get("p_dem_majority") or 0)
    p_rep = float(chamber.get("p_rep_majority") or 0)
    add("control_probs_sum_to_one", abs(p_dem + p_rep - 1.0) < 1e-3, {"p_dem": p_dem, "p_rep": p_rep})
    exp = float(chamber.get("expected_dem_seats") or -1)
    add("expected_seats_in_range", 0.0 <= exp <= 100.0, exp)
    n_polls = int(snap.get("n_polls") or diag.get("n_polls") or 0)
    add("min_polls", n_polls >= min_polls, n_polls)
    enop = float(diag.get("enop_global") or 0.0)
    add("min_enop", enop >= min_enop or n_polls == 0, enop)
    # OH/FL specials present for 2026
    if art.get("election_id") == "senate-2026":
        states = {r.get("state") for r in races}
        add("has_oh_fl_specials", {"OH", "FL"}.issubset(states), sorted(states & {"OH", "FL"}))
    # Rating consistency
    from midterms.model.overlays import rating_from_probability

    bad = [
        r["race_id"]
        for r in races
        if r.get("rating") != rating_from_probability(float(r.get("p_dem") or 0.5))
    ]
    add("ratings_match_p_dem", len(bad) == 0, bad[:5])

    # Manifest hash if present
    run_id = art.get("run_id")
    man_path = MANIFESTS_DIR / f"run_{run_id}.json" if run_id else None
    if man_path and man_path.exists():
        man = json.loads(man_path.read_text())
        add("run_manifest_present", True, str(man_path))
        add("manifest_model_version", man.get("model_version") == art.get("model_version"), man.get("model_version"))
    else:
        add("run_manifest_present", False, str(man_path))

    ok = all(c["ok"] for c in checks)
    report = {
        "ok": ok,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model_version": art.get("model_version") or MODEL_VERSION,
        "run_id": run_id,
        "checks": checks,
    }
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (MANIFESTS_DIR / "monitor_latest.json").write_text(json.dumps(report, indent=2))
    return report


def append_release_index(manifest: dict[str, Any]) -> Path:
    """Append one JSON line to the immutable release index."""
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFESTS_DIR / "releases.jsonl"
    line = {
        "run_id": manifest.get("run_id"),
        "generated_at": manifest.get("generated_at"),
        "forecast_as_of": manifest.get("forecast_as_of"),
        "model_version": manifest.get("model_version"),
        "code_commit": manifest.get("code_commit"),
        "configuration_hash": manifest.get("configuration_hash"),
        "output_hashes": manifest.get("output_hashes"),
        "paths": manifest.get("paths"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
    return path


def archive_release(manifest: dict[str, Any], artifact_text: str) -> dict[str, Any]:
    """
    Copy forecast JSON into data/releases/{run_id}/ with a sha256 sidecar.
    Signing can be layered later; content-addressed hash is the integrity anchor.
    """
    import hashlib
    import shutil

    run_id = str(manifest.get("run_id") or "unknown")
    releases = Path(__file__).resolve().parents[2] / "data" / "releases" / run_id
    releases.mkdir(parents=True, exist_ok=True)
    forecast_path = releases / "forecast.json"
    forecast_path.write_text(artifact_text)
    digest = hashlib.sha256(artifact_text.encode()).hexdigest()
    (releases / "forecast.sha256").write_text(digest + "\n")
    man_path = releases / "run_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2, default=str))
    # Optional draws copy
    draws = Path(str((manifest.get("paths") or {}).get("draws") or ""))
    if draws.exists():
        shutil.copy2(draws, releases / draws.name)
    return {"release_dir": str(releases), "sha256": digest}
