"""Freeze-before-truth OOS grid: static pymc vs pymc_dynamic vs state_space vs ridge."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from scipy.stats import norm

from midterms.baselines.models import RaceForecast
from midterms.baselines.score import score_forecasts
from midterms.config import ARTIFACTS_DIR
from midterms.evidence.score_targets import truth_margin_map
from midterms.evidence.warehouse import Warehouse
from midterms.model.challengers import fit_ridge_fundamentals
from midterms.model.pymc_model import fit_pymc, fit_pymc_dynamic
from midterms.model.state_space import fit_state_space


def _gb(snap) -> float:
    if not len(snap.polls):
        return 0.0
    m = snap.polls.merge(snap.races[["race_id", "prior_lean"]], on="race_id", how="left")
    return float((m["two_party_margin"] - m["prior_lean"]).mean())


def _to_forecasts(race_ids, means, sds) -> list[RaceForecast]:
    out: list[RaceForecast] = []
    for rid, mu, sd in zip(race_ids, means, sds):
        sd_f = float(max(sd, 0.5))
        out.append(
            RaceForecast(
                race_id=str(rid),
                state=str(rid).split("-")[-1] if "-" in str(rid) else "",
                mean_margin=float(mu),
                sd=sd_f,
                p_dem=float(norm.sf(0, loc=float(mu), scale=sd_f)),
            )
        )
    return out


def main() -> None:
    wh = Warehouse(ensure_fixtures=False)
    years = (2018, 2020, 2022, 2024)
    leads = (90, 60, 30, 14, 7)
    draws, tune, chains = 120, 120, 2
    n_ss = 800
    out: dict = {
        "protocol": "freeze_before_truth",
        "draws": draws,
        "tune": tune,
        "chains": chains,
        "years": list(years),
        "leads": list(leads),
        "by_year": {},
        "mean_crps": {},
        "note": (
            "Light inference for tractable multi-cycle grid; production stacking "
            "should still be re-fit from nested_component_loo when feasible."
        ),
    }
    acc: dict[str, list[float]] = {}

    for year in years:
        eid = f"senate-{year}"
        races = wh.races[wh.races["election_id"] == eid]
        if races.empty:
            print("skip", year, flush=True)
            continue
        ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
        frozen: dict[str, dict] = {}
        for lead in leads:
            as_of = ed - timedelta(days=lead)
            snap = wh.build_as_of(as_of, eid)
            gb = _gb(snap)
            print(f"freeze {year} lead={lead} as_of={as_of}", flush=True)
            block: dict = {}
            try:
                st = fit_pymc(
                    snap,
                    draws=draws,
                    tune=tune,
                    chains=chains,
                    seed=21 + year + lead,
                    generic_ballot=gb,
                )
                block["pymc"] = {
                    "means": st.mean_margin.tolist(),
                    "sds": st.sd_margin.tolist(),
                    "race_ids": st.race_ids,
                    "latent_path": (st.diagnostics or {}).get("latent_path"),
                }
            except Exception as exc:  # noqa: BLE001
                block["pymc"] = {"error": str(exc)}
            try:
                dy = fit_pymc_dynamic(
                    snap,
                    draws=draws,
                    tune=tune,
                    chains=chains,
                    seed=31 + year + lead,
                    generic_ballot=gb,
                )
                block["pymc_dynamic"] = {
                    "means": dy.mean_margin.tolist(),
                    "sds": dy.sd_margin.tolist(),
                    "race_ids": dy.race_ids,
                    "latent_path": (dy.diagnostics or {}).get("latent_path"),
                    "n_weeks": (dy.diagnostics or {}).get("n_weeks"),
                    "terminal_budget": ((dy.diagnostics or {}).get("error_budget") or {}).get(
                        "terminal_budget"
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                block["pymc_dynamic"] = {"error": str(exc)}
            try:
                ss = fit_state_space(
                    snap, n_draws=n_ss, seed=41 + year + lead, generic_ballot=gb
                )
                block["state_space"] = {
                    "means": ss.mean_margin.tolist(),
                    "sds": ss.sd_margin.tolist(),
                    "race_ids": ss.race_ids,
                }
            except Exception as exc:  # noqa: BLE001
                block["state_space"] = {"error": str(exc)}
            try:
                rf = fit_ridge_fundamentals(
                    snap, n_draws=n_ss, seed=51 + year + lead, generic_ballot=gb
                )
                block["ridge_fundamentals"] = {
                    "means": rf.mean_margin.tolist(),
                    "sds": rf.sd_margin.tolist(),
                    "race_ids": rf.race_ids,
                }
            except Exception as exc:  # noqa: BLE001
                block["ridge_fundamentals"] = {"error": str(exc)}
            frozen[str(lead)] = block

        results = wh.results[wh.results["election_id"] == eid]
        truth = truth_margin_map(results)
        year_scores: dict = {}
        for lead, block in frozen.items():
            lead_sc: dict = {}
            for name, payload in block.items():
                if "error" in payload:
                    lead_sc[name] = {"status": "failed", "error": payload["error"]}
                    continue
                fcs = [
                    f
                    for f in _to_forecasts(payload["race_ids"], payload["means"], payload["sds"])
                    if f.race_id in truth
                ]
                sc = score_forecasts(fcs, results)
                lead_sc[name] = sc
                if sc.get("n") and np.isfinite(sc.get("crps", np.nan)):
                    acc.setdefault(name, []).append(float(sc["crps"]))
            year_scores[lead] = lead_sc
            print(
                f"scored {year} lead={lead}",
                {k: (v.get("crps") if isinstance(v, dict) else None) for k, v in lead_sc.items()},
                flush=True,
            )
        out["by_year"][str(year)] = year_scores

    out["mean_crps"] = {k: float(np.mean(v)) for k, v in acc.items() if v}
    crps = out["mean_crps"]
    if crps:
        names = list(crps.keys())
        x = np.array([-crps[n] for n in names], dtype=float)
        x = x - x.max()
        w = np.exp(x / 0.75)
        w = w / w.sum()
        out["stack_weights_from_mean_crps"] = {n: float(wi) for n, wi in zip(names, w)}

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "dynamic_core_oos_grid.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("WROTE", path)
    print("mean_crps", out["mean_crps"])
    print("stack", out.get("stack_weights_from_mean_crps"))


if __name__ == "__main__":
    main()
