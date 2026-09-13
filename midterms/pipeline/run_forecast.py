"""End-to-end forecast pipeline: as-of → fit → joint chamber → artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from midterms.baselines.models import BASELINES
from midterms.baselines.score import score_forecasts
from midterms.config import (
    ARTIFACTS_DIR,
    DEMO_AS_OF,
    DEMO_ELECTION_ID,
    DEMO_SEED,
    LEAD_DAYS,
    MODEL_VERSION,
    PRIMARY_HOLDOUT,
)
from midterms.evidence.warehouse import Warehouse, write_run_manifest
from midterms.model.pymc_model import fit_fast_approximation, fit_pymc
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
    out_dir: Path | None = None,
) -> dict[str, Any]:
    wh = Warehouse()
    snap = wh.build_as_of(as_of, election_id)

    if method == "pymc":
        fit = fit_pymc(
            snap, draws=draws, tune=tune, chains=chains, seed=seed, generic_ballot=generic_ballot
        )
    else:
        fit = fit_fast_approximation(snap, n_draws=max(draws * chains, 2000), seed=seed, generic_ballot=generic_ballot)

    sim, race_summaries = simulate_chamber(fit, snap.races)
    foil = independent_bernoulli_foil(race_summaries, sim.held_dem, n_draws=len(sim.seat_draws), seed=seed)
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

    artifact = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast_as_of": as_of,
        "election_id": election_id,
        "model_version": MODEL_VERSION,
        "method": fit.method,
        "snapshot": snap.to_dict(),
        "chamber": {
            "held_dem": sim.held_dem,
            "held_rep": sim.held_rep,
            "majority_threshold": sim.majority_threshold,
            "p_dem_majority": sim.p_dem_majority,
            "p_rep_majority": sim.p_rep_majority,
            "p_tie": sim.p_tie,
            "expected_dem_seats": sim.expected_dem_seats,
            "seat_histogram": seat_hist,
            "independent_bernoulli_foil_expected": float(np.mean(foil)),
            "note": "Chamber totals from joint correlated draws — not independent Bernoulli.",
        },
        "races": race_summaries,
        "baselines": baselines,
        "diagnostics": fit.diagnostics,
        "house_effects": fit.house_effects,
    }

    artifact_path = out_dir / f"forecast_{run_id}.json"
    # Also write a stable demo filename for the UI
    demo_path = out_dir / "forecast_latest.json"
    text = json.dumps(artifact, indent=2)
    artifact_path.write_text(text)
    demo_path.write_text(text)

    # Persist a compact draws sample for audit (not full for UI)
    draws_path = out_dir / f"draws_{run_id}.npz"
    np.savez_compressed(
        draws_path,
        margins=fit.draws_margin.astype(np.float32),
        dem_seats=sim.seat_draws.astype(np.int16),
        race_ids=np.array(fit.race_ids),
    )

    manifest = {
        "run_id": run_id,
        "generated_at": artifact["generated_at"],
        "forecast_as_of": as_of,
        "election_id": election_id,
        "model_version": MODEL_VERSION,
        "code_commit": _git_commit(),
        "configuration_hash": _hash_obj(
            {"method": method, "draws": draws, "tune": tune, "chains": chains, "seed": seed, "generic_ballot": generic_ballot}
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
    """As-of replay of baselines on a held-out Senate cycle."""
    from datetime import date, timedelta

    wh = Warehouse()
    election_id = f"senate-{holdout_year}"
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        raise ValueError(f"no races for {election_id}")
    ed = date.fromisoformat(str(races["election_day"].iloc[0]))
    results = wh.results[wh.results["election_id"] == election_id]

    report: dict[str, Any] = {"election_id": election_id, "lead_days": {}, "aggregate": {}}
    agg_scores: dict[str, list] = {k: [] for k in BASELINES}

    for lead in LEAD_DAYS:
        as_of = ed - timedelta(days=lead)
        snap = wh.build_as_of(as_of, election_id)
        # Score against certified results (available_at may be after as_of — labels only)
        lead_block = {}
        for name, fn in BASELINES.items():
            forecasts = fn(snap)
            scores = score_forecasts(forecasts, results)
            lead_block[name] = scores
            if scores.get("n"):
                agg_scores[name].append(scores)
        report["lead_days"][str(lead)] = lead_block

    for name, scores_list in agg_scores.items():
        if not scores_list:
            report["aggregate"][name] = {}
            continue
        keys = [k for k in scores_list[0] if k != "n"]
        report["aggregate"][name] = {
            k: float(np.mean([s[k] for s in scores_list])) for k in keys
        }
        report["aggregate"][name]["n_leads"] = len(scores_list)

    out = ARTIFACTS_DIR / f"baseline_replay_{holdout_year}.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    report["path"] = str(out)
    return report
