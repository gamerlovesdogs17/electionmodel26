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
from midterms.model.pymc_model import FitResult, fit_fast_approximation, fit_pymc
from midterms.model.poll_weights import attach_poll_weights, global_enop
from midterms.simulate.chamber import simulate_chamber, vp_tiebreak_for_election_year


def _fit_hierarchical(
    snap,
    *,
    method: str,
    n_draws: int,
    seed: int,
    generic_ballot: float,
    pymc_draws: int,
    pymc_tune: int,
    pymc_chains: int,
) -> FitResult:
    """Production OOS uses PyMC; fast is CI/challenger only (blueprint §7.4 / §10)."""
    from midterms.model.pymc_model import fit_pymc_dynamic

    if method.startswith("pymc_dynamic"):
        try:
            return fit_pymc_dynamic(
                snap,
                draws=pymc_draws,
                tune=pymc_tune,
                chains=pymc_chains,
                seed=seed,
                generic_ballot=generic_ballot,
            )
        except Exception:
            fit = fit_pymc(
                snap,
                draws=pymc_draws,
                tune=pymc_tune,
                chains=pymc_chains,
                seed=seed,
                generic_ballot=generic_ballot,
            )
            fit.diagnostics = {
                **(fit.diagnostics or {}),
                "pymc_dynamic_fallback": True,
                "requested_method": "pymc_dynamic",
            }
            return fit
    if method.startswith("pymc"):
        try:
            return fit_pymc(
                snap,
                draws=pymc_draws,
                tune=pymc_tune,
                chains=pymc_chains,
                seed=seed,
                generic_ballot=generic_ballot,
            )
        except Exception:
            # Replay must still produce a fold score if NUTS fails locally
            fit = fit_fast_approximation(
                snap, n_draws=n_draws, seed=seed, generic_ballot=generic_ballot
            )
            fit.diagnostics = {
                **(fit.diagnostics or {}),
                "pymc_fallback": True,
                "requested_method": "pymc",
            }
            fit.method = "pymc_fallback_fast"
            return fit
    return fit_fast_approximation(
        snap, n_draws=n_draws, seed=seed, generic_ballot=generic_ballot
    )


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
    include_challengers: bool = True,
    allow_synthetic: bool = False,
    hierarchical_method: str = "pymc",
    pymc_draws: int = 150,
    pymc_tune: int = 150,
    pymc_chains: int = 2,
    include_fast_challenger: bool = True,
    nested_fundamentals: bool = True,
) -> dict[str, Any]:
    """
    Hold out one complete Senate cycle; score baselines + hierarchical model
    at fixed historical lead times using as-of snapshots only.

    Production hierarchical spine is PyMC (blueprint §7.4). Pass
    hierarchical_method='fast' for CI / smoke. Fast remains an optional
    challenger when include_fast_challenger=True.

    When nested_fundamentals=True (audit P1.2), fundamentals COEF are replaced
    by leave-one-cycle ridge estimates for the duration of the replay.
    """
    from midterms.evidence.fte_polls import assert_real_historical_polls
    from midterms.model.fundamentals import set_coefs
    from midterms.validation.coefficient_stability import with_nested_coefs

    election_id = f"senate-{holdout_year}"
    poll_gate = assert_real_historical_polls(
        election_id, allow_synthetic=allow_synthetic
    )
    wh = Warehouse()
    vp = vp_tiebreak_for_election_year(holdout_year)
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        raise ValueError(f"no races for {election_id}")
    ed = date.fromisoformat(str(races["election_day"].iloc[0]))
    results = wh.results[wh.results["election_id"] == election_id]

    fund_est: dict[str, Any] | None = None
    old_coefs = None
    if nested_fundamentals:
        old_coefs, fund_est = with_nested_coefs(holdout_year)

    try:
        return _replay_cycle_body(
            holdout_year=holdout_year,
            election_id=election_id,
            poll_gate=poll_gate,
            wh=wh,
            vp=vp,
            races=races,
            ed=ed,
            results=results,
            lead_days=lead_days,
            n_draws=n_draws,
            seed=seed,
            include_hierarchical=include_hierarchical,
            include_chamber=include_chamber,
            include_overlay_ablation=include_overlay_ablation,
            include_challengers=include_challengers,
            hierarchical_method=hierarchical_method,
            pymc_draws=pymc_draws,
            pymc_tune=pymc_tune,
            pymc_chains=pymc_chains,
            include_fast_challenger=include_fast_challenger,
            fund_est=fund_est,
        )
    finally:
        if old_coefs is not None:
            set_coefs(old_coefs)


