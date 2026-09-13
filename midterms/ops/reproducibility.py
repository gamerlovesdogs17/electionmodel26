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
    # Hash LF-normalized UTF-8 so Windows CRLF write_text artifacts still verify.
    actual_bytes = art_path.read_bytes().replace(b"\r\n", b"\n")
    actual = hashlib.sha256(actual_bytes).hexdigest()
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
        "newline_normalized": True,
    }


def write_environment_lock() -> Path:
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFESTS_DIR / "environment_lock.json"
    path.write_text(json.dumps(environment_lock(), indent=2))
    return path


# G10 hard tolerances (MCSE-aligned; not byte-identical JSON).
TOL_P_CONTROL = 0.01
TOL_EXPECTED_SEATS = 0.15
TOL_RACE_P_MAX = 0.02


def _load_sealed_forecast(manifest: dict[str, Any], *, release_dir: Path | None) -> dict[str, Any]:
    candidates: list[Path] = []
    if release_dir is not None:
        candidates.append(release_dir / "forecast.json")
    paths = manifest.get("paths") or {}
    for key in ("forecast", "forecast_latest"):
        p = Path(str(paths.get(key) or ""))
        if p:
            candidates.append(p)
    rid = manifest.get("run_id")
    if rid:
        candidates.append(ROOT / "data" / "releases" / str(rid) / "forecast.json")
        candidates.append(ARTIFACTS_DIR / f"forecast_{rid}.json")
    candidates.append(ARTIFACTS_DIR / "forecast_latest.json")
    for p in candidates:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError("sealed forecast.json not found for rebuild")


