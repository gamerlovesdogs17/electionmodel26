"""Prospective shadow publication — frozen, timestamped, write-once (audit P3).

Exit condition: at least one cycle of frozen, timestamped evaluation is retained.

A shadow bundle seals predictions (and supporting validation artifacts) under
``data/shadow/{shadow_id}/`` with content hashes. Sealed prediction files are
never overwritten. Post-seal ``evaluation.json`` may be added once without
mutating the frozen forecast.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from midterms.config import (
    ARTIFACTS_DIR,
    MANIFESTS_DIR,
    MODEL_VERSION,
    PRIMARY_HOLDOUT,
    ROOT,
)
from midterms.evidence.warehouse import Warehouse
from midterms.ops.reproducibility import environment_lock, snapshot_domain_hashes
from midterms.validation.nested_component_loo import (
    FrozenPrediction,
    freeze_component_predictions,
    score_frozen_predictions,
)


SHADOW_ROOT = ROOT / "data" / "shadow"
SHADOW_INDEX = MANIFESTS_DIR / "shadow_publications.jsonl"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _code_commit() -> str | None:
    try:
        import subprocess

        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except Exception:  # noqa: BLE001
        return None


def _write_once(path: Path, text: str) -> str:
    if path.exists():
        raise FileExistsError(f"shadow seal refuses overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return _sha256_bytes(data)


def _copy_once(src: Path, dest: Path) -> str | None:
    if not src.exists():
        return None
    if dest.exists():
        raise FileExistsError(f"shadow seal refuses overwrite: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return _sha256_file(dest)


def _try_sign(path: Path, text: str) -> bool:
    try:
        from midterms.ops.signing import write_signature_sidecar

        write_signature_sidecar(path, text)
        return True
    except Exception:  # noqa: BLE001
        return False


def _freeze_spine(
    snap,
    *,
    election_id: str,
    holdout_year: int,
    lead_days: int,
    hierarchical_method: str,
    n_draws: int,
    seed: int,
) -> dict[str, FrozenPrediction]:
    """Freeze only the hierarchical spine (faster retained-cycle seal)."""
    from midterms.validation.nested_component_loo import (
        _fit_hierarchical,
        _freeze_from_fit,
        _generic_ballot,
    )

    gb = _generic_ballot(snap)
    name = "pymc" if hierarchical_method.startswith("pymc") else "fast_hierarchical_t"
    fit = _fit_hierarchical(
        snap, method=hierarchical_method, n_draws=n_draws, seed=seed, gb=gb
    )
    return {
        name: _freeze_from_fit(
            fit,
            component=name,
            election_id=election_id,
            holdout_year=holdout_year,
            lead_days=lead_days,
            as_of=snap.as_of,
            seed=seed,
        )
    }


def list_shadow_publications() -> list[dict[str, Any]]:
    if not SHADOW_INDEX.exists():
        return []
    rows = []
    for line in SHADOW_INDEX.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append_shadow_index(entry: dict[str, Any]) -> Path:
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    with SHADOW_INDEX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return SHADOW_INDEX


def publish_historical_shadow(
    *,
    year: int = PRIMARY_HOLDOUT,
    lead_days: int = 60,
    hierarchical_method: str = "fast",
    n_draws: int = 800,
    seed: int = 20260913,
    shadow_id: str | None = None,
    spine_only: bool = True,
    shadow_root: Path | None = None,
    milestone: str | None = None,
    extra_validation: tuple[str, ...] = (),
    audit_item: str = "P3",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Freeze then score one historical cycle as-of (audit P3 retained evaluation).

    Predictions are frozen before certified results are read. The sealed directory
    is write-once. Set ``milestone='first_publishable'`` for the Milestone-0 seal.
    """
    election_id = f"senate-{year}"
    wh = Warehouse()
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        raise ValueError(f"no races for {election_id}")
    ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
    as_of = ed - timedelta(days=lead_days)
    stamped = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = "milestone" if milestone else "shadow"
    shadow_id = shadow_id or (
        f"{prefix}_{election_id}_{as_of.isoformat()}_{MODEL_VERSION}_{stamped}"
    )
    root = shadow_root or SHADOW_ROOT
    dest = root / shadow_id
    if dest.exists():
        raise FileExistsError(f"shadow already exists: {dest}")
    dest.mkdir(parents=True, exist_ok=False)

    mode = "milestone_first_publishable" if milestone == "first_publishable" else "historical_cycle"

    # --- Freeze (no results) ---
    snap = wh.build_as_of(as_of, election_id)
    if spine_only:
        frozen = _freeze_spine(
            snap,
            election_id=election_id,
            holdout_year=year,
            lead_days=lead_days,
            hierarchical_method=hierarchical_method,
            n_draws=n_draws,
            seed=seed,
        )
    else:
        frozen = freeze_component_predictions(
            snap,
            election_id=election_id,
            holdout_year=year,
            lead_days=lead_days,
            hierarchical_method=hierarchical_method,
            n_draws=n_draws,
            seed=seed,
        )
    spine = "pymc" if hierarchical_method.startswith("pymc") else "fast_hierarchical_t"
    frozen_payload = {
        "shadow_id": shadow_id,
        "mode": mode,
        "milestone": milestone,
        "election_id": election_id,
        "holdout_year": year,
        "lead_days": lead_days,
        "as_of": as_of.isoformat(),
        "election_day": ed.isoformat(),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "hierarchical_method": hierarchical_method,
        "spine_label": spine,
        "spine_only": spine_only,
        "seed": seed,
        "n_draws": n_draws,
        "freeze_before_truth": True,
        "notes": notes or [],
        "predictions": {k: asdict(v) for k, v in frozen.items()},
    }
    pred_text = json.dumps(frozen_payload, indent=2, default=str)
    hashes: dict[str, str] = {}
    pred_path = dest / "frozen_predictions.json"
    hashes["frozen_predictions.json"] = _write_once(pred_path, pred_text)
    if _try_sign(pred_path, pred_text):
        sig_path = pred_path.with_suffix(pred_path.suffix + ".sig.json")
        if sig_path.exists():
            hashes["frozen_predictions.json.sig.json"] = _sha256_file(sig_path)

    # --- Truth contact only after freeze ---
    results = wh.results[wh.results["election_id"] == election_id]
    scores = score_frozen_predictions(frozen, results)
    spine_score = scores.get(spine) or {}
    evaluation = {
        "shadow_id": shadow_id,
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "freeze_before_truth": True,
        "milestone": milestone,
        "scores": scores,
        "spine_crps": spine_score.get("crps"),
        "n_components_ok": sum(
            1 for s in scores.values() if isinstance(s, dict) and s.get("status") == "ok"
        ),
    }
    eval_text = json.dumps(evaluation, indent=2, default=str)
    hashes["evaluation.json"] = _write_once(dest / "evaluation.json", eval_text)

    # Supporting validation copies (optional)
    validation_names = (
        "nested_component_loo.json",
        "stack_weights_oof.json",
        "covariance_calibration.json",
        "coefficient_stability.json",
        "poll_coverage_latest.json",
        "chamber_reconcile_latest.json",
        *extra_validation,
    )
    for name in validation_names:
        h = _copy_once(ARTIFACTS_DIR / name, dest / "validation" / name)
        if h:
            hashes[f"validation/{name}"] = h

    notice_title = (
        "# First publishable milestone shadow (Milestone-0)\n\n"
        if milestone == "first_publishable"
        else "# Shadow publication (audit P3)\n\n"
    )
    notice = (
        notice_title
        + f"- **shadow_id:** `{shadow_id}`\n"
        f"- **cycle:** {election_id} as-of {as_of.isoformat()} (D−{lead_days})\n"
        f"- **model_version:** {MODEL_VERSION}\n"
        f"- **spine:** {spine} (`{hierarchical_method}`)\n"
        f"- **milestone:** {milestone or 'none'}\n"
        f"- **freeze_before_truth:** true\n"
        f"- **sealed:** write-once under `data/shadow/`\n\n"
        "This is a retained frozen evaluation for independent audit — not a live "
        "public probability product. Do not overwrite sealed prediction files.\n"
    )
    if notes:
        notice += "\n## Notes\n\n" + "\n".join(f"- {n}" for n in notes) + "\n"
    hashes["AUDIT_NOTICE.md"] = _write_once(dest / "AUDIT_NOTICE.md", notice)

    manifest = {
        "audit_item": audit_item,
        "shadow_id": shadow_id,
        "mode": mode,
        "milestone": milestone,
        "election_id": election_id,
        "as_of": as_of.isoformat(),
        "lead_days": lead_days,
        "model_version": MODEL_VERSION,
        "code_commit": _code_commit(),
        "frozen_at": frozen_payload["frozen_at"],
        "scored_at": evaluation["scored_at"],
        "freeze_before_truth": True,
        "hierarchical_method": hierarchical_method,
        "spine_label": spine,
        "spine_only": spine_only,
        "notes": notes or [],
        "environment": environment_lock(),
        "domain_hashes": snapshot_domain_hashes(),
        "file_hashes": hashes,
        "paths": {
            "shadow_dir": str(dest),
            "frozen_predictions": str(pred_path),
            "evaluation": str(dest / "evaluation.json"),
        },
        "spine_crps": evaluation.get("spine_crps"),
        "exit_condition": (
            "First publishable milestone: pymc-spine frozen evaluation + G1–G11 acceptance."
            if milestone == "first_publishable"
            else "At least one cycle of frozen, timestamped evaluation is retained."
        ),
        "write_once": True,
    }
    man_text = json.dumps(manifest, indent=2, default=str)
    _write_once(dest / "SHADOW_MANIFEST.json", man_text)
    _try_sign(dest / "SHADOW_MANIFEST.json", man_text)

    index_entry = {
        "shadow_id": shadow_id,
        "mode": mode,
        "milestone": milestone,
        "election_id": election_id,
        "as_of": as_of.isoformat(),
        "model_version": MODEL_VERSION,
        "spine_label": spine,
        "frozen_at": frozen_payload["frozen_at"],
        "spine_crps": evaluation.get("spine_crps"),
        "path": str(dest),
        "manifest_sha256": _sha256_bytes(man_text.encode("utf-8")),
    }
    append_shadow_index(index_entry)
    manifest["index_path"] = str(SHADOW_INDEX)
    manifest["path"] = str(dest)
    return manifest


