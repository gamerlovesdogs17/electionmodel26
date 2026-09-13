"""Publish validation report artifact (blueprint §12.1 / Appendix B)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, PRIMARY_HOLDOUT


def build_validation_report(
    *,
    quick: bool = True,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    """
    Assemble lead-time grid + component ablation + nested df/era + calibration
    into one published report. `quick=True` uses fewer draws for CI friendliness.
    """
    from midterms.baselines.score import score_forecasts  # noqa: F401 — reserved for future
    from midterms.evidence.warehouse import Warehouse
    from midterms.model.pymc_model import fit_fast_approximation
    from midterms.validation.ablations import run_component_ablations
    from midterms.validation.cycle_replay import replay_cycle
    from midterms.validation.lead_time_grid import nested_df_era_search, replay_lead_time_grid
    from midterms.validation.metrics import (
        interval_score_gaussian,
        reliability_bins,
        score_margins_extended,
    )

    draws = 150 if quick else 400
    lead = replay_lead_time_grid(year=PRIMARY_HOLDOUT, lead_days=(90, 60, 30, 7), draws=draws)
    abl = run_component_ablations(year=PRIMARY_HOLDOUT, lead_days=60, draws=draws)
    nested = nested_df_era_search(holdout_year=PRIMARY_HOLDOUT, draws=max(100, draws // 2))

    cycle = None
    try:
        cycle = replay_cycle(
            PRIMARY_HOLDOUT,
            lead_days=(60, 30),
            n_draws=max(200, draws),
            include_challengers=True,
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
        results = wh.results[wh.results["election_id"] == election_id].set_index("race_id")
        fit = fit_fast_approximation(snap, n_draws=max(300, draws), seed=PRIMARY_HOLDOUT)
        probs = []
        outcomes = []
        interval_scores = []
        for i, rid in enumerate(fit.race_ids):
            if rid not in results.index:
                continue
            y = float(results.loc[rid, "two_party_margin"])
            mu = float(fit.mean_margin[i])
            sd = float(max(fit.sd_margin[i], 0.5))
            from scipy.stats import norm

            p = float(norm.sf(0, loc=mu, scale=sd))
            probs.append(p)
            outcomes.append(1.0 if y >= 0 else 0.0)
            interval_scores.append(interval_score_gaussian(y, mu, sd))
        if probs:
            calibration = {
                "n": len(probs),
                "reliability": reliability_bins(np.array(probs), np.array(outcomes)),
                "mean_interval_score_90": float(np.mean(interval_scores)),
                "brier": float(np.mean((np.array(probs) - np.array(outcomes)) ** 2)),
            }
            try:
                calibration["margin_scores"] = score_margins_extended(
                    fit.mean_margin,
                    fit.sd_margin,
                    np.array(
                        [
                            float(results.loc[rid, "two_party_margin"])
                            if rid in results.index
                            else np.nan
                            for rid in fit.race_ids
                        ]
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                calibration["margin_scores_error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        calibration = {"error": str(exc)}

    stack_path = ARTIFACTS_DIR / "cycle_replay_all.json"
    stack_weights = None
    stack_meta: dict[str, Any] = {}
    if stack_path.exists():
        try:
            stack_doc = json.loads(stack_path.read_text())
            stack_weights = stack_doc.get("stack_weights")
            stack_meta = {
                "comparable": stack_doc.get("comparable"),
                "validation_status": stack_doc.get("validation_status"),
                "archive_note": stack_doc.get("archive_note"),
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        "stack_weights_artifact": stack_weights,
        "stack_weights_meta": stack_meta,
        "chamber_reconcile": chamber_reconcile,
        "poll_coverage": poll_coverage,
        "peer_gate": None,
        "acceptance_gates": None,
        "limitations": [
            "VoteHub documents no /polls/archive — historical polls prefer FTE CC BY (Wayback/sealed polls-page).",
            "Licensed Cook/IE feeds are not redistributed; Wikipedia multi-rater is production ratings.",
            "House / Electoral College intentionally out of scope.",
            "fast hierarchical-t is a non-production approximation; production prefers pymc / ensemble_stack.",
            "Peer Brier/CRPS is a release gate only — peers are never averaged into the ensemble.",
            "Pre-P0.3 cycle_replay artifacts may be non-comparable (wrong historical race universe / synthetic polls).",
            "Chamber reconcile + poll coverage gates (2018–2024) are required before treating holdout scores as validated.",
            "P2.1 nested-component-loo: freeze-before-truth; no fast→pymc weight remapping.",
            "Milestone-0 archive scope is 2018–2024; 2014/2016 provisional. Public live probabilities wait on gate green + pymc shadow.",
        ],
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
    path.write_text(json.dumps(report, indent=2, default=str))
    md_path = ARTIFACTS_DIR / "validation_report_latest.md"
    md_path.write_text(_to_markdown(report))
    report["path"] = str(path)
    report["md_path"] = str(md_path)
    return report


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Validation report — {report.get('model_version')}",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Primary holdout: {report.get('primary_holdout')}",
        "",
        "## Stack weights",
        "",
        "```json",
        json.dumps(report.get("stack_weights_artifact") or report.get("cycle_replay", {}).get("stack_weights"), indent=2),
        "```",
        "",
        "## Calibration (60-day lead)",
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
        f"- ok: {(report.get('acceptance_gates') or {}).get('ok')}",
        f"- pass/partial/fail: {(report.get('acceptance_gates') or {}).get('n_pass')}/"
        f"{(report.get('acceptance_gates') or {}).get('n_partial')}/"
        f"{(report.get('acceptance_gates') or {}).get('n_fail')}",
        f"- failures: {(report.get('acceptance_gates') or {}).get('failures')}",
        "",
        "## Limitations",
        "",
    ]
    for lim in report.get("limitations") or []:
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines)
