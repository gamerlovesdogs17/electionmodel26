"""Complete-cycle historical replay for hierarchical model + baselines."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from midterms.baselines.models import BASELINES
from midterms.baselines.score import score_chamber_draws, score_forecasts
from midterms.config import ARTIFACTS_DIR, CYCLES, LEAD_DAYS, PRIMARY_HOLDOUT
from midterms.evidence.warehouse import Warehouse
from midterms.model.ensemble import default_weights_from_replay, softmax_neg_scores
from midterms.model.overlays import apply_rating_overlay, shift_draws_to_means
from midterms.model.pymc_model import FitResult, fit_fast_approximation
from midterms.model.poll_weights import attach_poll_weights, global_enop
from midterms.simulate.chamber import simulate_chamber, vp_tiebreak_for_election_year


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


def _realized_chamber(races, results, *, vp_tiebreak_party: str = "R") -> tuple[float, int]:
    held = races[races["not_up"]]
    held_dem = int((held["held_by"] == "D").sum()) + int((held["held_by"] == "I").sum())
    contested = races[~races["not_up"]]
    by_res = results.set_index("race_id") if len(results) else None
    wins = 0
    n = 0
    for _, r in contested.iterrows():
        rid = r["race_id"]
        if by_res is None or rid not in by_res.index:
            continue
        n += 1
        if float(by_res.loc[rid, "two_party_margin"]) >= 0:
            wins += 1
    dem_seats = held_dem + wins
    if n == 0:
        dem_seats = float(held_dem)
    if vp_tiebreak_party == "D":
        dem_control = int(dem_seats >= 50)
    else:
        dem_control = int(dem_seats >= 51)
    return float(dem_seats), dem_control


def _overlay_ablation_block(
    fit: FitResult, snap, results, *, vp_tiebreak_party: str = "R"
) -> dict[str, Any]:
    """Compare unadjusted vs soft rating overlay chamber scores (synthetic ratings OK)."""
    from midterms.model.overlays import rating_from_probability
    from scipy.stats import norm

    realized_seats, realized_ctl = _realized_chamber(
        snap.races, results, vp_tiebreak_party=vp_tiebreak_party
    )
    core_sim, _ = simulate_chamber(fit, snap.races, vp_tiebreak_party=vp_tiebreak_party)
    core_scores = score_chamber_draws(
        core_sim.seat_draws,
        realized_dem_seats=realized_seats,
        realized_dem_control=realized_ctl,
    )

    # Build soft ratings from model itself (identity) vs shifted anchors — ablation plumbing
    rows = []
    for i, rid in enumerate(fit.race_ids):
        p = float(norm.sf(0, loc=fit.mean_margin[i], scale=max(fit.sd_margin[i], 0.5)))
        rows.append(
            {
                "race_id": rid,
                "rating": rating_from_probability(min(0.98, max(0.02, p + 0.05))),
                "source": "ablation_shift",
            }
        )
    import pandas as pd

    ratings = pd.DataFrame(rows)
    adj = apply_rating_overlay(fit.mean_margin.copy(), fit.race_ids, ratings, weight=0.15)
    shifted = shift_draws_to_means(fit.draws_margin, adj)
    adj_fit = FitResult(
        race_ids=fit.race_ids,
        states=fit.states,
        mean_margin=shifted.mean(axis=0),
        sd_margin=shifted.std(axis=0),
        draws_margin=shifted,
        house_effects=fit.house_effects,
        diagnostics=fit.diagnostics,
        method=fit.method + "+rating_ablation",
    )
    adj_sim, _ = simulate_chamber(adj_fit, snap.races, vp_tiebreak_party=vp_tiebreak_party)
    adj_scores = score_chamber_draws(
        adj_sim.seat_draws,
        realized_dem_seats=realized_seats,
        realized_dem_control=realized_ctl,
    )
    return {
        "unadjusted": core_scores,
        "rating_overlay": adj_scores,
        "delta_control_brier": float(
            adj_scores["control_brier"] - core_scores["control_brier"]
        ),
        "delta_seat_crps": float(adj_scores["seat_crps"] - core_scores["seat_crps"]),
    }


def replay_cycle(
    holdout_year: int = PRIMARY_HOLDOUT,
    *,
    lead_days: tuple[int, ...] = LEAD_DAYS,
    n_draws: int = 1200,
    seed: int = 2022,
    include_hierarchical: bool = True,
    include_chamber: bool = True,
    include_overlay_ablation: bool = True,
) -> dict[str, Any]:
    """
    Hold out one complete Senate cycle; score baselines + hierarchical model
    at fixed historical lead times using as-of snapshots only.
    """
    wh = Warehouse()
    election_id = f"senate-{holdout_year}"
    vp = vp_tiebreak_for_election_year(holdout_year)
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        raise ValueError(f"no races for {election_id}")
    ed = date.fromisoformat(str(races["election_day"].iloc[0]))
    results = wh.results[wh.results["election_id"] == election_id]

    models = dict(BASELINES)
    report: dict[str, Any] = {
        "election_id": election_id,
        "holdout_year": holdout_year,
        "vp_tiebreak_party": vp,
        "lead_days": {},
        "aggregate": {},
        "enop": {},
        "chamber": {},
        "overlay_ablation": {},
    }
    keys = list(models.keys()) + (["fast_hierarchical_t"] if include_hierarchical else [])
    agg_scores: dict[str, list] = {k: [] for k in keys}
    chamber_scores: list[dict[str, float]] = []

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

            if include_chamber:
                sim, _ = simulate_chamber(fit, snap.races, vp_tiebreak_party=vp)
                realized_seats, realized_ctl = _realized_chamber(
                    snap.races, results, vp_tiebreak_party=vp
                )
                c_scores = score_chamber_draws(
                    sim.seat_draws,
                    realized_dem_seats=realized_seats,
                    realized_dem_control=realized_ctl,
                )
                lead_block["chamber"] = c_scores
                chamber_scores.append(c_scores)
                if include_overlay_ablation:
                    lead_block["overlay_ablation"] = _overlay_ablation_block(
                        fit, snap, results, vp_tiebreak_party=vp
                    )

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

    if chamber_scores:
        report["chamber"] = {
            k: float(np.mean([s[k] for s in chamber_scores]))
            for k in chamber_scores[0]
            if k != "n_draws"
        }
        report["chamber"]["n_leads"] = len(chamber_scores)

    # Aggregate overlay ablation deltas across leads
    deltas = []
    for lead_block in report["lead_days"].values():
        ab = lead_block.get("overlay_ablation")
        if ab:
            deltas.append(ab)
    if deltas:
        report["overlay_ablation"] = {
            "mean_delta_control_brier": float(
                np.mean([d["delta_control_brier"] for d in deltas])
            ),
            "mean_delta_seat_crps": float(np.mean([d["delta_seat_crps"] for d in deltas])),
            "n_leads": len(deltas),
            "note": "Positive delta_control_brier means overlay worsened Brier vs unadjusted.",
        }

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
                "chamber": reports[y].get("chamber"),
                "overlay_ablation": reports[y].get("overlay_ablation"),
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