def publish_milestone_shadow(
    *,
    year: int = PRIMARY_HOLDOUT,
    lead_days: int = 60,
    n_draws: int = 800,
    seed: int = 20260913,
) -> dict[str, Any]:
    """
    Milestone-0 seal: pymc spine, eligibility-clean inputs, acceptance gates copied in.

    Prior fast P3 shadows remain immutable. OOF stack/nested LOO may still be
    fast-scored; that is stamped honestly and not remapped to pymc.
    """
    from midterms.validation.acceptance_gates import evaluate_acceptance_gates

    # Refresh acceptance gates before sealing
    gates = evaluate_acceptance_gates(write=True)
    notes = [
        "Shadow forecast spine is pymc (production). Nested LOO / OOF stack weights remain "
        "fast-scored until a later refresh — no silent remapping.",
        "Archive scope: 2018–2024 official; 2014/2016 provisional.",
        "Not a public live probability product.",
        f"Acceptance gates overall ok={gates.get('ok')} "
        f"(fail={gates.get('failures')} partial={gates.get('partials')})",
    ]
    return publish_historical_shadow(
        year=year,
        lead_days=lead_days,
        hierarchical_method="pymc",
        n_draws=n_draws,
        seed=seed,
        spine_only=True,
        milestone="first_publishable",
        extra_validation=("acceptance_gates_latest.json", "evidence_eligibility_latest.json"),
        audit_item="Milestone-0",
        notes=notes,
    )


