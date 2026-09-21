"""Fail-closed coherence between forecast, eligibility, gates, and rebuild artifacts.

Prevents the recurring failure mode where ``forecast_latest.json`` is refreshed
to a publication-eligible evidence snapshot while ``evidence_eligibility_latest``
/ acceptance gates / independent rebuild still describe an older run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, MODEL_VERSION, PUBLIC_LIVE_ENABLED, ROOT


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def evidence_manifest_fingerprint() -> dict[str, Any]:
    """Stable fingerprint of evidence manifests that gate publication."""
    names = [
        "fundraising_shares.json",
        "economics_vintages.json",
        "pres_approval.json",
        "demography.json",
        "expert_ratings.json",
        "markets_kalshi.json",
        "official_senate_ballots.json",
        "presidential_vote_sources.json",
        "wiki_ratings.json",
    ]
    digests: dict[str, str] = {}
    for name in names:
        p = MANIFESTS_DIR / name
        if not p.exists():
            digests[name] = "missing"
            continue
        digests[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    blob = json.dumps(digests, sort_keys=True).encode()
    return {
        "sha256": hashlib.sha256(blob).hexdigest(),
        "manifests": digests,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def stamp_eligibility_identity(
    report: dict[str, Any],
    *,
    run_id: str | None = None,
    snapshot_id: str | None = None,
    forecast_generated_at: str | None = None,
) -> dict[str, Any]:
    """Attach fingerprints so downstream gates can refuse stale eligibility."""
    out = dict(report)
    fp = evidence_manifest_fingerprint()
    out["evidence_fingerprint"] = fp
    out["model_version"] = MODEL_VERSION
    out["PUBLIC_LIVE_ENABLED"] = PUBLIC_LIVE_ENABLED
    if run_id:
        out["forecast_run_id"] = run_id
    if snapshot_id:
        out["snapshot_id"] = snapshot_id
    if forecast_generated_at:
        out["forecast_generated_at"] = forecast_generated_at
    out["identity_checked_at"] = datetime.now(timezone.utc).isoformat()
    return out


def check_run_coherence(
    *,
    artifacts_dir: Path | None = None,
    require_matching_eligibility: bool = True,
) -> dict[str, Any]:
    """
    Compare forecast_latest against eligibility / rebuild / release identity.

    Returns ``ok=False`` with explicit mismatches — never soft-passes contradictions.
    """
    art_dir = artifacts_dir or ARTIFACTS_DIR
    forecast = _load(art_dir / "forecast_latest.json")
    eligibility = _load(art_dir / "evidence_eligibility_latest.json")
    rebuild = _load(art_dir / "independent_rebuild_latest.json")
    publication = _load(art_dir / "publication_latest.json")

    mismatches: list[str] = []
    notes: list[str] = []

    if forecast is None:
        return {
            "ok": False,
            "mismatches": ["missing forecast_latest.json"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    run_id = str(forecast.get("run_id") or "")
    if forecast.get("election_id") == "senate-2026":
        from midterms.evidence.outcome_identity import INDEPENDENT_DEM_CAUCUSES_BASIS

        policy = (forecast.get("chamber") or {}).get("independent_caucus_policy") or {}
        if (policy.get("ballot_party") != "I"
                or policy.get("seat_accounting_caucus") != "D"
                or policy.get("basis") != INDEPENDENT_DEM_CAUCUSES_BASIS):
            mismatches.append("Independent caucus accounting policy differs from current code")
    model_version = str(forecast.get("model_version") or "")
    snap = forecast.get("snapshot") or {}
    snapshot_id = str(snap.get("snapshot_id") or (forecast.get("snapshot_ids") or {}).get("evidence") or "")
    emb_elig = forecast.get("evidence_eligibility") or {}
    emb_fp = str((forecast.get("evidence_fingerprint") or {}).get("sha256") or "")
    source_sha = forecast.get("presidential_source_sha256") or snap.get("presidential_source_sha256")
    if source_sha:
        try:
            from midterms.evidence.presidential_results import verified_source_set_sha256

            if verified_source_set_sha256() != source_sha:
                mismatches.append("presidential source fingerprint differs from forecast")
        except (FileNotFoundError, ValueError) as exc:
            mismatches.append(f"presidential source verification failed: {exc}")
    elif forecast.get("publishable"):
        mismatches.append("publishable forecast lacks presidential source fingerprint")
    prior_sha = forecast.get("prior_store_sha256")
    prior_rel = snap.get("prior_snapshot_path")
    if prior_sha and prior_rel:
        try:
            from midterms.evidence.presidential_prior import _canonical_sha256

            prior_path = (ROOT / str(prior_rel)).resolve()
            if not prior_path.is_relative_to(ROOT.resolve()):
                raise ValueError("prior snapshot path escapes repository")
            prior_payload = json.loads(prior_path.read_text(encoding="utf-8"))
            computed = _canonical_sha256({
                key: value for key, value in prior_payload.items() if key != "snapshot_sha256"
            })
            if computed != prior_sha or prior_payload.get("snapshot_sha256") != prior_sha:
                mismatches.append("derived-prior snapshot fingerprint differs from forecast")
            if source_sha and prior_payload.get("source_set_sha256") != source_sha:
                mismatches.append("derived-prior source set differs from forecast")
        except (OSError, ValueError, TypeError) as exc:
            mismatches.append(f"derived-prior snapshot verification failed: {exc}")
    elif forecast.get("publishable"):
        mismatches.append("publishable forecast lacks derived-prior store fingerprint or path")

    stack_sha = forecast.get("stack_artifact_sha256")
    if stack_sha:
        stack_path = ARTIFACTS_DIR / "stack_weights_oof.json"
        if not stack_path.is_file() or hashlib.sha256(stack_path.read_bytes()).hexdigest() != stack_sha:
            mismatches.append("stack artifact fingerprint differs from forecast")
    elif forecast.get("publishable"):
        mismatches.append("publishable forecast lacks a stack artifact fingerprint")

    market_sha = forecast.get("market_store_sha256")
    market_audit_sha = forecast.get("market_audit_sha256")
    if market_sha or market_audit_sha or forecast.get("publishable"):
        from midterms.evidence.markets import verify_market_store_integrity

        market_check = verify_market_store_integrity(as_of=str(forecast.get("forecast_as_of") or "")[:10])
        if not market_check["ok"]:
            mismatches.append(f"market store integrity failed: {market_check['reason']}")
        else:
            market_manifest = _load(MANIFESTS_DIR / "markets_kalshi.json") or {}
            if market_manifest.get("normalized_races_sha256") != market_sha:
                mismatches.append("market store fingerprint differs from forecast")
            if market_check.get("audit_sha256") != market_audit_sha:
                mismatches.append("market mapping audit fingerprint differs from forecast")

    decomposition_name = forecast.get("decomposition_artifact")
    if decomposition_name:
        if Path(str(decomposition_name)).name != decomposition_name:
            mismatches.append("decomposition artifact path is unsafe")
        else:
            decomposition_path = art_dir / str(decomposition_name)
            decomposition = _load(decomposition_path)
            if decomposition is None:
                mismatches.append("decomposition artifact missing or unreadable")
            else:
                expected_decomposition = {
                    "run_id": run_id,
                    "model_version": model_version,
                    "snapshot_id": snapshot_id,
                    "prior_store_sha256": prior_sha,
                    "forecast_sha256": hashlib.sha256(
                        (art_dir / "forecast_latest.json").read_bytes()
                    ).hexdigest(),
                }
                if market_sha:
                    expected_decomposition["market_store_sha256"] = market_sha
                    expected_decomposition["market_audit_sha256"] = market_audit_sha
                if stack_sha:
                    expected_decomposition["stack_artifact_sha256"] = stack_sha
                for field, value in expected_decomposition.items():
                    if decomposition.get(field) != value:
                        mismatches.append(f"decomposition.{field} differs from forecast")
                if (decomposition.get("evidence_fingerprint") or {}).get("sha256") != emb_fp:
                    mismatches.append("decomposition evidence fingerprint differs from forecast")
    elif forecast.get("publishable"):
        mismatches.append("publishable forecast lacks decomposition artifact")

    if model_version and model_version != MODEL_VERSION:
        mismatches.append(
            f"forecast model_version={model_version} != config MODEL_VERSION={MODEL_VERSION}"
        )

    if require_matching_eligibility:
        if eligibility is None:
            mismatches.append("missing evidence_eligibility_latest.json")
        else:
            elig_pub = bool(eligibility.get("publishable"))
            fc_pub = bool(forecast.get("publishable"))
            if fc_pub and not elig_pub:
                mismatches.append(
                    "forecast is publishable while evidence eligibility is false"
                )
            elig_class = str(eligibility.get("run_class") or "")
            fc_class = str(forecast.get("run_class") or "")
            if fc_class == "publication" and elig_class != "publication":
                mismatches.append(
                    "forecast has publication run_class while evidence does not"
                )
            if elig_pub and not fc_pub:
                notes.append("evidence is eligible but forecast inference is non-publication")
            elig_run = str(eligibility.get("forecast_run_id") or "")
            if elig_run and run_id and elig_run != run_id:
                mismatches.append(
                    f"eligibility.forecast_run_id={elig_run} != forecast.run_id={run_id}"
                )
            elig_fp = str((eligibility.get("evidence_fingerprint") or {}).get("sha256") or "")
            if emb_fp and elig_fp and emb_fp != elig_fp:
                mismatches.append("evidence_fingerprint mismatch between forecast and eligibility artifact")
            # Soft note when eligibility lacks fingerprint (pre-upgrade artifacts)
            if not elig_fp:
                notes.append("eligibility artifact lacks evidence_fingerprint (regenerate)")
            # Embedded vs standalone publishable must match when both present
            if emb_elig and "publishable" in emb_elig:
                if bool(emb_elig.get("publishable")) != elig_pub:
                    mismatches.append(
                        "embedded forecast.evidence_eligibility.publishable disagrees with "
                        "evidence_eligibility_latest.json"
                    )

    if rebuild is not None:
        reb_run = str(rebuild.get("run_id") or "")
        if reb_run and run_id and reb_run != run_id:
            mismatches.append(
                f"independent_rebuild run_id={reb_run} != forecast.run_id={run_id}"
            )
        if rebuild.get("ok") is False:
            mismatches.append("independent_rebuild_latest.json reports ok=False")

    # Live lock: research_only must stay research_only while PUBLIC_LIVE_ENABLED=False
    surface = str(forecast.get("publication_surface") or "")
    if not PUBLIC_LIVE_ENABLED and surface == "live":
        mismatches.append(
            "forecast publication_surface=live while PUBLIC_LIVE_ENABLED=False"
        )
    if publication and not PUBLIC_LIVE_ENABLED:
        pub_surface = str(publication.get("publication_surface") or publication.get("surface") or "")
        if pub_surface == "live":
            notes.append(
                "publication_latest.json still describes a live surface; "
                "research_only containment is active — do not treat as current live auth"
            )

    live_ok = PUBLIC_LIVE_ENABLED is False  # this pass: lock must hold
    if PUBLIC_LIVE_ENABLED:
        notes.append("PUBLIC_LIVE_ENABLED=True — live publish path unlocked in config")

    ok = len(mismatches) == 0
    report = {
        "ok": ok,
        "mismatches": mismatches,
        "notes": notes,
        "forecast_run_id": run_id,
        "snapshot_id": snapshot_id or None,
        "model_version": model_version or MODEL_VERSION,
        "forecast_publishable": bool(forecast.get("publishable")),
        "PUBLIC_LIVE_ENABLED": PUBLIC_LIVE_ENABLED,
        "live_locked": live_ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return report


def write_coherence_report(*, artifacts_dir: Path | None = None) -> dict[str, Any]:
    art_dir = artifacts_dir or ARTIFACTS_DIR
    art_dir.mkdir(parents=True, exist_ok=True)
    report = check_run_coherence(artifacts_dir=art_dir)
    path = art_dir / "run_coherence_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report
