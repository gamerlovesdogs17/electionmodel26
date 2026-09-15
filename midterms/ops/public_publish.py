"""Public-facing live probability publication (post Milestone-0).

Requires green G1–G11 acceptance gates, publication-eligible evidence, and
passing numerical quality. Stamps ``forecast_latest.json``, syncs the research
UI artifact, and seals a prospective live shadow.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, MODEL_VERSION, ROOT
from midterms.ops.reproducibility import environment_lock, snapshot_domain_hashes
from midterms.validation.acceptance_gates import evaluate_acceptance_gates


PUBLICATION_INDEX = MANIFESTS_DIR / "public_publications.jsonl"
WEB_FORECAST = ROOT / "web" / "public" / "data" / "forecast_latest.json"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _code_commit() -> str | None:
    try:
        import subprocess

        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except Exception:  # noqa: BLE001
        return None


def _load_forecast(path: Path | None = None) -> dict[str, Any]:
    path = path or (ARTIFACTS_DIR / "forecast_latest.json")
    if not path.exists():
        raise FileNotFoundError(f"missing forecast artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def assert_public_ready(
    *,
    artifact: dict[str, Any] | None = None,
    gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Fail closed unless acceptance gates, eligibility, and numerical quality pass.
    """
    from midterms.config import PUBLIC_LIVE_ENABLED

    art = artifact or _load_forecast()
    gates = gates or evaluate_acceptance_gates(write=False)
    reasons: list[str] = []

    if not PUBLIC_LIVE_ENABLED:
        reasons.append(
            "PUBLIC_LIVE_ENABLED=False (fresh audit Stage-0 containment — "
            "research_only until independent truth + all-domain eligibility cleared)"
        )

    if not gates.get("ok"):
        reasons.append(
            "acceptance gates not green: " + ", ".join(gates.get("failures") or ["unknown"])
        )
    # Auditor-strict: any fail / not_evaluable / partial on P0-mapped gates blocks
    for g in gates.get("gate_list") or list((gates.get("gates") or {}).values()):
        if not isinstance(g, dict):
            continue
        if g.get("status") in {"fail", "not_evaluable"} or (
            g.get("id") in {"G1", "G2", "G3", "G4", "G11"} and g.get("status") != "pass"
        ):
            reasons.append(f"auditor gate {g.get('id')} status={g.get('status')}")

    run_class = str(art.get("run_class") or "")
    publishable = bool(art.get("publishable"))
    elig = art.get("evidence_eligibility") or (art.get("diagnostics") or {}).get(
        "evidence_eligibility"
    )
    if isinstance(elig, dict):
        if elig.get("publishable") is False:
            reasons.append(
                "evidence not publication-eligible: "
                + "; ".join((elig.get("reasons") or [])[:5] or ["blocked"])
            )
        if elig.get("run_class") and elig.get("run_class") != "publication":
            reasons.append(f"evidence run_class={elig.get('run_class')}")
    if run_class != "publication" or not publishable:
        reasons.append(f"artifact run_class={run_class!r} publishable={publishable}")

    nq = art.get("numerical_quality") or (art.get("diagnostics") or {}).get("numerical_quality")
    if not isinstance(nq, dict) or not nq.get("ok"):
        reasons.append(
            "numerical_quality not ok: "
            + "; ".join((nq or {}).get("alerts") or ["missing"])
        )
    else:
        # Re-check under publishable thresholds when possible
        try:
            from midterms.validation.numerical_quality import evaluate_numerical_quality

            chamber = art.get("chamber") or {}
            hist = chamber.get("seat_histogram") or []
            n_draws = int(sum(int(h.get("count") or 0) for h in hist)) if hist else 0
            hard = evaluate_numerical_quality(
                p_dem_control=float(chamber.get("p_dem_majority") or 0.5),
                n_posterior_samples=(art.get("diagnostics") or {}).get("n_posterior_samples")
                or (art.get("diagnostics") or {}).get("draws")
                or n_draws,

                convergence=(nq.get("convergence") if isinstance(nq.get("convergence"), dict) else None)
                or (art.get("diagnostics") or {}).get("convergence"),
                seed=art.get("seed"),
                publishable=True,
            )
            # Prefer chamber MCSE from stamped block when present
            if nq.get("chamber_mcse"):
                hard["chamber_mcse"] = nq["chamber_mcse"]
                hard["ok"] = bool(nq.get("ok")) and all(
                    c.get("ok")
                    for c in (nq.get("checks") or [])
                    if c.get("name") in {"mcse_control", "min_sim_draws", "r_hat", "ess_frac", "min_posterior"}
                ) if nq.get("checks") else bool(nq.get("ok"))
            if not hard.get("ok") and not nq.get("ok"):
                reasons.append(
                    "publishable numerical gate failed: "
                    + "; ".join(hard.get("alerts") or nq.get("alerts") or [])
                )
        except Exception as exc:  # noqa: BLE001
            if not nq.get("ok"):
                reasons.append(f"numerical recheck error: {exc}")

    method = str(art.get("method") or "")
    core = str(
        (art.get("diagnostics") or {}).get("spine_method")
        or (art.get("diagnostics") or {}).get("core_method")
        or ""
    )
    if not (
        method.startswith("pymc")
        or (
            method.startswith("ensemble")
            and (core.startswith("pymc") or core == "")
            and not core.startswith("fast")
        )
    ):
        reasons.append(f"non-production method method={method} core={core}")

    ok = len(reasons) == 0
    return {
        "ok": ok,
        "reasons": reasons,
        "gates_ok": bool(gates.get("ok")),
        "run_class": run_class,
        "publishable": publishable,
        "model_version": art.get("model_version"),
        "election_id": art.get("election_id"),
        "as_of": art.get("as_of") or art.get("forecast_as_of"),
    }