def recover_run_configuration(
    manifest: dict[str, Any],
    sealed_forecast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover fit knobs from sealed configuration block or forecast artifact."""
    cfg = dict(manifest.get("configuration") or {})
    art = sealed_forecast or {}
    diag = art.get("diagnostics") or {}
    method = str(cfg.get("method") or art.get("method") or diag.get("core_method") or "pymc")
    fit_method = str(
        cfg.get("fit_method")
        or diag.get("spine_method")
        or diag.get("core_method")
        or ("pymc" if method.startswith("ensemble") else method)
    )
    if fit_method.startswith("ensemble"):
        fit_method = str(diag.get("core_method") or diag.get("spine_method") or "pymc")
    ensemble = bool(cfg.get("ensemble", method.startswith("ensemble")))
    return {
        "election_id": manifest.get("election_id") or art.get("election_id"),
        "as_of": manifest.get("forecast_as_of") or art.get("as_of") or art.get("forecast_as_of"),
        "method": fit_method,
        "draws": int(cfg.get("draws") or manifest.get("draws") or art.get("draws") or 400),
        "tune": int(cfg.get("tune") or manifest.get("tune") or 400),
        "chains": int(cfg.get("chains") or manifest.get("chains") or 2),
        "seed": int(cfg.get("seed") or manifest.get("seed") or art.get("seed") or 0),
        "generic_ballot": float(
            cfg["generic_ballot"]
            if cfg.get("generic_ballot") is not None
            else (art.get("generic_ballot") if art.get("generic_ballot") is not None else -1.0)
        ),
        "ensemble": ensemble,
        "with_ratings": bool(cfg.get("with_ratings", True)),
        "with_markets": bool(cfg.get("with_markets", True)),
        "rating_weight": float(cfg.get("rating_weight", 0.15)),
        "market_weight": float(cfg.get("market_weight", 0.12)),
        "control_weight": float(cfg.get("control_weight", 0.15)),
        "control_calibrate": bool(cfg.get("control_calibrate", False)),
        "allow_fast_fallback": bool(cfg.get("allow_fast_fallback", False)),
    }


def compare_forecast_artifacts(
    sealed: dict[str, Any],
    rebuilt: dict[str, Any],
    *,
    tol_p_control: float = TOL_P_CONTROL,
    tol_seats: float = TOL_EXPECTED_SEATS,
    tol_race_p: float = TOL_RACE_P_MAX,
) -> dict[str, Any]:
    """Compare chamber / race probabilities within declared tolerances."""
    sch = sealed.get("chamber") or {}
    rch = rebuilt.get("chamber") or {}
    d_p = abs(float(sch.get("p_dem_majority") or 0) - float(rch.get("p_dem_majority") or 0))
    d_seats = abs(
        float(sch.get("expected_dem_seats") or 0) - float(rch.get("expected_dem_seats") or 0)
    )
    sealed_races = {str(r.get("race_id")): r for r in (sealed.get("races") or [])}
    diffs = []
    for r in rebuilt.get("races") or []:
        rid = str(r.get("race_id"))
        if rid not in sealed_races:
            continue
        dp = abs(float(r.get("p_dem") or 0) - float(sealed_races[rid].get("p_dem") or 0))
        diffs.append(dp)
    max_race = float(max(diffs)) if diffs else 0.0
    mean_race = float(sum(diffs) / len(diffs)) if diffs else 0.0
    checks = [
        {"name": "p_dem_majority", "ok": d_p <= tol_p_control, "delta": d_p, "tol": tol_p_control},
        {"name": "expected_dem_seats", "ok": d_seats <= tol_seats, "delta": d_seats, "tol": tol_seats},
        {"name": "max_race_p_dem", "ok": max_race <= tol_race_p, "delta": max_race, "tol": tol_race_p},
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "mean_abs_race_p_dem": mean_race,
        "n_races_compared": len(diffs),
    }


def independent_rebuild(
    *,
    run_id: str | None = None,
    release_dir: Path | None = None,
    out_dir: Path | None = None,
    require_domain_match: bool = True,
    write_artifact: bool = True,
    method_override: str | None = None,
) -> dict[str, Any]:
    """
    Re-execute ``run_forecast`` from a sealed manifest into a temp directory and
    compare chamber/race probabilities within MCSE-aligned tolerances (G10 hard).
    """
    import tempfile

    from midterms.pipeline.run_forecast import run_forecast

    lite = verify_rebuild(run_id=run_id)
    rid = run_id or lite.get("run_id")
    if release_dir is None and rid:
        cand = ROOT / "data" / "releases" / str(rid)
        if cand.exists():
            release_dir = cand
    man_path = MANIFESTS_DIR / f"run_{rid}.json" if rid else None
    if release_dir and (release_dir / "run_manifest.json").exists():
        man = json.loads((release_dir / "run_manifest.json").read_text(encoding="utf-8"))
        man_path = release_dir / "run_manifest.json"
    elif man_path and man_path.exists():
        man = json.loads(man_path.read_text(encoding="utf-8"))
    else:
        return {"ok": False, "error": "missing sealed run manifest", "lite": lite}

    sealed = _load_sealed_forecast(man, release_dir=release_dir)
    cfg = recover_run_configuration(man, sealed)
    if method_override:
        cfg["method"] = method_override

    domain_now = snapshot_domain_hashes()
    domain_expected = man.get("domain_hashes") or {}
    domain_mismatches = [
        k
        for k, v in domain_expected.items()
        if domain_now.get(k) != v
    ]
    domain_ok = len(domain_mismatches) == 0
    if require_domain_match and not domain_ok:
        return {
            "ok": False,
            "mode": "independent",
            "error": "domain_hashes drifted vs sealed manifest",
            "domain_mismatches": domain_mismatches[:20],
            "lite": lite,
            "run_id": rid,
        }

    tmp_owned = out_dir is None
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="midterms_rebuild_"))
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = run_forecast(
            election_id=str(cfg["election_id"]),
            as_of=str(cfg["as_of"])[:10],
            method=str(cfg["method"]),
            draws=int(cfg["draws"]),
            tune=int(cfg["tune"]),
            chains=int(cfg["chains"]),
            seed=int(cfg["seed"]),
            generic_ballot=float(cfg["generic_ballot"]),
            ensemble=bool(cfg["ensemble"]),
            with_ratings=bool(cfg["with_ratings"]),
            with_markets=bool(cfg["with_markets"]),
            rating_weight=float(cfg["rating_weight"]),
            market_weight=float(cfg["market_weight"]),
            control_weight=float(cfg["control_weight"]),
            control_calibrate=bool(cfg["control_calibrate"]),
            allow_fast_fallback=bool(cfg["allow_fast_fallback"]),
            out_dir=out,
            rebuild_mode=True,
            allow_non_publication=True,
        )
        rebuilt = result["artifact"]
        cmp = compare_forecast_artifacts(sealed, rebuilt)
        report = {
            "ok": bool(cmp.get("ok")) and (domain_ok or not require_domain_match),
            "mode": "independent",
            "run_id": rid,
            "manifest": str(man_path),
            "release_dir": str(release_dir) if release_dir else None,
            "configuration": cfg,
            "domain_ok": domain_ok,
            "domain_mismatches": domain_mismatches[:20],
            "comparison": cmp,
            "lite_hash_seal": lite,
            "rebuild_out_dir": str(out),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": (
                "G10 hard: re-executes run_forecast under rebuild_mode; compares "
                "probabilities within MCSE tolerances (not byte-identical JSON)."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        report = {
            "ok": False,
            "mode": "independent",
            "run_id": rid,
            "error": str(exc),
            "domain_ok": domain_ok,
            "lite_hash_seal": lite,
            "configuration": cfg,
        }
    if write_artifact:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        path = ARTIFACTS_DIR / "independent_rebuild_latest.json"
        path.write_bytes(json.dumps(report, indent=2, default=str).encode("utf-8"))
        report["path"] = str(path)
    if tmp_owned:
        # Keep temp dir for audit; path recorded in report.
        pass
    return report
