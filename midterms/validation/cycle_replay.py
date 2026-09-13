"""Complete-cycle historical replay for hierarchical model + baselines."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from midterms.baselines.models import BASELINES
from midterms.baselines.score import score_forecasts
from midterms.config import ARTIFACTS_DIR, CYCLES, LEAD_DAYS, PRIMARY_HOLDOUT
from midterms.evidence.warehouse import Warehouse
from midterms.model.ensemble import default_weights_from_replay, softmax_neg_scores
from midterms.model.pymc_model import fit_fast_approximation
from midterms.model.poll_weights import attach_poll_weights, global_enop


def _forecasts_from_fit(fit) -> list:
    from midterms.baselines.models import RaceForecast
    from scipy.stats import norm

    out = []
    for i, rid in enumerate(fit.race_ids):
        mu = float(fit.mean_margin[i])
        sd = float(max(fit.sd_margin[i], 0.5))
        out.append(
            RaceForecast(
                race_id=rid,
                state=fit.states[i],
                mean_margin=mu,
                sd=sd,
                p_dem=float(norm.sf(0, loc=mu, scale=sd)),
            )
        )
    return out


def replay_cycle(
    holdout_year: int = PRIMARY_HOLDOUT,
    *,
    lead_days: tuple[int, ...] = LEAD_DAYS,
    n_draws: int = 1200,
    seed: int = 2022,
    include_hierarchical: bool = True,
) -> dict[str, Any]:
    """
    Hold out one complete Senate cycle; score baselines + hierarchical model
    at fixed historical lead times using as-of snapshots only.
    """
    wh = Warehouse()
    election_id = f"senate-{holdout_year}"
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        raise ValueError(f"no races for {election_id}")
    ed = date.fromisoformat(str(races["election_day"].iloc[0]))
    results = wh.results[wh.results["election_id"] == election_id]

    models = dict(BASELINES)
    report: dict[str, Any] = {
        "election_id": election_id,
        "holdout_year": holdout_year,
        "lead_days": {},
        "aggregate": {},
        "enop": {},
    }
    keys = list(models.keys()) + (["fast_hierarchical_t"] if include_hierarchical else [])
    agg_scores: dict[str, list] = {k: [] for k in keys}

    for lead in lead_days:
        as_of = ed - timedelta(days=lead)
        snap = wh.build_as_of(as_of, election_id)
        weighted = attach_poll_weights(snap.polls, as_of=snap.as_of)
        report["enop"][str(lead)] = {
            "n_polls": int(len(snap.polls)),
            "enop_global": global_enop(weighted),
        }
        lead_block: dict[str, Any] = {}
        for name, fn in models.items():
            forecasts = fn(snap)
            scores = score_forecasts(forecasts, results)
            lead_block[name] = scores
            if scores.get("n"):
                agg_scores[name].append(scores)

        if include_hierarchical:
            gb = 0.0
            if len(snap.polls):
                merged = snap.polls.merge(
                    snap.races[["race_id", "prior_lean"]], on="race_id", how="left"
                )
                gb = float((merged["two_party_margin"] - merged["prior_lean"]).mean())
            fit = fit_fast_approximation(
                snap, n_draws=n_draws, seed=seed + lead, generic_ballot=gb
            )
            forecasts = _forecasts_from_fit(fit)
            scores = score_forecasts(forecasts, results)
            lead_block["fast_hierarchical_t"] = {
                **scores,
                "enop_global": fit.diagnostics.get("enop_global"),
            }
            if scores.get("n"):
                agg_scores["fast_hierarchical_t"].append(scores)

        report["lead_days"][str(lead)] = lead_block

    for name, scores_list in agg_scores.items():
        if not scores_list:
            report["aggregate"][name] = {}
            continue
        metric_keys = [k for k in scores_list[0] if k != "n"]
        report["aggregate"][name] = {
            k: float(np.mean([s[k] for s in scores_list])) for k in metric_keys
        }
        report["aggregate"][name]["n_leads"] = len(scores_list)

    report["stack_weights"] = default_weights_from_replay(report)
    return report


def replay_all_cycles(
    years: tuple[int, ...] | None = None,
    *,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Leave-one-cycle-out style reports for each historical Senate cycle."""
    years = years or CYCLES
    reports = {}
    crps_pool: dict[str, list[float]] = {}
    for year in years:
        try:
            rep = replay_cycle(year)
        except ValueError:
            continue
        reports[str(year)] = rep
        for name, block in (rep.get("aggregate") or {}).items():
            if "crps" in block:
                crps_pool.setdefault(name, []).append(float(block["crps"]))

    mean_crps = {k: float(np.mean(v)) for k, v in crps_pool.items() if v}
    summary = {
        "cycles": list(reports.keys()),
        "mean_crps_by_model": mean_crps,
        "stack_weights": softmax_neg_scores(mean_crps, temperature=0.75) if mean_crps else {},
        "by_cycle": {
            y: {
                "aggregate": reports[y].get("aggregate"),
                "stack_weights": reports[y].get("stack_weights"),
            }
            for y in reports
        },
    }
    out_path = out_path or (ARTIFACTS_DIR / "cycle_replay_all.json")
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    if str(PRIMARY_HOLDOUT) in reports:
        detail = ARTIFACTS_DIR / f"cycle_replay_{PRIMARY_HOLDOUT}.json"
        detail.write_text(json.dumps(reports[str(PRIMARY_HOLDOUT)], indent=2))
        summary["primary_detail_path"] = str(detail)
    summary["path"] = str(out_path)
    return summary
