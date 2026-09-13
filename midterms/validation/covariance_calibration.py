"""Nested calibration of terminal + similarity scales (audit P1.3)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from midterms.baselines.score import score_chamber_draws, score_forecasts
from midterms.config import ARTIFACTS_DIR
from midterms.evidence.warehouse import Warehouse
from midterms.model import terminal as terminal_mod
from midterms.model.pymc_model import fit_fast_approximation
from midterms.model.terminal import active_scales
from midterms.simulate.chamber import simulate_chamber, vp_tiebreak_for_election_year
from midterms.validation.cycle_replay import _forecasts_from_fit, _realized_chamber
from midterms.validation.metrics import score_margins_extended


# Compact curated grid (~15 cells) — not a full factorial explosion.
COVERAGE_TOLERANCE = 0.03  # max allowed drop in coverage_90 vs baseline


def _grid_configs() -> list[dict[str, float]]:
    base = active_scales()
    configs: list[dict[str, float]] = [dict(base)]
    curated = [
        {**base, "terminal_nat_sd": 1.4},
        {**base, "terminal_nat_sd": 2.2},
        {**base, "terminal_race_sd": 1.0},
        {**base, "terminal_race_sd": 1.8},
        {**base, "sim_scale": 0.6},
        {**base, "sim_scale": 1.4},
        {**base, "length_scale": 1.25},
        {**base, "length_scale": 2.25},
        {**base, "terminal_nat_sd": 1.4, "sim_scale": 1.4},
        {**base, "terminal_nat_sd": 2.2, "sim_scale": 0.6},
        {**base, "terminal_race_sd": 1.0, "sim_scale": 1.4},
        {**base, "terminal_nat_sd": 1.6, "terminal_race_sd": 1.2, "sim_scale": 1.2},
        {**base, "terminal_nat_sd": 2.0, "terminal_race_sd": 1.6, "sim_scale": 0.8},
        {**base, "terminal_nat_sd": 1.5, "terminal_race_sd": 1.5, "sim_scale": 1.0, "length_scale": 1.5},
    ]
    seen = {tuple(sorted(configs[0].items()))}
    for cfg in curated:
        key = tuple(sorted(cfg.items()))
        if key not in seen:
            seen.add(key)
            configs.append(cfg)
    return configs


def _score_config(
    *,
    scales: dict[str, float],
    years: tuple[int, ...],
    lead_days: tuple[int, ...],
    n_draws: int,
    seed: int,
) -> dict[str, Any]:
    wh = Warehouse()
    race_crps: list[float] = []
    seat_crps: list[float] = []
    control_brier: list[float] = []
    coverage: list[float] = []

    for year in years:
        election_id = f"senate-{year}"
        races = wh.races[wh.races["election_id"] == election_id]
        if races.empty:
            continue
        ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
        results = wh.results[wh.results["election_id"] == election_id]
        if results.empty:
            continue
        vp = vp_tiebreak_for_election_year(year)
        realized_seats, realized_ctl = _realized_chamber(
            races, results, vp_tiebreak_party=vp
        )
        for lead in lead_days:
            as_of = ed - timedelta(days=lead)
            snap = wh.build_as_of(as_of, election_id)
            gb = 0.0
            if len(snap.polls):
                merged = snap.polls.merge(
                    snap.races[["race_id", "prior_lean"]], on="race_id", how="left"
                )
                gb = float((merged["two_party_margin"] - merged["prior_lean"]).mean())
            fit = fit_fast_approximation(
                snap,
                n_draws=n_draws,
                seed=seed + year + lead,
                generic_ballot=gb,
                terminal_scales=scales,
            )
            sc = score_forecasts(_forecasts_from_fit(fit), results)
            if sc.get("n"):
                race_crps.append(float(sc["crps"]))
            # Coverage / reliability on contested margins
            by_res = results.set_index("race_id")
            means, sds, ys = [], [], []
            for i, rid in enumerate(fit.race_ids):
                if rid not in by_res.index:
                    continue
                means.append(float(fit.mean_margin[i]))
                sds.append(float(max(fit.sd_margin[i], 0.5)))
                ys.append(float(by_res.loc[rid, "two_party_margin"]))
            if means:
                ext = score_margins_extended(
                    np.asarray(means), np.asarray(sds), np.asarray(ys)
                )
                if ext.get("n"):
                    coverage.append(float(ext["coverage_90"]))
            sim, _ = simulate_chamber(fit, snap.races, vp_tiebreak_party=vp)
            ch = score_chamber_draws(
                sim.seat_draws,
                realized_dem_seats=realized_seats,
                realized_dem_control=realized_ctl,
                vp_tiebreak_party=vp,
            )
            seat_crps.append(float(ch["seat_crps"]))
            control_brier.append(float(ch["control_brier"]))

    def _mean(xs: list[float]) -> float:
        return float(np.mean(xs)) if xs else float("nan")

    return {
        "scales": scales,
        "n_folds": len(seat_crps),
        "mean_race_crps": _mean(race_crps),
        "mean_seat_crps": _mean(seat_crps),
        "mean_control_brier": _mean(control_brier),
        "mean_coverage_90": _mean(coverage),
        # Chamber objective: prefer lower seat CRPS + control Brier
        "chamber_objective": _mean(seat_crps) + _mean(control_brier),
    }


def run_covariance_calibration(
    *,
    years: tuple[int, ...] = (2018, 2020, 2022, 2024),
    lead_days: tuple[int, ...] = (60, 30),
    n_draws: int = 800,
    seed: int = 13,
    apply_defaults: bool = True,
    out_path: Path | None = None,
    max_configs: int | None = None,
) -> dict[str, Any]:
    """
    Nested scale grid on fast spine. Winner must improve chamber_objective vs
    baseline without coverage_90 dropping by more than COVERAGE_TOLERANCE.
    """
    configs = _grid_configs()
    if max_configs is not None:
        configs = configs[: max(1, int(max_configs))]

    results: list[dict[str, Any]] = []
    for i, cfg in enumerate(configs):
        row = _score_config(
            scales=cfg,
            years=years,
            lead_days=lead_days,
            n_draws=n_draws,
            seed=seed,
        )
        results.append(row)
        print(
            f"[cov-cal] {i + 1}/{len(configs)} obj={row['chamber_objective']:.4f} "
            f"cov90={row['mean_coverage_90']:.3f}",
            flush=True,
        )

    baseline = results[0]
    base_obj = baseline["chamber_objective"]
    base_cov = baseline["mean_coverage_90"]
    eligible: list[dict[str, Any]] = []
    for row in results[1:]:
        obj = row["chamber_objective"]
        cov = row["mean_coverage_90"]
        if obj != obj or base_obj != base_obj:
            continue
        if obj >= base_obj:
            continue
        if cov == cov and base_cov == base_cov and (base_cov - cov) > COVERAGE_TOLERANCE:
            continue
        eligible.append(row)

    winner = None
    if eligible:
        winner = min(eligible, key=lambda r: r["chamber_objective"])
    gate_passed = winner is not None

    applied = False
    if gate_passed and apply_defaults and winner is not None:
        terminal_mod.set_defaults_from_calibration(winner["scales"])
        applied = True

    report = {
        "audit_item": "P1.3",
        "years": list(years),
        "lead_days": list(lead_days),
        "n_draws": n_draws,
        "coverage_tolerance": COVERAGE_TOLERANCE,
        "n_configs": len(results),
        "baseline": baseline,
        "candidates": results,
        "eligible_count": len(eligible),
        "winner": winner,
        "gate_passed": gate_passed,
        "defaults_applied": applied,
        "active_scales_after": active_scales(),
        "exit_condition": (
            "Multivariate/chamber scores improve without reliability loss "
            "(coverage_90 drop ≤ tolerance)."
        ),
        "note": (
            "Fast-spine nested grid; winning scales used as production terminal "
            "defaults for pymc / pymc_dynamic / fast."
        ),
    }
    out_path = out_path or (ARTIFACTS_DIR / "covariance_calibration.json")
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    # Stamp path before write so on-disk artifact includes it
    report["path"] = str(out_path)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    return report