def publish_live_shadow(
    *,
    forecast_path: Path | None = None,
    shadow_id: str | None = None,
    shadow_root: Path | None = None,
) -> dict[str, Any]:
    """Seal ``forecast_latest.json`` as a prospective shadow (no truth attached)."""
    forecast_path = forecast_path or (ARTIFACTS_DIR / "forecast_latest.json")
    if not forecast_path.exists():
        raise FileNotFoundError(f"missing forecast artifact: {forecast_path}")
    art = json.loads(forecast_path.read_text(encoding="utf-8"))
    stamped = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    election_id = str(art.get("election_id") or "unknown")
    as_of = str(art.get("as_of") or art.get("forecast_as_of") or "unknown")[:10]
    shadow_id = shadow_id or f"shadow_{election_id}_{as_of}_{MODEL_VERSION}_{stamped}"
    root = shadow_root or SHADOW_ROOT
    dest = root / shadow_id
    if dest.exists():
        raise FileExistsError(f"shadow already exists: {dest}")
    dest.mkdir(parents=True, exist_ok=False)

    text = forecast_path.read_text(encoding="utf-8")
    hashes: dict[str, str] = {}
    hashes["forecast.json"] = _write_once(dest / "forecast.json", text)
    _try_sign(dest / "forecast.json", text)

    for name in ("stack_weights_oof.json", "nested_component_loo.json"):
        h = _copy_once(ARTIFACTS_DIR / name, dest / "validation" / name)
        if h:
            hashes[f"validation/{name}"] = h

    nq = art.get("numerical_quality")
    if nq:
        hashes["numerical_quality.json"] = _write_once(
            dest / "numerical_quality.json", json.dumps(nq, indent=2, default=str)
        )

    notice = (
        "# Prospective shadow publication (audit P3)\n\n"
        f"- **shadow_id:** `{shadow_id}`\n"
        f"- **election_id:** {election_id}\n"
        f"- **as_of:** {as_of}\n"
        f"- **model_version:** {MODEL_VERSION}\n"
        f"- **evaluation:** deferred until certified results exist\n\n"
        "Frozen live/prospective forecast. Do not overwrite sealed files.\n"
    )
    hashes["AUDIT_NOTICE.md"] = _write_once(dest / "AUDIT_NOTICE.md", notice)

    manifest = {
        "audit_item": "P3",
        "shadow_id": shadow_id,
        "mode": "prospective_live",
        "election_id": election_id,
        "as_of": as_of,
        "model_version": MODEL_VERSION,
        "code_commit": _code_commit(),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "evaluation": None,
        "environment": environment_lock(),
        "domain_hashes": snapshot_domain_hashes(),
        "file_hashes": hashes,
        "paths": {"shadow_dir": str(dest), "forecast": str(dest / "forecast.json")},
        "publishable": bool(art.get("publishable") or (art.get("diagnostics") or {}).get("publishable")),
        "run_class": art.get("run_class") or (art.get("diagnostics") or {}).get("run_class"),
        "exit_condition": "At least one cycle of frozen, timestamped evaluation is retained.",
        "write_once": True,
    }
    man_text = json.dumps(manifest, indent=2, default=str)
    _write_once(dest / "SHADOW_MANIFEST.json", man_text)
    _try_sign(dest / "SHADOW_MANIFEST.json", man_text)
    append_shadow_index(
        {
            "shadow_id": shadow_id,
            "mode": "prospective_live",
            "election_id": election_id,
            "as_of": as_of,
            "model_version": MODEL_VERSION,
            "frozen_at": manifest["frozen_at"],
            "path": str(dest),
            "manifest_sha256": _sha256_bytes(man_text.encode("utf-8")),
        }
    )
    manifest["path"] = str(dest)
    return manifest