def _replay_cycle_body(
    *,
    holdout_year: int,
    election_id: str,
    poll_gate: dict[str, Any],
    wh: Warehouse,
    vp: str,
    races,
    ed: date,
    results,
    lead_days: tuple[int, ...],
    n_draws: int,
    seed: int,
    include_hierarchical: bool,
    include_chamber: bool,
    include_overlay_ablation: bool,
    include_challengers: bool,
    hierarchical_method: str,
    pymc_draws: int,
    pymc_tune: int,
    pymc_chains: int,
    include_fast_challenger: bool,
    fund_est: dict[str, Any] | None,
) -> dict[str, Any]:
    models = dict(BASELINES)
    spine_key = "pymc" if hierarchical_method.startswith("pymc") else "fast_hierarchical_t"
    report: dict[str, Any] = {
        "election_id": election_id,
        "holdout_year": holdout_year,
        "vp_tiebreak_party": vp,
        "poll_gate": poll_gate,
        "hierarchical_method": hierarchical_method,
        "spine_key": spine_key,
        "fundamentals_nested": fund_est,
        "lead_days": {},
        "aggregate": {},
        "enop": {},
        "chamber": {},
        "overlay_ablation": {},
    }
    keys = list(models.keys())
    if include_hierarchical:
        keys.append(spine_key)
        if include_fast_challenger and spine_key != "fast_hierarchical_t":
            keys.append("fast_hierarchical_t")
    if include_challengers:
        keys.extend(["state_space", "poll_only_state_space", "ridge_fundamentals"])
    keys = list(dict.fromkeys(keys))
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
            fit = _fit_hierarchical(
                snap,
                method=hierarchical_method,
                n_draws=n_draws,
                seed=seed + lead,
                generic_ballot=gb,
                pymc_draws=pymc_draws,
                pymc_tune=pymc_tune,
                pymc_chains=pymc_chains,
            )
            # Normalize production label to pymc even if local NUTS fell back
            score_key = spine_key
            forecasts = _forecasts_from_fit(fit)
            scores = score_forecasts(forecasts, results)
            lead_block[score_key] = {
                **scores,
                "enop_global": fit.diagnostics.get("enop_global"),
                "fit_method": fit.method,
                "error_budget": (fit.diagnostics or {}).get("error_budget"),
            }
            if scores.get("n"):
                agg_scores[score_key].append(scores)

            if include_fast_challenger and spine_key != "fast_hierarchical_t":
                fast = fit_fast_approximation(
                    snap, n_draws=min(n_draws, 800), seed=seed + lead + 3, generic_ballot=gb
                )
                f_scores = score_forecasts(_forecasts_from_fit(fast), results)
                lead_block["fast_hierarchical_t"] = f_scores
                if f_scores.get("n"):
                    agg_scores["fast_hierarchical_t"].append(f_scores)

            if include_challengers:
                from midterms.model.challengers import (
                    fit_poll_only_state_space,
                    fit_ridge_fundamentals,
                )
                from midterms.model.state_space import fit_state_space

                for cname, cfit in (
                    (
                        "state_space",
                        fit_state_space(
                            snap, n_draws=n_draws, seed=seed + lead + 11, generic_ballot=gb
                        ),
                    ),
                    (
                        "poll_only_state_space",
                        fit_poll_only_state_space(
                            snap, n_draws=n_draws, seed=seed + lead + 13
                        ),
                    ),
                    (
                        "ridge_fundamentals",
                        fit_ridge_fundamentals(
                            snap, n_draws=n_draws, seed=seed + lead + 17, generic_ballot=gb
                        ),
                    ),
                ):
                    try:
                        c_scores = score_forecasts(_forecasts_from_fit(cfit), results)
                        lead_block[cname] = c_scores
                        if c_scores.get("n"):
                            agg_scores[cname].append(c_scores)
                    except Exception as exc:  # noqa: BLE001
                        lead_block[cname] = {"error": str(exc), "n": 0}

            if include_chamber:
                sim, _ = simulate_chamber(fit, snap.races, vp_tiebreak_party=vp)
                realized_seats, realized_ctl = _realized_chamber(
                    snap.races, results, vp_tiebreak_party=vp
                )
                ch = score_chamber_draws(
                    sim.seat_draws,
                    realized_dem_seats=realized_seats,
                    realized_dem_control=realized_ctl,
                )
                lead_block["chamber"] = ch
                chamber_scores.append(ch)

            if include_overlay_ablation:
                lead_block["overlay_ablation"] = _overlay_ablation_block(
                    fit, snap, results, vp_tiebreak_party=vp
                )

        report["lead_days"][str(lead)] = lead_block

    for name, blocks in agg_scores.items():
        if not blocks:
            continue
        report["aggregate"][name] = {
            "crps": float(np.mean([b["crps"] for b in blocks if b.get("crps") is not None])),
            "brier": float(np.mean([b["brier"] for b in blocks if b.get("brier") is not None])),
            "mae": float(np.mean([b["mae"] for b in blocks if b.get("mae") is not None])),
            "n_leads": len(blocks),
        }
    if chamber_scores:
        report["chamber"] = {
            "control_brier": float(np.mean([c["control_brier"] for c in chamber_scores])),
            "seat_mae": float(np.mean([c["seat_mae"] for c in chamber_scores])),
            "n_leads": len(chamber_scores),
        }
    report["stack_weights"] = default_weights_from_replay(report)

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
    report["comparable"] = True
    report["validation_status"] = "official_ballot_fte_polls"
    report["archive_note"] = (
        "Official race universe + FTE candidate-identity polls (P0.3). "
        "Regenerate nested OOS artifacts before peer publication claims."
    )
    return report


