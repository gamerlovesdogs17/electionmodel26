"""Nested leave-one-cycle fundamentals coefficient ablations (blueprint §5 / §10.3)."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from midterms.baselines.score import score_forecasts
from midterms.config import ARTIFACTS_DIR, CYCLES, LEAD_DAYS, PRIMARY_HOLDOUT
from midterms.evidence.warehouse import Warehouse
from midterms.model import fundamentals as fund_mod
from midterms.model.pymc_model import fit_fast_approximation
from midterms.validation.cycle_replay import _forecasts_from_fit


ABLATION_KEYS = (
    "fundraising_logit",
    "pres_approval",
    "midterm_outparty",
    "real_income_yoy",
    "incumbency",
    "generic_ballot",
)


def _mean_crps_for_coefs(
    *,
    coefs: dict[str, float],
    holdout_year: int,
    lead_days: tuple[int, ...] = (60, 30),
    n_draws: int = 600,
    seed: int = 11,
) -> float:
    """Fit hierarchical model under patched COEF; return mean race CRPS on holdout."""
    wh = Warehouse()
    election_id = f"senate-{holdout_year}"
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        return float("nan")
    ed = date.fromisoformat(str(races["election_day"].iloc[0]))
    results = wh.results[wh.results["election_id"] == election_id]
    scores: list[float] = []

    old = deepcopy(fund_mod.COEF)
    try:
        fund_mod.COEF.clear()
        fund_mod.COEF.update(coefs)
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
                snap, n_draws=n_draws, seed=seed + lead, generic_ballot=gb
            )
            sc = score_forecasts(_forecasts_from_fit(fit), results)
            if sc.get("n"):
                scores.append(float(sc["crps"]))
    finally:
        fund_mod.COEF.clear()
        fund_mod.COEF.update(old)

    return float(np.mean(scores)) if scores else float("nan")


def run_fundamentals_ablation(
    *,
    years: tuple[int, ...] | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """
    For each cycle, compare full COEF vs drop-one components on held-out CRPS.

    A component 'earns' keep status when ablating it increases (worsens) mean CRPS
    on a majority of held-out cycles.
    """
    years = years or CYCLES
    base = deepcopy(fund_mod.COEF)
    by_cycle: dict[str, Any] = {}
    keep_votes: dict[str, int] = {k: 0 for k in ABLATION_KEYS}
    n_cycles = 0

    for year in years:
        try:
            full = _mean_crps_for_coefs(coefs=base, holdout_year=year)
        except ValueError:
            continue
        if full != full:  # NaN
            continue
        n_cycles += 1
        drops: dict[str, Any] = {}
        for key in ABLATION_KEYS:
            patched = deepcopy(base)
            patched[key] = 0.0
            dropped = _mean_crps_for_coefs(coefs=patched, holdout_year=year)
            delta = float(dropped - full) if dropped == dropped else float("nan")
            drops[key] = {"crps": dropped, "delta_vs_full": delta}
            if delta == delta and delta > 0:
                keep_votes[key] += 1
        by_cycle[str(year)] = {"full_crps": full, "drop_one": drops}

    recommendations = {}
    for key, votes in keep_votes.items():
        recommendations[key] = {
            "keep_votes": votes,
            "n_cycles": n_cycles,
            "recommend": "keep" if n_cycles and votes >= max(1, (n_cycles + 1) // 2) else "drop_or_shrink",
        }

    report = {
        "base_coefs": base,
        "ablation_keys": list(ABLATION_KEYS),
        "n_cycles": n_cycles,
        "by_cycle": by_cycle,
        "recommendations": recommendations,
        "note": (
            "Positive delta_vs_full means dropping the coefficient worsened CRPS "
            "(component helps). Nested leave-one-cycle design; synthetic polls limit strength."
        ),
    }
    out_path = out_path or (ARTIFACTS_DIR / "fundamentals_ablation.json")
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    report["path"] = str(out_path)
    return report


def run_primary_holdout_ablation() -> dict[str, Any]:
    """Convenience: ablation focused on PRIMARY_HOLDOUT only (faster CI)."""
    return run_fundamentals_ablation(years=(PRIMARY_HOLDOUT,))
