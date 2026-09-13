"""Operational health checks + append-only release index (blueprint §11.3)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, MODEL_VERSION, NORMALIZED_DIR


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
    alerts: list[str] = []
    art = _load_latest_artifact()
    chamber = art.get("chamber") or {}
    races = art.get("races") or []
    snap = art.get("snapshot") or {}
    diag = art.get("diagnostics") or {}

    def add(name: str, ok: bool, detail: Any = None, alert: str | None = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok and alert:
            alerts.append(alert)

    add("has_races", len(races) >= 30, len(races), "too few races in artifact")
    add("model_version_present", bool(art.get("model_version")), art.get("model_version"))
    p_dem = float(chamber.get("p_dem_majority") or 0)
    p_rep = float(chamber.get("p_rep_majority") or 0)
    add(
        "control_probs_sum_to_one",
        abs(p_dem + p_rep - 1.0) < 1e-3,
        {"p_dem": p_dem, "p_rep": p_rep},
        "control probabilities do not sum to 1",
    )
    exp = float(chamber.get("expected_dem_seats") or -1)
    add("expected_seats_in_range", 0.0 <= exp <= 100.0, exp)
    n_polls = int(snap.get("n_polls") or diag.get("n_polls") or 0)
    add("min_polls", n_polls >= min_polls, n_polls, f"stale/low polls n={n_polls}")
    enop = float(diag.get("enop_global") or 0.0)
    add("min_enop", enop >= min_enop or n_polls == 0, enop, f"low ENOP={enop}")
    if art.get("election_id") == "senate-2026":
        states = {r.get("state") for r in races}
        add("has_oh_fl_specials", {"OH", "FL"}.issubset(states), sorted(states & {"OH", "FL"}))
    from midterms.model.overlays import rating_from_probability

    bad = [
        r["race_id"]
        for r in races
        if r.get("rating") != rating_from_probability(float(r.get("p_dem") or 0.5))
    ]
    add("ratings_match_p_dem", len(bad) == 0, bad[:5], "rating/probability inconsistency")

    # Duplicate race IDs
    ids = [r.get("race_id") for r in races]
    dup = sorted({i for i in ids if ids.count(i) > 1 and i})
    add("no_duplicate_race_ids", len(dup) == 0, dup[:5], "duplicate race_ids")

    # Soft informational checks (do not fail the gate alone)
    soft = []
    soft.append({"name": "has_scenarios", "ok": bool(art.get("scenarios")), "detail": bool(art.get("scenarios"))})
    soft.append({"name": "has_auxiliary_layers", "ok": bool(art.get("auxiliary")), "detail": bool(art.get("auxiliary"))})
    if not art.get("scenarios"):
        alerts.append("scenarios block missing (regenerate forecast on v0.8+)")

    method = str(art.get("method") or "")
    core = str(diag.get("spine_method") or diag.get("core_method") or "")
    prod_ok = method.startswith("pymc") or (
        method.startswith("ensemble_stack")
        and (core.startswith("pymc") or core == "" or core.startswith("ensemble"))
        and not core.startswith("fast")
        and not core.startswith("degraded")
    )
    add(
        "production_method",
        prod_ok,
        {"method": method, "core_method": core},
        f"non-production method in artifact: method={method} core={core}",
    )
    if method.startswith("degraded") or method.startswith("fast") or core.startswith("fast"):
        alerts.append(f"non-production method in artifact: method={method} core={core}")

    null_m = [
        r.get("race_id")
        for r in races
        if r.get("mean_margin") is None or r.get("sd_margin") is None
    ]
    add("finite_race_margins", len(null_m) == 0, null_m[:5], "null mean/sd margins in races")

    if art.get("generic_ballot") is None and art.get("snapshot", {}).get("generic_ballot") is None:
        soft.append({"name": "generic_ballot_recorded", "ok": False, "detail": None})
        alerts.append("generic_ballot missing from artifact")
    else:
        soft.append(
            {
                "name": "generic_ballot_recorded",
                "ok": True,
                "detail": art.get("generic_ballot")
                if art.get("generic_ballot") is not None
                else art.get("snapshot", {}).get("generic_ballot"),
            }
        )

    try:
        from midterms.validation.peer_gate import score_against_peers

        peer_gate = score_against_peers(art)
        soft.append(
            {
                "name": "peer_brier_crps_gate",
                "ok": bool(peer_gate.get("ok")),
                "detail": {
                    "max_abs_control_gap": peer_gate.get("max_abs_control_gap"),
                    "race_scores": peer_gate.get("race_scores"),
                    "reasons": peer_gate.get("reasons"),
                },
            }
        )
        if not peer_gate.get("ok"):
            alerts.append("peer release gate failed: " + "; ".join(peer_gate.get("reasons") or []))
            # Fail closed on peer gate for published artifacts
            add(
                "peer_release_gate",
                False,
                peer_gate.get("reasons"),
                "peer Brier/CRPS / control-gap release gate failed",
            )
        else:
            add("peer_release_gate", True, peer_gate.get("max_abs_control_gap"))
    except Exception as exc:  # noqa: BLE001
        soft.append({"name": "peer_brier_crps_gate", "ok": False, "detail": str(exc)})
        alerts.append(f"peer gate error: {exc}")

    if art.get("warnings"):
        alerts.append(f"layer_warnings={len(art['warnings'])}")
        soft.append({"name": "layer_warnings", "ok": False, "detail": art["warnings"][:5]})

    stack_path = ARTIFACTS_DIR / "cycle_replay_all.json"
    if stack_path.exists():
        age_h = (datetime.now(timezone.utc).timestamp() - stack_path.stat().st_mtime) / 3600.0
        soft.append({"name": "stack_weights_fresh", "ok": age_h < 24 * 30, "detail": {"age_hours": age_h}})
        if age_h >= 24 * 30:
            alerts.append("stack weights artifact older than 30 days — rerun replay-cycle --all")

    hist_man = MANIFESTS_DIR / "historical_polls.json"
    if hist_man.exists():
        try:
            hm = json.loads(hist_man.read_text())
            soft.append(
                {
                    "name": "historical_polls_primary",
                    "ok": hm.get("primary_source") == "fte",
                    "detail": hm.get("primary_source"),
                }
            )
            if hm.get("primary_source") != "fte":
                alerts.append("historical polls not FTE-primary — run ingest-fte-polls")
        except (json.JSONDecodeError, OSError):
            pass

    # Source freshness: polls parquet mtime vs artifact
    polls_path = NORMALIZED_DIR / "polls.parquet"
    if polls_path.exists():
        age_hours = (datetime.now().timestamp() - polls_path.stat().st_mtime) / 3600.0
        add("polls_file_present", True, {"age_hours": round(age_hours, 2)})
        if age_hours > 24 * 14:
            alerts.append(f"polls parquet age {age_hours:.0f}h (>14d)")
    else:
        add("polls_file_present", False, None, "missing polls.parquet")

    run_id = art.get("run_id")
    man_path = MANIFESTS_DIR / f"run_{run_id}.json" if run_id else None
    if man_path and man_path.exists():
        man = json.loads(man_path.read_text())
        add("run_manifest_present", True, str(man_path))
        add(
            "manifest_model_version",
            man.get("model_version") == art.get("model_version"),
            man.get("model_version"),
        )
        soft.append(
            {
                "name": "has_environment_lock",
                "ok": bool(man.get("environment_lock")),
                "detail": bool(man.get("environment_lock")),
            }
        )
        soft.append(
            {"name": "has_signature", "ok": bool(man.get("signature")), "detail": bool(man.get("signature"))}
        )
    else:
        add("run_manifest_present", False, str(man_path), "missing run manifest")

    ok = all(c["ok"] for c in checks)
    report = {
        "ok": ok,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model_version": art.get("model_version") or MODEL_VERSION,
        "run_id": run_id,
        "checks": checks,
        "soft_checks": soft,
        "alerts": alerts,
    }
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (MANIFESTS_DIR / "monitor_latest.json").write_text(json.dumps(report, indent=2))
    return report


def append_release_index(manifest: dict[str, Any]) -> Path:
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
        "signature": manifest.get("signature"),
        "paths": manifest.get("paths"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
    return path


def archive_release(manifest: dict[str, Any], artifact_text: str) -> dict[str, Any]:
    import hashlib
    import shutil

    from midterms.ops.signing import write_signature_sidecar

    run_id = str(manifest.get("run_id") or "unknown")
    releases = Path(__file__).resolve().parents[2] / "data" / "releases" / run_id
    releases.mkdir(parents=True, exist_ok=True)
    forecast_path = releases / "forecast.json"
    forecast_path.write_text(artifact_text)
    digest = hashlib.sha256(artifact_text.encode()).hexdigest()
    (releases / "forecast.sha256").write_text(digest + "\n")
    write_signature_sidecar(forecast_path, artifact_text)
    man_path = releases / "run_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2, default=str))
    write_signature_sidecar(man_path, man_path.read_text())
    draws = Path(str((manifest.get("paths") or {}).get("draws") or ""))
    if draws.exists():
        shutil.copy2(draws, releases / draws.name)
    return {"release_dir": str(releases), "sha256": digest}
