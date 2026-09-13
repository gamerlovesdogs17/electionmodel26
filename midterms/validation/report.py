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
    if stack_path.exists():
        try:
            stack_weights = json.loads(stack_path.read_text()).get("stack_weights")
        except (json.JSONDecodeError, OSError):
            stack_weights = None

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "primary_holdout": PRIMARY_HOLDOUT,
        "quick": quick,
        "lead_time_grid": lead,
        "component_ablations": abl,
        "nested_df_era": nested,
        "cycle_replay": {
            "stack_weights": (cycle or {}).get("stack_weights"),
            "chamber": (cycle or {}).get("chamber"),
            "poll_gate": (cycle or {}).get("poll_gate"),
            "aggregate_keys": list(((cycle or {}).get("aggregate") or {}).keys()),
            "error": (cycle or {}).get("error"),
        },
        "calibration": calibration,
        "stack_weights_artifact": stack_weights,
        "limitations": [
            "VoteHub documents no /polls/archive — historical polls prefer FTE CC BY Datasette.",
            "Licensed Cook/IE feeds are not redistributed; Wikipedia multi-rater is production ratings.",
            "House / Electoral College intentionally out of scope.",
            "fast hierarchical-t is a non-production approximation; production prefers pymc / ensemble_stack.",
        ],
    }
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
        "## Limitations",
        "",
    ]
    for lim in report.get("limitations") or []:
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines)
