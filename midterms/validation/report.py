"""Publish validation report artifact (blueprint §12.1 / Appendix B)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, PRIMARY_HOLDOUT

STATUS_SCHEMA_VERSION = "validation-report-current-status-v1"
CANONICAL_JSON_HASH_MODE = "canonical_json_sha256_v1"
CURRENT_REPORT_LIMITATIONS = [
    "VoteHub documents no /polls/archive — historical polls prefer FTE CC BY (Wayback/sealed polls-page).",
    "Licensed Cook/IE feeds are not redistributed; Wikipedia multi-rater is production ratings.",
    "House / Electoral College intentionally out of scope.",
    "fast hierarchical-t is a non-production approximation; production prefers pymc / ensemble_stack.",
    "Peer Brier/CRPS is an integrity diagnostic only — peers are never averaged into the ensemble.",
    "Pre-P0.3 cycle_replay artifacts may be non-comparable (wrong historical race universe / synthetic polls).",
    "Chamber reconcile uses the 2014–2024 official-ballot/certified-margin archive; the poll-coverage production gate remains 2018–2024.",
    "P2.1 nested-component-loo freezes before truth and prohibits model-identity weight remapping.",
    "PUBLIC_LIVE_ENABLED remains false; live publication requires a separate explicit future review.",
]


def build_validation_report(
    *,
    quick: bool = True,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    """
    Assemble lead-time grid + component ablation + nested df/era + calibration
    into one published report. `quick=True` uses fewer draws for CI friendliness.
    """
    from midterms.baselines.score import (
        score_forecasts,  # noqa: F401 — reserved for future
    )
    from midterms.evidence.warehouse import Warehouse
    from midterms.model.pymc_model import fit_fast_approximation
    from midterms.validation.ablations import run_component_ablations
    from midterms.validation.cycle_replay import replay_cycle
    from midterms.validation.lead_time_grid import (
        nested_df_era_search,
        replay_lead_time_grid,
    )
    from midterms.validation.metrics import (
        interval_score_gaussian,
        reliability_bins,
        score_margins_extended,
    )

    draws = 150 if quick else 400
    lead = replay_lead_time_grid(
        year=PRIMARY_HOLDOUT, lead_days=(90, 60, 30, 7), draws=draws
    )
    abl = run_component_ablations(year=PRIMARY_HOLDOUT, lead_days=60, draws=draws)
    nested = nested_df_era_search(
        holdout_year=PRIMARY_HOLDOUT, draws=max(100, draws // 2)
    )

    cycle = None
    try:
        cycle = replay_cycle(
            PRIMARY_HOLDOUT,
            lead_days=(60, 30),
            n_draws=max(200, draws),
            # Canonical hierarchical predictions, chamber scores, and stack
            # evidence were already frozen by the OOF/joint-score stages.  Do
            # not launch a second legacy PyMC fit with default poll structure
            # while merely assembling their validation report.
            include_hierarchical=False,
            include_chamber=False,
            include_challengers=False,
            allow_synthetic=allow_synthetic,
        )
    except ValueError as exc:
        cycle = {"error": str(exc), "allow_synthetic": allow_synthetic}

    # Calibration / interval scores at 60-day lead on primary holdout
    calibration: dict[str, Any] = {}
    try:
        from datetime import date, timedelta

        wh = Warehouse()
        election_id = f"senate-{PRIMARY_HOLDOUT}"
        races = wh.races[wh.races["election_id"] == election_id]
        ed = date.fromisoformat(str(races["election_day"].iloc[0]))
        snap = wh.build_as_of(ed - timedelta(days=60), election_id)
        results = wh.results[wh.results["election_id"] == election_id]
        from midterms.evidence.score_targets import truth_margin_map

        truth_map = truth_margin_map(results)
        fit = fit_fast_approximation(
            snap, n_draws=max(300, draws), seed=PRIMARY_HOLDOUT
        )
        probs = []
        outcomes = []
        interval_scores = []
        means_scored = []
        sds_scored = []
        ys_scored = []
        for i, rid in enumerate(fit.race_ids):
            if rid not in truth_map:
                continue
            y = float(truth_map[rid])
            mu = float(fit.mean_margin[i])
            sd = float(max(fit.sd_margin[i], 0.5))
            from scipy.stats import norm

            p = float(norm.sf(0, loc=mu, scale=sd))
            probs.append(p)
            outcomes.append(1.0 if y >= 0 else 0.0)
            interval_scores.append(interval_score_gaussian(y, mu, sd))
            means_scored.append(mu)
            sds_scored.append(sd)
            ys_scored.append(y)
        if probs:
            calibration = {
                "n": len(probs),
                "validation_role": "exploratory_same_holdout_diagnostic",
                "formal_acceptance_eligible": False,
                "reliability": reliability_bins(np.array(probs), np.array(outcomes)),
                "mean_interval_score_90": float(np.mean(interval_scores)),
                "brier": float(np.mean((np.array(probs) - np.array(outcomes)) ** 2)),
            }
            try:
                calibration["margin_scores"] = score_margins_extended(
                    np.array(means_scored),
                    np.array(sds_scored),
                    np.array(ys_scored),
                )
            except Exception as exc:  # noqa: BLE001
                calibration["margin_scores_error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        calibration = {"error": str(exc)}

    # Production weights are fitted from the canonical frozen OOF artifact.
    # ``cycle_replay_all`` is a legacy diagnostic and must not be presented as
    # the current production-stack source.
    stack_path = ARTIFACTS_DIR / "stack_weights_oof.json"
    stack_weights = None
    stack_meta: dict[str, Any] = {}
    if stack_path.exists():
        try:
            stack_doc = json.loads(stack_path.read_text())
            stack_weights = stack_doc.get("stack_weights") or stack_doc.get(
                "stack_weights_production"
            )
            stack_meta = {
                "source_nested_loo": stack_doc.get("source_nested_loo"),
                "source_nested_loo_sha256": stack_doc.get("source_nested_loo_sha256"),
                "validation_phase": stack_doc.get("source_validation_phase"),
                "reproduction_ok": (stack_doc.get("reproduction") or {}).get("ok"),
            }
        except (json.JSONDecodeError, OSError):
            stack_weights = None

    chamber_reconcile = None
    try:
        from midterms.validation.chamber_reconcile import reconcile_all_cycles

        chamber_reconcile = reconcile_all_cycles()
    except Exception as exc:  # noqa: BLE001
        chamber_reconcile = {"ok": False, "error": str(exc)}

    poll_coverage = None
    try:
        from midterms.validation.poll_coverage import write_poll_coverage_report

        poll_coverage = write_poll_coverage_report()
    except Exception as exc:  # noqa: BLE001
        poll_coverage = {"ok": False, "error": str(exc)}

    nested_loo = None
    nested_loo_path = ARTIFACTS_DIR / "nested_component_loo_canonical.json"
    if not nested_loo_path.exists():
        nested_loo_path = ARTIFACTS_DIR / "nested_component_loo.json"
    if nested_loo_path.exists():
        try:
            nested_loo = json.loads(nested_loo_path.read_text())
            nested_loo = {
                "path": str(nested_loo_path),
                "audit_item": nested_loo.get("audit_item"),
                "spine_label": nested_loo.get("spine_label"),
                "freeze_before_truth": nested_loo.get("freeze_before_truth"),
                "no_weight_remapping": nested_loo.get("no_weight_remapping"),
                "mean_crps_by_component": nested_loo.get("mean_crps_by_component"),
                "g8_recommendations": nested_loo.get("g8_recommendations"),
                "n_failures": len(nested_loo.get("failures") or []),
            }
        except Exception as exc:  # noqa: BLE001
            nested_loo = {"ok": False, "error": str(exc)}

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "primary_holdout": PRIMARY_HOLDOUT,
        "quick": quick,
        "lead_time_grid": lead,
        "component_ablations": abl,
        "nested_df_era": nested,
        "nested_component_loo": nested_loo,
        "cycle_replay": {
            "stack_weights": (cycle or {}).get("stack_weights"),
            "chamber": (cycle or {}).get("chamber"),
            "poll_gate": (cycle or {}).get("poll_gate"),
            "aggregate_keys": list(((cycle or {}).get("aggregate") or {}).keys()),
            "error": (cycle or {}).get("error"),
            "comparable": (cycle or {}).get("comparable"),
            "validation_status": (cycle or {}).get("validation_status"),
        },
        "calibration": calibration,
        "joint_proper_scores": (
            json.loads((ARTIFACTS_DIR / "joint_oof_scores_latest.json").read_text())
            if (ARTIFACTS_DIR / "joint_oof_scores_latest.json").exists()
            else {
                "status": "requires_rebuild",
                "capability": "joint-proper-scores-v1",
                "reason": "historical frozen joint draws have not been rescored",
            }
        ),
        "stack_weights_artifact": stack_weights,
        "stack_weights_meta": stack_meta,
        "chamber_reconcile": chamber_reconcile,
        "poll_coverage": poll_coverage,
        "peer_gate": None,
        "acceptance_gates": None,
        "limitations": list(CURRENT_REPORT_LIMITATIONS),
    }
    try:
        from midterms.validation.leave_pollster_out import leave_pollster_out

        report["leave_pollster_out"] = leave_pollster_out(
            year=PRIMARY_HOLDOUT, lead_days=60, draws=max(150, draws // 2), top_n=3
        )
    except Exception as exc:  # noqa: BLE001
        report["leave_pollster_out"] = {"ok": False, "error": str(exc)}
    try:
        from midterms.validation.peer_gate import write_peer_gate_report

        report["peer_gate"] = write_peer_gate_report()
    except Exception as exc:  # noqa: BLE001
        report["peer_gate"] = {"ok": False, "error": str(exc)}
    try:
        from midterms.validation.acceptance_gates import evaluate_acceptance_gates

        report["acceptance_gates"] = evaluate_acceptance_gates(write=True)
    except Exception as exc:  # noqa: BLE001
        report["acceptance_gates"] = {"ok": False, "error": str(exc)}
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "validation_report_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path = ARTIFACTS_DIR / "validation_report_latest.md"
    md_path.write_text(_to_markdown(report), encoding="utf-8")
    report["path"] = str(path)
    report["md_path"] = str(md_path)
    return report


def _read_required_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing required current artifact: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid current artifact {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"current artifact {path.name} is not a JSON object")
    return payload


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def refresh_validation_report_status(
    *,
    artifacts_dir: str | Path = ARTIFACTS_DIR,
    release_identity_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh current status/lineage without recomputing statistical diagnostics.

    The expensive validation sections are retained byte-for-byte as parsed JSON.
    Only the embedded acceptance result and an explicit current-status block are
    replaced from the final authoritative artifacts produced later in the
    rebuild workflow.
    """
    art_dir = Path(artifacts_dir)
    report_path = art_dir / "validation_report_latest.json"
    report = _read_required_json(report_path)
    sources = {
        "acceptance_gates": _read_required_json(
            art_dir / "acceptance_gates_latest.json"
        ),
        "run_coherence": _read_required_json(art_dir / "run_coherence_latest.json"),
        "forecast": _read_required_json(art_dir / "forecast_latest.json"),
        "evidence_eligibility": _read_required_json(
            art_dir / "evidence_eligibility_latest.json"
        ),
        "validated_model_spec": _read_required_json(
            art_dir / "validated_model_spec_latest.json"
        ),
        "stack_weights": _read_required_json(art_dir / "stack_weights_oof.json"),
        "stack_reliability": _read_required_json(
            art_dir / "stack_reliability_crossfit_latest.json"
        ),
    }
    acceptance = sources["acceptance_gates"]
    coherence = sources["run_coherence"]
    forecast = sources["forecast"]
    eligibility = sources["evidence_eligibility"]
    spec = sources["validated_model_spec"]
    stack = sources["stack_weights"]
    reliability = sources["stack_reliability"]

    if release_identity_report is None:
        from midterms.ops.release_identity import verify_release_identity

        release_identity_report = verify_release_identity(
            expected_model_version=str(forecast.get("model_version") or MODEL_VERSION),
            artifacts_dir=art_dir,
        )
    release_identity = dict(release_identity_report)
    release_lineage = release_identity.get("lineage") or {}

    problems: list[str] = []
    model_versions = {
        str(value)
        for value in (
            report.get("model_version"),
            acceptance.get("model_version"),
            coherence.get("model_version"),
            forecast.get("model_version"),
            eligibility.get("model_version"),
            spec.get("model_version"),
            stack.get("model_version") or stack.get("source_model_version"),
            reliability.get("model_version"),
            release_identity.get("model_version"),
        )
        if value
    }
    if model_versions != {MODEL_VERSION}:
        problems.append(f"model version disagreement: {sorted(model_versions)}")

    bundle_id = spec.get("evidence_bundle_id")
    bundle_sha = spec.get("evidence_bundle_sha256")
    if not bundle_id or forecast.get("evidence_bundle_id") != bundle_id:
        problems.append("forecast and validated spec evidence bundle IDs differ")
    if forecast.get("evidence_bundle_sha256") != bundle_sha:
        problems.append("forecast and validated spec evidence bundle hashes differ")
    if release_lineage.get("evidence_bundle_id") != bundle_id:
        problems.append("release identity and validated spec evidence bundles differ")
    if release_lineage.get("evidence_bundle_sha256") != bundle_sha:
        problems.append("release identity and validated spec bundle hashes differ")

    spec_sha = spec.get("spec_sha256")
    if not spec_sha or forecast.get("validated_model_spec_sha256") != spec_sha:
        problems.append("forecast and validated model spec hashes differ")
    if release_lineage.get("validated_model_spec_sha256") != spec_sha:
        problems.append("release identity and validated model spec hashes differ")

    forecast_run_id = forecast.get("run_id")
    if coherence.get("forecast_run_id") != forecast_run_id:
        problems.append("run coherence is not bound to the current forecast run")
    if eligibility.get("forecast_run_id") != forecast_run_id:
        problems.append("evidence eligibility is not bound to the current forecast run")
    if coherence.get("forecast_publishable") != forecast.get("publishable"):
        problems.append("forecast and coherence publication eligibility differ")
    if eligibility.get("publishable") != forecast.get("publishable"):
        problems.append("forecast and evidence publication eligibility differ")

    embedded_release = (
        ((acceptance.get("gates") or {}).get("G10") or {}).get("detail") or {}
    ).get("release_identity") or {}
    if embedded_release.get("release_id") != release_identity.get("release_id"):
        problems.append("acceptance and current release identities differ")
    if bool(embedded_release.get("ok")) != bool(release_identity.get("ok")):
        problems.append("acceptance and current release verification differ")
    if not release_identity.get("ok"):
        problems.append("current release identity does not verify")

    public_live = coherence.get("PUBLIC_LIVE_ENABLED")
    if (
        public_live is not False
        or release_identity.get("PUBLIC_LIVE_ENABLED") is not False
    ):
        problems.append("PUBLIC_LIVE_ENABLED must remain false")
    surface = forecast.get("publication_surface")
    if (
        surface != "research_only"
        or release_identity.get("publication_surface") != surface
    ):
        problems.append("publication surface is not consistently research_only")
    if problems:
        raise ValueError(
            "validation report status refresh refused: " + "; ".join(problems)
        )

    source_hashes = {
        name: _canonical_json_sha256(payload) for name, payload in sources.items()
    }
    source_hashes["release_identity"] = _canonical_json_sha256(release_identity)
    gate_statuses = {
        gate_id: gate.get("status")
        for gate_id, gate in (acceptance.get("gates") or {}).items()
    }
    raw_reliability = reliability.get("raw_production_stack") or {}
    calibration = raw_reliability.get("calibration_slope_intercept") or {}
    reliability_gate = raw_reliability.get("reliability_overconfidence") or {}
    current_status = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "refreshed_at": datetime.now(UTC).isoformat(),
        "source_hash_mode": CANONICAL_JSON_HASH_MODE,
        "source_artifact_sha256": source_hashes,
        "model_version": MODEL_VERSION,
        "forecast_run_id": forecast_run_id,
        "evidence_bundle_id": bundle_id,
        "evidence_bundle_sha256": bundle_sha,
        "validated_model_spec_sha256": spec_sha,
        "selected_structure_id": spec.get("selected_structure_id"),
        "selected_poll_structure_id": spec.get("selected_poll_structure_id"),
        "release_id": release_identity.get("release_id"),
        "release_identity_verified": True,
        "gates": gate_statuses,
        "n_pass": acceptance.get("n_pass"),
        "n_partial": acceptance.get("n_partial"),
        "n_fail": acceptance.get("n_fail"),
        "run_coherence_ok": bool(coherence.get("ok")),
        "forecast_publishable": bool(forecast.get("publishable")),
        "research_acceptance_ok": bool(acceptance.get("ok")),
        "promotion_eligible": bool(acceptance.get("promotion_ok")),
        "cycle_crossfit_reliability": {
            "n": raw_reliability.get("n"),
            "brier": raw_reliability.get("brier"),
            "calibration_slope": calibration.get("slope"),
            "calibration_intercept": calibration.get("intercept"),
            "n_overconfident_bins": reliability_gate.get("n_overconfident"),
        },
        "PUBLIC_LIVE_ENABLED": False,
        "publication_surface": surface,
    }
    report["acceptance_gates"] = acceptance
    report["stack_weights_artifact"] = stack.get("stack_weights") or stack.get(
        "stack_weights_production"
    )
    report["limitations"] = list(CURRENT_REPORT_LIMITATIONS)
    report["current_status"] = current_status
    report["model_version"] = MODEL_VERSION
    report_path.write_text(
        json.dumps(report, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path = art_dir / "validation_report_latest.md"
    md_path.write_text(_to_markdown(report), encoding="utf-8", newline="\n")
    return {**current_status, "path": str(report_path), "md_path": str(md_path)}


def _to_markdown(report: dict[str, Any]) -> str:
    status = report.get("current_status") or {}
    acceptance = report.get("acceptance_gates") or {}
    reliability = status.get("cycle_crossfit_reliability") or {}
    lines = [
        f"# Validation report — {report.get('model_version')}",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Current status refreshed: {status.get('refreshed_at')}",
        f"Primary holdout: {report.get('primary_holdout')}",
        "",
        "## Current authoritative status",
        "",
        f"- Evidence bundle: {status.get('evidence_bundle_id')}",
        f"- Validated model spec: {status.get('validated_model_spec_sha256')}",
        f"- Release identity: {status.get('release_id')}",
        f"- Release identity verified: {status.get('release_identity_verified')}",
        f"- Run coherence: {status.get('run_coherence_ok')}",
        f"- Research acceptance: {status.get('research_acceptance_ok')}",
        f"- Promotion eligible: {status.get('promotion_eligible')}",
        f"- Forecast publishable: {status.get('forecast_publishable')}",
        f"- PUBLIC_LIVE_ENABLED: {status.get('PUBLIC_LIVE_ENABLED')}",
        f"- Publication surface: {status.get('publication_surface')}",
        "",
        "## Cycle-cross-fitted production-stack reliability",
        "",
        f"- n: {reliability.get('n')}",
        f"- Brier: {reliability.get('brier')}",
        f"- Calibration slope: {reliability.get('calibration_slope')}",
        f"- Calibration intercept: {reliability.get('calibration_intercept')}",
        f"- Flagged overconfident bins: {reliability.get('n_overconfident_bins')}",
        "",
        "## Stack weights",
        "",
        "```json",
        json.dumps(
            report.get("stack_weights_artifact")
            or report.get("cycle_replay", {}).get("stack_weights"),
            indent=2,
        ),
        "```",
        "",
        "## Exploratory legacy primary-holdout diagnostic",
        "",
        f"- n: {(report.get('calibration') or {}).get('n')}",
        f"- Brier: {(report.get('calibration') or {}).get('brier')}",
        f"- Mean 90% interval score: {(report.get('calibration') or {}).get('mean_interval_score_90')}",
        "",
        "## Peer release gate",
        "",
        "```json",
        json.dumps(report.get("peer_gate"), indent=2, default=str),
        "```",
        "",
        "## Chamber reconcile (audit P0.2)",
        "",
        f"- ok: {(report.get('chamber_reconcile') or {}).get('ok')}",
        f"- failures: {(report.get('chamber_reconcile') or {}).get('failures')}",
        "",
        "## Poll coverage (audit P0.3)",
        "",
        f"- ok: {(report.get('poll_coverage') or {}).get('ok')}",
        f"- failures: {(report.get('poll_coverage') or {}).get('failures')}",
        "",
        "## Acceptance gates (Milestone-0 / G1–G11)",
        "",
        f"- ok: {acceptance.get('ok')}",
        (
            f"- pass/partial/fail: {acceptance.get('n_pass')}/"
            f"{acceptance.get('n_partial')}/{acceptance.get('n_fail')}"
        ),
        f"- failures: {acceptance.get('failures')}",
        "",
        "## Limitations",
        "",
    ]
    for lim in report.get("limitations") or []:
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines)