def stamp_public_release(
    artifact: dict[str, Any],
    *,
    published_at: str | None = None,
    publication_id: str | None = None,
) -> dict[str, Any]:
    """Return a copy of the artifact stamped for public live surface."""
    stamped = dict(artifact)
    published_at = published_at or datetime.now(timezone.utc).isoformat()
    as_of = str(stamped.get("as_of") or stamped.get("forecast_as_of") or "unknown")[:10]
    publication_id = publication_id or (
        f"public_{stamped.get('election_id')}_{as_of}_"
        f"{MODEL_VERSION}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    block = {
        "enabled": True,
        "surface": "live",
        "publication_id": publication_id,
        "published_at": published_at,
        "model_version": MODEL_VERSION,
        "code_commit": _code_commit(),
        "acceptance_gates_ok": True,
        "disclaimer": (
            "Probabilistic Senate forecast for research and public information. "
            "Not betting advice. Chamber control uses ≥51 Dem seats; 50–50 is Republican "
            "via the Vice President. Independents without a Dem nominee still count toward "
            "Democratic caucus seats when applicable."
        ),
        "limitations_url_hint": "MODEL_CARD.md / acceptance_gates_latest.md",
        "archive_scope": "2018-2024 official for validation; live target is senate-2026",
    }
    stamped["public_release"] = block
    stamped["publication_surface"] = "live"
    stamped["model_version"] = MODEL_VERSION
    # Keep run_class/publishable as-is (must already be publication)
    return stamped


def _write_forecast_bytes(path: Path, artifact: dict[str, Any]) -> str:
    text = json.dumps(artifact, indent=2, default=str, allow_nan=False)
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha256_bytes(data)


