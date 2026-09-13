"""End-to-end forecast pipeline: as-of → fit → joint chamber → artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.baselines.models import BASELINES
from midterms.config import (
    ARTIFACTS_DIR,
    DEMO_AS_OF,
    DEMO_ELECTION_ID,
    DEMO_SEED,
    MODEL_VERSION,
    PRIMARY_HOLDOUT,
)
from midterms.evidence.warehouse import Warehouse, write_run_manifest
from midterms.model.pymc_model import (
    FitResult,
    draws_from_baseline_forecasts,
    fit_fast_approximation,
    fit_pymc,
)
from midterms.simulate.chamber import independent_bernoulli_foil, simulate_chamber


def _git_commit() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        return "unknown"


def _hash_obj(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _load_stack_weights() -> dict[str, float]:
    path = ARTIFACTS_DIR / "cycle_replay_all.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            weights = payload.get("stack_weights") or {}
            if weights:
                return {k: float(v) for k, v in weights.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    # Default: trust hierarchical core; keep shrinkage as light challenger
    return {
        "fast_hierarchical_t": 0.70,
        "shrinkage_polls": 0.20,
        "last_election_swing": 0.10,
    }


def run_forecast(
    *,
    election_id: str = DEMO_ELECTION_ID,
    as_of: str = DEMO_AS_OF,
    method: str = "fast",
    draws: int = 400,
    tune: int = 400,
    chains: int = 2,
    seed: int = DEMO_SEED,
    generic_ballot: float = -1.0,
    ensemble: bool = True,
    with_ratings: bool = False,
    with_markets: bool = False,
    rating_weight: float = 0.15,
    market_weight: float = 0.10,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    from midterms.evidence.economics import write_economic_store, yoy_growth_as_of
    from midterms.evidence.fec import attach_fundraising_to_races, write_finance_store
    from midterms.model.overlays import (
        apply_market_overlay,
        apply_rating_overlay,
        markets_from_probabilities,
        overlay_report,
        rating_from_probability,
        shift_draws_to_means,
    )

    # Ensure economic + finance stores exist (fixtures if APIs unavailable)
    write_economic_store()
    try:
        write_finance_store(election_id=election_id)
    except Exception:  # noqa: BLE001
        pass

    wh = Warehouse()
    snap = wh.build_as_of(as_of, election_id)
    # Overlay FEC fundraising shares onto race rows used by fundamentals
    snap.races = attach_fundraising_to_races(snap.races)
    year = None
    try:
        year = int(str(snap.races["election_day"].iloc[0])[:4])
    except Exception:  # noqa: BLE001
        year = None
    yoy = yoy_growth_as_of(as_of, election_year=year)
    if yoy is not None:
        snap.races = snap.races.copy()
        snap.races["real_income_yoy"] = yoy

    if method == "pymc":
        fit = fit_pymc(
            snap, draws=draws, tune=tune, chains=chains, seed=seed, generic_ballot=generic_ballot
        )
    else:
        fit = fit_fast_approximation(
            snap, n_draws=max(draws * chains, 2000), seed=seed, generic_ballot=generic_ballot
        )

    stack_weights = None
    if ensemble and method == "fast":
        weights = _load_stack_weights()
        component_draws: dict[str, np.ndarray] = {"fast_hierarchical_t": fit.draws_margin}
        for name in ("shrinkage_polls", "last_election_swing", "equal_weight_polls"):
            if name in weights and name in BASELINES:
                bl = BASELINES[name](snap)
                component_draws[name] = draws_from_baseline_forecasts(
                    bl,
                    n_draws=fit.draws_margin.shape[0],
                    seed=seed + hash(name) % 10_000,
                    race_ids=fit.race_ids,
                )
        use_w = {k: v for k, v in weights.items() if k in component_draws and v > 0}
        if len(use_w) >= 2:
            means = {n: component_draws[n].mean(axis=0) for n in use_w}
            sds = {n: component_draws[n].std(axis=0) for n in use_w}
            blended_mean = sum(use_w[n] * means[n] for n in use_w)
            blended_sd = sum(use_w[n] * sds[n] for n in use_w)
            core = component_draws["fast_hierarchical_t"]
            core_mean = core.mean(axis=0)
            core_sd = np.maximum(core.std(axis=0), 1e-6)
            shocks = (core - core_mean) * (blended_sd / core_sd)
            stacked = blended_mean + shocks
            fit = FitResult(
                race_ids=fit.race_ids,
                states=fit.states,
                mean_margin=stacked.mean(axis=0),
                sd_margin=stacked.std(axis=0),
                draws_margin=stacked,
                house_effects=fit.house_effects,
                diagnostics={
                    **fit.diagnostics,
                    "ensemble": True,
                    "stack_weights": use_w,
                    "ensemble_note": "mean/sd stack + hierarchical joint shocks",
                },
                method="ensemble_stack",
            )
            stack_weights = use_w

    # Optional ablatable overlays (applied to means; joint shocks preserved)
    unadjusted_means = fit.mean_margin.copy()
    adj = unadjusted_means.copy()
    if with_ratings:
        ratings = pd.DataFrame(
            {
                "race_id": fit.race_ids,
                "rating": [rating_from_probability(float(1 / (1 + np.exp(-m / 5.0)))) for m in adj],
            }
        )
        adj = apply_rating_overlay(adj, fit.race_ids, ratings, weight=rating_weight)
    if with_markets:
        markets = markets_from_probabilities(
            [
                {
                    "race_id": rid,
                    "p_dem": float(1 / (1 + np.exp(-m / 5.0))),
                }
                for rid, m in zip(fit.race_ids, adj)
            ]
        )
        adj = apply_market_overlay(adj, fit.race_ids, markets, weight=market_weight)
    if with_ratings or with_markets:
        shifted = shift_draws_to_means(fit.draws_margin, adj)
        fit = FitResult(
            race_ids=fit.race_ids,
            states=fit.states,
            mean_margin=shifted.mean(axis=0),
            sd_margin=shifted.std(axis=0),
            draws_margin=shifted,
            house_effects=fit.house_effects,
            diagnostics={
                **fit.diagnostics,
                "overlays": overlay_report(
                    used_ratings=with_ratings,
                    used_markets=with_markets,
                    rating_weight=rating_weight if with_ratings else 0.0,
                    market_weight=market_weight if with_markets else 0.0,
                ),
                "mean_shift_mae": float(np.mean(np.abs(adj - unadjusted_means))),
            },
            method=fit.method + ("+overlays" if not fit.method.endswith("+overlays") else ""),
        )

    sim, race_summaries = simulate_chamber(fit, snap.races, vp_tiebreak_party="R")
    foil = independent_bernoulli_foil(
        race_summaries, sim.held_dem, n_draws=len(sim.seat_draws), seed=seed
    )
    baselines = {}
    for name, fn in BASELINES.items():
        forecasts = fn(snap)
        baselines[name] = [f.as_dict() for f in forecasts]

    out_dir = out_dir or ARTIFACTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{election_id}_{as_of}_{fit.method}_{seed}"

    seat_hist = [
        {"dem_seats": int(k), "count": int(v), "probability": v / len(sim.seat_draws)}
        for k, v in sorted(sim.seat_histogram.items(), key=lambda kv: int(kv[0]))
    ]
    expected_rep = 100.0 - sim.expected_dem_seats

    artifact = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast_as_of": as_of,
        "election_id": election_id,
        "model_version": MODEL_VERSION,
        "method": fit.method,
        "snapshot": {
            **snap.to_dict(),
            "real_income_yoy": yoy,
        },
        "chamber": {
            "held_dem": sim.held_dem,
            "held_rep": sim.held_rep,
            "majority_threshold": sim.majority_threshold,
            "p_dem_majority": sim.p_dem_majority,
            "p_rep_majority": sim.p_rep_majority,
            "p_fifty_fifty": sim.p_fifty_fifty,
            "p_tie": sim.p_fifty_fifty,  # backward-compatible alias
            "vp_tiebreak_party": sim.vp_tiebreak_party,
            "expected_dem_seats": sim.expected_dem_seats,
            "expected_rep_seats": expected_rep,
            "seat_histogram": seat_hist,
            "independent_bernoulli_foil_expected": float(np.mean(foil)),
            "note": (
                "Chamber totals from joint correlated draws. "
                "50–50 counts as Republican control (VP tiebreak)."
            ),
        },
        "races": race_summaries,
        "baselines": baselines,
        "diagnostics": fit.diagnostics,
        "house_effects": fit.house_effects,
        "stack_weights": stack_weights,
        "overlays": overlay_report(
            used_ratings=with_ratings,
            used_markets=with_markets,
            rating_weight=rating_weight if with_ratings else 0.0,
            market_weight=market_weight if with_markets else 0.0,
        ),
    }

    artifact_path = out_dir / f"forecast_{run_id}.json"
    demo_path = out_dir / "forecast_latest.json"
    text = json.dumps(artifact, indent=2)
    artifact_path.write_text(text)
    demo_path.write_text(text)

    draws_path = out_dir / f"draws_{run_id}.npz"
    np.savez_compressed(
        draws_path,
        margins=fit.draws_margin.astype(np.float32),
        dem_seats=sim.seat_draws.astype(np.int16),
        race_ids=np.array(fit.race_ids),
    )

    web_copy = Path(__file__).resolve().parents[2] / "web" / "public" / "data" / "forecast_latest.json"
    if out_dir.resolve() == ARTIFACTS_DIR.resolve():
        web_copy.parent.mkdir(parents=True, exist_ok=True)
        web_copy.write_text(text)

    manifest = {
        "run_id": run_id,
        "generated_at": artifact["generated_at"],
        "forecast_as_of": as_of,
        "election_id": election_id,
        "model_version": MODEL_VERSION,
        "code_commit": _git_commit(),
        "configuration_hash": _hash_obj(
            {
                "method": method,
                "draws": draws,
                "tune": tune,
                "chains": chains,
                "seed": seed,
                "generic_ballot": generic_ballot,
                "ensemble": ensemble,
                "stack_weights": stack_weights,
                "with_ratings": with_ratings,
                "with_markets": with_markets,
            }
        ),
        "snapshot_ids": {"evidence": snap.snapshot_id},
        "seed": seed,
        "draws": fit.diagnostics.get("draws"),
        "tune": tune if method == "pymc" else None,
        "chains": chains if method == "pymc" else None,
        "output_hashes": {
            "forecast_json": hashlib.sha256(text.encode()).hexdigest(),
            "draws": hashlib.sha256(draws_path.read_bytes()).hexdigest(),
        },
        "paths": {
            "forecast": str(artifact_path),
            "forecast_latest": str(demo_path),
            "draws": str(draws_path),
        },
    }
    write_run_manifest(manifest)
    return {"artifact": artifact, "manifest": manifest, "paths": manifest["paths"]}


def replay_baselines(holdout_year: int = PRIMARY_HOLDOUT) -> dict[str, Any]:
    """As-of replay of baselines on a held-out Senate cycle (legacy entrypoint)."""
    from midterms.validation.cycle_replay import replay_cycle

    report = replay_cycle(holdout_year, include_hierarchical=False)
    out = ARTIFACTS_DIR / f"baseline_replay_{holdout_year}.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    # Strip hierarchical-only fields for backward-compatible baseline artifact
    slim = {
        "election_id": report["election_id"],
        "lead_days": {
            k: {n: s for n, s in v.items() if n in BASELINES}
            for k, v in report["lead_days"].items()
        },
        "aggregate": {n: report["aggregate"][n] for n in BASELINES if n in report["aggregate"]},
        "path": str(out),
    }
    out.write_text(json.dumps(slim, indent=2))
    slim["path"] = str(out)
    return slim
