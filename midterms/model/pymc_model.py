"""Hierarchical Bayesian latent-opinion model for Senate margins (PyMC).

Economist / Linzer-style spine:
- Election-Day latent margin per race with national + region + state hierarchy
- Fundamentals prior as ED anchor
- Poll measurement with hierarchical house effects, Student-t overdispersion,
  influence weights (ENOP / pollster caps / study clustering), and mode/population shifts
- Future-movement noise from as-of to Election Day (Morris-style split)
- Industrywide terminal bias that does not vanish on Election Day
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.fundamentals import fundamentals_mean
from midterms.model.poll_weights import attach_poll_weights, global_enop, race_enop_summary
from midterms.model.similarity import correlated_shocks
from midterms.evidence.schema import is_active_ballot_row


@dataclass
class FitResult:
    race_ids: list[str]
    states: list[str]
    mean_margin: np.ndarray
    sd_margin: np.ndarray
    draws_margin: np.ndarray  # shape (n_draws, n_races)
    house_effects: dict[str, float]
    diagnostics: dict
    method: str


def _mode_offset(mode: object) -> float:
    if mode is None or (isinstance(mode, float) and np.isnan(mode)):
        return 0.0
    m = str(mode).lower()
    if "live" in m:
        return 0.0
    if "ivr" in m:
        return -0.4
    if "online" in m or "web" in m:
        return 0.3
    return 0.0


def _population_offset(population: object) -> float:
    if population is None or (isinstance(population, float) and np.isnan(population)):
        return 0.0
    p = str(population).upper()
    if "LV" in p:
        return 0.0
    if "RV" in p:
        return 0.5
    return 0.8


def _prepare(snapshot: EvidenceSnapshot, generic_ballot: float = 0.0):
    races = snapshot.races.copy()
    if len(races):
        races = races[races.apply(is_active_ballot_row, axis=1)].reset_index(drop=True)
    polls = snapshot.polls.copy()
    polls = polls[polls["race_id"].isin(set(races["race_id"]))]
    polls = attach_poll_weights(polls, as_of=snapshot.as_of)

    real_income_yoy = None
    try:
        from midterms.evidence.economics import yoy_growth_as_of

        year = int(str(races["election_day"].iloc[0])[:4]) if len(races) else None
        real_income_yoy = yoy_growth_as_of(snapshot.as_of, election_year=year)
    except Exception:  # noqa: BLE001
        real_income_yoy = None

    fund = fundamentals_mean(
        snapshot.races,
        generic_ballot=generic_ballot,
        real_income_yoy=real_income_yoy,
    )
    race_ids = races["race_id"].tolist()
    race_index = {r: i for i, r in enumerate(race_ids)}
    regions = sorted(races["region"].unique())
    region_index = {g: i for i, g in enumerate(regions)}

    ed = date.fromisoformat(str(races["election_day"].iloc[0]))
    days_to_ed = max((ed - snapshot.as_of).days, 1)

    pollster_ids = sorted(polls["pollster_id"].unique()) if len(polls) else []
    pollster_index = {p: i for i, p in enumerate(pollster_ids)}

    if len(polls):
        poll_race = polls["race_id"].map(race_index).astype(int).to_numpy()
        poll_house = polls["pollster_id"].map(pollster_index).astype(int).to_numpy()
        # Adjust observed margins for mode/population prior offsets before likelihood
        mode_adj = polls["mode"].astype(str).map(_mode_offset).to_numpy() if "mode" in polls.columns else 0.0
        pop_adj = (
            polls["population"].astype(str).map(_population_offset).to_numpy()
            if "population" in polls.columns
            else 0.0
        )
        poll_y = polls["two_party_margin"].astype(float).to_numpy() - mode_adj - pop_adj
        n = polls["sample_size"].astype(float).clip(lower=50)
        qw = (
            polls["quality_weight"].astype(float)
            if "quality_weight" in polls.columns
            else pd.Series(np.ones(len(polls)))
        ).clip(0.2, 1.0)
        # Inflate SE when influence weight is low (down-weighted / capped polls)
        iw = polls["influence_weight"].astype(float).clip(0.05, 3.0).to_numpy()
        poll_se = (100.0 / np.sqrt(n) / np.sqrt(qw.to_numpy()) / np.sqrt(iw)).astype(float)
        house_prior = (
            polls.groupby("pollster_id")["house_effect_prior"].mean()
            if "house_effect_prior" in polls.columns
            else pd.Series(dtype=float)
        )
        extra_prior = (
            polls.groupby("pollster_id")["extra_sd_prior"].mean()
            if "extra_sd_prior" in polls.columns
            else pd.Series(dtype=float)
        )
        house_mu = np.array([float(house_prior.get(p, 0.0)) for p in pollster_ids])
        extra_mu = np.array([float(extra_prior.get(p, 2.2)) for p in pollster_ids])
        influence = iw
    else:
        poll_race = np.zeros(0, dtype=int)
        poll_house = np.zeros(0, dtype=int)
        poll_y = np.zeros(0)
        poll_se = np.zeros(0)
        house_mu = np.zeros(0)
        extra_mu = np.zeros(0)
        influence = np.zeros(0)

    prior_mu = np.array([float(fund.loc[r]) for r in race_ids])
    region_idx = races["region"].map(region_index).astype(int).to_numpy()
    return {
        "races": races,
        "race_ids": race_ids,
        "states": races["state"].tolist(),
        "prior_mu": prior_mu,
        "region_idx": region_idx,
        "n_regions": len(regions),
        "poll_race": poll_race,
        "poll_house": poll_house,
        "poll_y": poll_y,
        "poll_se": poll_se,
        "influence": influence,
        "n_pollsters": len(pollster_ids),
        "pollster_ids": pollster_ids,
        "house_mu": house_mu,
        "extra_mu": extra_mu,
        "days_to_ed": days_to_ed,
        "enop_global": global_enop(polls),
        "enop_by_race": race_enop_summary(polls),
        "n_polls_raw": int(len(snapshot.polls)),
        "n_polls_weighted": int(len(polls)),
    }


def fit_pymc(
    snapshot: EvidenceSnapshot,
    *,
    draws: int = 400,
    tune: int = 400,
    chains: int = 2,
    seed: int = 20260901,
    generic_ballot: float = 0.0,
) -> FitResult:
    import pymc as pm

    prep = _prepare(snapshot, generic_ballot=generic_ballot)
    n_races = len(prep["race_ids"])
    future_sd = 1.2 * np.sqrt(prep["days_to_ed"] / 30.0)

    coords = {
        "race": prep["race_ids"],
        "region": list(range(prep["n_regions"])),
        "pollster": prep["pollster_ids"] or ["_none"],
    }

    with pm.Model(coords=coords) as model:  # noqa: F841
        sigma_nat = pm.HalfNormal("sigma_nat", 3.0)
        national = pm.StudentT("national", nu=4, mu=0.0, sigma=sigma_nat)
        sigma_region = pm.HalfNormal("sigma_region", 2.0)
        region_eff = pm.StudentT("region_eff", nu=5, mu=0.0, sigma=sigma_region, dims="region")
        sigma_local = pm.HalfNormal("sigma_local", 3.0)
        local = pm.StudentT("local", nu=5, mu=0.0, sigma=sigma_local, dims="race")

        mu_ed = pm.Deterministic(
            "mu_ed",
            prep["prior_mu"] + national + region_eff[prep["region_idx"]] + local,
            dims="race",
        )

        future = pm.StudentT("future", nu=5, mu=0.0, sigma=future_sd, dims="race")
        theta_now = pm.Deterministic("theta_now", mu_ed - future, dims="race")

        if prep["n_pollsters"]:
            house = pm.Normal("house", mu=prep["house_mu"], sigma=1.0, dims="pollster")
            log_extra = pm.Normal(
                "log_extra",
                mu=np.log(np.clip(prep["extra_mu"], 1.0, 4.5)),
                sigma=0.35,
                dims="pollster",
            )
            extra = pm.Deterministic("extra_sd", pm.math.exp(log_extra), dims="pollster")
        else:
            house = pm.Normal("house", 0.0, 1.5, shape=1)
            extra = pm.Deterministic(
                "extra_sd", pm.math.exp(pm.Normal("log_extra", -0.2, 0.4, shape=1))
            )

        if len(prep["poll_y"]):
            sigma_obs = pm.math.sqrt(prep["poll_se"] ** 2 + extra[prep["poll_house"]] ** 2)
            pm.StudentT(
                "polls",
                nu=5,
                mu=theta_now[prep["poll_race"]] + house[prep["poll_house"]],
                sigma=sigma_obs,
                observed=prep["poll_y"],
            )

        terminal_bias = pm.StudentT("terminal_bias", nu=4, mu=0.0, sigma=1.5)
        mu_final = pm.Deterministic("mu_final", mu_ed + terminal_bias, dims="race")

        idata = pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            random_seed=seed,
            target_accept=0.9,
            progressbar=False,
            return_inferencedata=True,
            compute_convergence_checks=False,
        )

    posterior = idata.posterior
    mu = posterior["mu_final"].stack(sample=("chain", "draw")).values.T
    mean = mu.mean(axis=0)
    sd = mu.std(axis=0)
    house_mean = {}
    if prep["pollster_ids"] and "house" in posterior:
        h = posterior["house"].stack(sample=("chain", "draw")).mean(dim="sample").values
        house_mean = {p: float(h[i]) for i, p in enumerate(prep["pollster_ids"])}

    return FitResult(
        race_ids=prep["race_ids"],
        states=prep["states"],
        mean_margin=mean,
        sd_margin=sd,
        draws_margin=mu,
        house_effects=house_mean,
        diagnostics={
            "n_polls": int(len(prep["poll_y"])),
            "n_polls_raw": prep["n_polls_raw"],
            "n_races": n_races,
            "days_to_ed": prep["days_to_ed"],
            "draws": draws,
            "tune": tune,
            "chains": chains,
            "seed": seed,
            "enop_global": prep["enop_global"],
            "enop_by_race_mean": float(np.mean(list(prep["enop_by_race"].values())))
            if prep["enop_by_race"]
            else 0.0,
        },
        method="pymc_nuts",
    )


def fit_fast_approximation(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 20260901,
    generic_ballot: float = 0.0,
) -> FitResult:
    """
    Principled fallback when PyMC sampling is too heavy: hierarchical shrinkage
    posterior with heavy-tailed national / regional / local shocks matching the
    generative story. Uses ENOP-aware influence weights and mode/population offsets.
    """
    rng = np.random.default_rng(seed)
    prep = _prepare(snapshot, generic_ballot=generic_ballot)
    n = len(prep["race_ids"])
    means = prep["prior_mu"].copy()
    sds = np.full(n, 6.5)
    polls = attach_poll_weights(snapshot.polls.copy(), as_of=snapshot.as_of)
    polls = polls[polls["race_id"].isin(set(prep["race_ids"]))]
    house_effects: dict[str, float] = {}
    if prep["n_pollsters"]:
        house_effects = {p: float(prep["house_mu"][i]) for i, p in enumerate(prep["pollster_ids"])}

    for i, rid in enumerate(prep["race_ids"]):
        rp = polls[polls["race_id"] == rid] if len(polls) else polls
        if len(rp):
            n_samp = rp["sample_size"].astype(float).clip(100)
            qw = (
                rp["quality_weight"].astype(float)
                if "quality_weight" in rp.columns
                else pd.Series(np.ones(len(rp)), index=rp.index)
            ).clip(0.2, 1.0)
            house = (
                rp["house_effect_prior"].astype(float)
                if "house_effect_prior" in rp.columns
                else pd.Series(np.zeros(len(rp)), index=rp.index)
            )
            mode_adj = rp["mode"].astype(str).map(_mode_offset) if "mode" in rp.columns else 0.0
            pop_adj = (
                rp["population"].astype(str).map(_population_offset)
                if "population" in rp.columns
                else 0.0
            )
            iw = (
                rp["influence_weight"].astype(float)
                if "influence_weight" in rp.columns
                else pd.Series(np.ones(len(rp)), index=rp.index)
            ).clip(0.05, 3.0)
            y = rp["two_party_margin"].astype(float) - house - mode_adj - pop_adj
            w = n_samp * qw * iw
            # Prior strength scales down when ENOP is high (more independent info)
            enop = float(rp["enop_race"].iloc[0]) if "enop_race" in rp.columns else float(len(rp))
            prior_n = 35.0 / max(np.sqrt(enop / 3.0), 0.6)
            means[i] = (prior_n * means[i] + float((w * y).sum())) / (prior_n + float(w.sum()))
            sds[i] = float(
                np.sqrt(
                    25.0 / (float(w.sum()) / 600.0 + 1.0)
                    + 4.0
                    + prep["days_to_ed"] / 40.0
                    + 2.0 / max(enop, 1.0)
                )
            )

    # Joint draws: national + region + local + continuous similarity Student-t shocks
    nat = rng.standard_t(4, size=n_draws) * 2.0
    region_draws = {r: rng.standard_t(5, size=n_draws) * 1.5 for r in range(prep["n_regions"])}
    local = rng.standard_t(5, size=(n_draws, n)) * (sds * 0.55)
    contested = snapshot.races.set_index("race_id").loc[prep["race_ids"]].reset_index()
    sim_shocks = correlated_shocks(
        contested,
        n_draws,
        rng,
        scale=sds * 0.35,
        length_scale=1.75,
        nu=5.0,
    )
    mu = np.zeros((n_draws, n))
    for i in range(n):
        mu[:, i] = (
            means[i]
            + nat
            + region_draws[int(prep["region_idx"][i])]
            + local[:, i]
            + sim_shocks[:, i]
        )
    mu += rng.standard_t(4, size=(n_draws, 1)) * 1.2

    return FitResult(
        race_ids=prep["race_ids"],
        states=prep["states"],
        mean_margin=mu.mean(axis=0),
        sd_margin=mu.std(axis=0),
        draws_margin=mu,
        house_effects=house_effects,
        diagnostics={
            "n_polls": int(len(prep["poll_y"])),
            "n_polls_raw": prep["n_polls_raw"],
            "n_races": n,
            "days_to_ed": prep["days_to_ed"],
            "draws": n_draws,
            "seed": seed,
            "n_rated_pollsters": int(sum(1 for v in house_effects.values() if v != 0.0)),
            "enop_global": prep["enop_global"],
            "enop_by_race_mean": float(np.mean(list(prep["enop_by_race"].values())))
            if prep["enop_by_race"]
            else 0.0,
            "similarity_covariance": True,
            "note": (
                "fast hierarchical-t with ENOP weights, mode/pop offsets, "
                "richer fundamentals, continuous similarity covariance"
            ),
        },
        method="fast_hierarchical_t",
    )


def draws_from_baseline_forecasts(
    forecasts: list,
    *,
    n_draws: int = 2000,
    seed: int = 0,
    race_ids: list[str] | None = None,
) -> np.ndarray:
    """Independent Normal draws from baseline mean/sd — used as stackable challenger."""
    from midterms.baselines.models import RaceForecast

    rng = np.random.default_rng(seed)
    by_id = {f.race_id: f for f in forecasts}
    ids = race_ids or [f.race_id for f in forecasts]
    means = np.array([by_id[r].mean_margin for r in ids], dtype=float)
    sds = np.array([max(by_id[r].sd, 0.5) for r in ids], dtype=float)
    return rng.normal(means, sds, size=(n_draws, len(ids)))