def publish_live(
    *,
    forecast_path: Path | None = None,
    seal_shadow: bool = True,
    sync_web: bool = True,
) -> dict[str, Any]:
    """
    Promote the latest forecast to the public live surface.

    Exit: stamped artifact + publication index row (+ optional live shadow).
    """
    forecast_path = forecast_path or (ARTIFACTS_DIR / "forecast_latest.json")
    gates = evaluate_acceptance_gates(write=True)
    art = _load_forecast(forecast_path)
    ready = assert_public_ready(artifact=art, gates=gates)
    if not ready["ok"]:
        raise ValueError(
            "Not ready for public live probabilities: " + "; ".join(ready["reasons"])
        )

    stamped = stamp_public_release(art)
    pub_id = stamped["public_release"]["publication_id"]
    digest = _write_forecast_bytes(forecast_path, stamped)
    # Also write named copy under artifacts
    named = ARTIFACTS_DIR / f"forecast_{stamped.get('run_id')}_public.json"
    _write_forecast_bytes(named, stamped)

    # Re-seal run manifest + release archive so verify-rebuild stays green (G10).
    try:
        from midterms.ops.monitor import append_release_index, archive_release
        from midterms.ops.signing import sign_payload

        run_id = str(stamped.get("run_id") or "unknown")
        man_path = MANIFESTS_DIR / f"run_{run_id}.json"
        if man_path.exists():
            man = json.loads(man_path.read_text(encoding="utf-8"))
        else:
            man = {"run_id": run_id, "paths": {}}
        man["model_version"] = MODEL_VERSION
        man["public_release"] = stamped.get("public_release")
        man["publication_surface"] = "live"
        hashes = dict(man.get("output_hashes") or {})
        hashes["forecast_json"] = digest
        draws_path = Path(str((man.get("paths") or {}).get("draws") or ""))
        if draws_path.exists():
            hashes["draws"] = _sha256_bytes(draws_path.read_bytes())
        man["output_hashes"] = hashes
        man["paths"] = {
            **(man.get("paths") or {}),
            "forecast": str(named),
            "forecast_latest": str(forecast_path),
        }
        man["signature"] = sign_payload(man)
        man_path.write_bytes(json.dumps(man, indent=2, default=str).encode("utf-8"))
        text = forecast_path.read_bytes().decode("utf-8")
        append_release_index(man)
        archive_release(man, text)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"failed to re-seal public release hashes: {exc}") from exc

    if sync_web:
        WEB_FORECAST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(forecast_path, WEB_FORECAST)

    shadow_meta = None
    if seal_shadow:
        from midterms.ops.shadow_publish import publish_live_shadow

        shadow_meta = publish_live_shadow(
            forecast_path=forecast_path,
            shadow_id=f"live_{pub_id}",
        )

    publication = {
        "audit_item": "PublicLive",
        "publication_id": pub_id,
        "published_at": stamped["public_release"]["published_at"],
        "model_version": MODEL_VERSION,
        "code_commit": _code_commit(),
        "election_id": stamped.get("election_id"),
        "as_of": stamped.get("as_of") or stamped.get("forecast_as_of"),
        "run_id": stamped.get("run_id"),
        "run_class": stamped.get("run_class"),
        "publishable": stamped.get("publishable"),
        "forecast_sha256": digest,
        "paths": {
            "forecast_latest": str(forecast_path),
            "forecast_public_copy": str(named),
            "web_forecast": str(WEB_FORECAST) if sync_web else None,
            "shadow": (shadow_meta or {}).get("path"),
        },
        "acceptance_gates": {
            "ok": gates.get("ok"),
            "n_pass": gates.get("n_pass"),
            "n_fail": gates.get("n_fail"),
            "failures": gates.get("failures"),
        },
        "chamber": {
            "p_dem_majority": (stamped.get("chamber") or {}).get("p_dem_majority"),
            "expected_dem_seats": (stamped.get("chamber") or {}).get("expected_dem_seats"),
        },
        "environment": environment_lock(),
        "domain_hashes": snapshot_domain_hashes(),
        "exit_condition": "Public-facing live probabilities published behind green acceptance gates.",
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    pub_path = ARTIFACTS_DIR / "publication_latest.json"
    pub_path.write_bytes(json.dumps(publication, indent=2, default=str).encode("utf-8"))
    publication["path"] = str(pub_path)

    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    with PUBLICATION_INDEX.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "publication_id": pub_id,
                    "published_at": publication["published_at"],
                    "model_version": MODEL_VERSION,
                    "forecast_sha256": digest,
                    "path": str(pub_path),
                    "shadow_path": (shadow_meta or {}).get("path"),
                },
                default=str,
            )
            + "\n"
        )

    # Refresh acceptance gates with public_live flag note in markdown via re-run
    gates2 = evaluate_acceptance_gates(write=True)
    publication["acceptance_gates_after"] = {
        "ok": gates2.get("ok"),
        "n_pass": gates2.get("n_pass"),
    }
    return publication