def verify_shadow(shadow_id: str | None = None, *, path: Path | None = None) -> dict[str, Any]:
    """Recompute file hashes and verify Ed25519 sidecars when present.

    Hash match alone is reported as ``hashes_ok``; cryptographic verification is
    a separate ``signatures_ok`` field so G10 cannot claim 'signature verified'
    from hashes only.
    """
    if path is None:
        if shadow_id is None:
            rows = list_shadow_publications()
            if not rows:
                return {"ok": False, "error": "no shadow publications indexed"}
            path = Path(rows[-1]["path"])
            shadow_id = rows[-1]["shadow_id"]
        else:
            path = SHADOW_ROOT / shadow_id
    man_path = path / "SHADOW_MANIFEST.json"
    if not man_path.exists():
        return {"ok": False, "error": f"missing manifest at {man_path}"}
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    expected = manifest.get("file_hashes") or {}
    mismatches = []
    checked = []
    for rel, digest in expected.items():
        fp = path / rel
        if not fp.exists():
            mismatches.append({"file": rel, "error": "missing"})
            continue
        got = _sha256_file(fp)
        checked.append(rel)
        if got != digest:
            mismatches.append({"file": rel, "expected": digest, "got": got})
    hashes_ok = len(mismatches) == 0

    # Signature verification against historical key_id when sidecars exist.
    from midterms.ops.signing import resolve_historical_public_key, verify_signature

    sig_results = []
    sig_ok = True
    sig_checked = 0
    for rel in list(expected.keys()) + ["SHADOW_MANIFEST.json"]:
        fp = path / rel
        sig_path = Path(str(fp) + ".sig.json")
        if not sig_path.exists():
            # Also try path.with_suffix style used by write_signature_sidecar
            alt = fp.with_suffix(fp.suffix + ".sig.json")
            if alt.exists():
                sig_path = alt
            else:
                continue
        if not fp.exists():
            continue
        try:
            sig_doc = json.loads(sig_path.read_text(encoding="utf-8"))
            payload_text = fp.read_text(encoding="utf-8")
            key_path = resolve_historical_public_key(sig_doc.get("key_id"))
            verified = verify_signature(
                payload_text,
                str(sig_doc.get("signature") or ""),
                alg=str(sig_doc.get("alg") or ""),
                public_key_path=key_path,
            )
            sig_checked += 1
            sig_results.append(
                {
                    "file": rel,
                    "ok": verified,
                    "alg": sig_doc.get("alg"),
                    "key_id": sig_doc.get("key_id"),
                    "key_path": str(key_path) if key_path else None,
                }
            )
            if not verified:
                sig_ok = False
        except Exception as exc:  # noqa: BLE001
            sig_checked += 1
            sig_ok = False
            sig_results.append({"file": rel, "ok": False, "error": str(exc)})

    # If no sidecars: hashes-only — do not claim signature verification.
    if sig_checked == 0:
        signatures_ok = False
        signature_status = "missing"
    elif sig_ok:
        signatures_ok = True
        signature_status = "verified"
    elif hashes_ok:
        # File integrity holds but Ed25519 does not verify against the resolved
        # public key — typical after in-place key rotation without retaining the
        # historical public key. Not an integrity failure; not a crypto pass.
        signatures_ok = False
        signature_status = "unverifiable"
    else:
        signatures_ok = False
        signature_status = "failed"

    return {
        "ok": hashes_ok and (signatures_ok if sig_checked and signature_status == "verified" else hashes_ok),
        "hashes_ok": hashes_ok,
        "signatures_ok": signatures_ok if sig_checked else None,
        "signature_status": signature_status,
        "signature_checks": sig_results,
        "shadow_id": shadow_id or manifest.get("shadow_id"),
        "path": str(path),
        "checked": checked,
        "mismatches": mismatches,
        "has_evaluation": (path / "evaluation.json").exists(),
        "mode": manifest.get("mode"),
        "claim": (
            "Hashes recomputed; Ed25519 sidecars verified against historical key_id "
            "when present. missing=no sidecar; unverifiable=hash ok but key does not "
            "verify (e.g. rotated trust root); failed=integrity or corrupt seal."
        ),
    }


def ensure_retained_shadow(
    *,
    year: int = PRIMARY_HOLDOUT,
    lead_days: int = 60,
    hierarchical_method: str = "fast",
    n_draws: int = 400,
) -> dict[str, Any]:
    """
    Ensure at least one historical shadow evaluation exists (P3 exit).

    If a matching sealed bundle is already indexed, return it; otherwise publish.
    """
    election_id = f"senate-{year}"
    for row in list_shadow_publications():
        if (
            row.get("mode") == "historical_cycle"
            and row.get("election_id") == election_id
            and Path(str(row.get("path") or "")).exists()
        ):
            ver = verify_shadow(path=Path(row["path"]))
            return {
                "created": False,
                "shadow_id": row["shadow_id"],
                "path": row["path"],
                "verify": ver,
                "note": "retained shadow already present",
            }
    created = publish_historical_shadow(
        year=year,
        lead_days=lead_days,
        hierarchical_method=hierarchical_method,
        n_draws=n_draws,
        spine_only=True,
    )
    ver = verify_shadow(path=Path(created["path"]))
    return {"created": True, "manifest": created, "verify": ver}