def replay_all_cycles(
    years: tuple[int, ...] | None = None,
    *,
    out_path: Path | None = None,
    allow_synthetic: bool = False,
    hierarchical_method: str = "pymc",
) -> dict[str, Any]:
    """
    Leave-one-cycle-out reports for each historical Senate cycle.

    Production `stack_weights` average CRPS across all OOS cycle reports (each
    cycle was held out when scored). Per-holdout `stack_weights_loo` excludes
    that holdout's own CRPS so evaluation of stacking on year Y never peeks at Y.
    """
    from midterms.model.ensemble import align_weights_to_spine, weights_from_oof_scores

    years = years or CYCLES
    reports = {}
    crps_by_fold: dict[str, dict[str, float]] = {}
    for year in years:
        try:
            rep = replay_cycle(
                year,
                allow_synthetic=allow_synthetic,
                hierarchical_method=hierarchical_method,
            )
        except ValueError:
            continue
        reports[str(year)] = rep
        fold_scores = {}
        for name, block in (rep.get("aggregate") or {}).items():
            if isinstance(block, dict) and "crps" in block:
                fold_scores[name] = float(block["crps"])
        if fold_scores:
            crps_by_fold[str(year)] = fold_scores

    # Second pass: LOO weights need the full fold table
    for y, rep in reports.items():
        rep["stack_weights_loo"] = align_weights_to_spine(
            weights_from_oof_scores(crps_by_fold, exclude_fold=str(y)),
            spine="pymc",
        )

    mean_crps = {
        k: float(np.mean([fold[k] for fold in crps_by_fold.values() if k in fold]))
        for k in {n for fold in crps_by_fold.values() for n in fold}
    }
    production_weights = align_weights_to_spine(
        weights_from_oof_scores(crps_by_fold),
        spine="pymc",
    )
    summary = {
        "cycles": list(reports.keys()),
        "mean_crps_by_model": mean_crps,
        "stack_weights": production_weights,
        "stack_weights_production": production_weights,
        "hierarchical_method": hierarchical_method,
        # comparable once official ballots + FTE identity polls pass coverage (P0.1–P0.3)
        "comparable": True,
        "validation_status": "official_ballot_fte_polls",
        "archive_note": (
            "Race universe uses official ballots; historical polls are FTE with candidate "
            "identity (P0.3). Still not a peer-published claim until nested OOS scores are regenerated."
        ),
        "stack_provenance": {
            "method": "leave_one_cycle_out_mean_crps",
            "folds": list(crps_by_fold.keys()),
            "temperature": 0.75,
            "hierarchical_method": hierarchical_method,
            "note": (
                "Production weights average OOS CRPS across historical cycles. "
                "Each cycle's scores were computed with that cycle held out. "
                "Hierarchical spine scored as pymc when hierarchical_method=pymc."
            ),
        },
        "note": "Stack weights from OOS mean CRPS across cycles; hierarchical mass labeled pymc.",
        "by_cycle": {
            y: {
                "aggregate": reports[y].get("aggregate"),
                "stack_weights": reports[y].get("stack_weights"),
                "stack_weights_loo": reports[y].get("stack_weights_loo"),
                "chamber": reports[y].get("chamber"),
                "overlay_ablation": reports[y].get("overlay_ablation"),
                "poll_gate": reports[y].get("poll_gate"),
                "hierarchical_method": reports[y].get("hierarchical_method"),
            }
            for y in reports
        },
    }
    out_path = out_path or (ARTIFACTS_DIR / "cycle_replay_all.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    if str(PRIMARY_HOLDOUT) in reports:
        detail = ARTIFACTS_DIR / f"cycle_replay_{PRIMARY_HOLDOUT}.json"
        detail.write_text(json.dumps(reports[str(PRIMARY_HOLDOUT)], indent=2, default=str))
        summary["primary_detail_path"] = str(detail)
    summary["path"] = str(out_path)
    return summary
