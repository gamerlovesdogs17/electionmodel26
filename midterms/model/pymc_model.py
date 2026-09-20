"""Hierarchical Bayesian latent-opinion model for Senate margins (PyMC).

Two production-capable spines (audit Finding 3 / P1.1):

- ``fit_pymc`` — **static** Election-Day hierarchical measurement model.
  Polls observe a single current latent; date enters via recency weights and a
  Morris future/terminal split. Labeled ``latent_path=static_election_day``.
- ``fit_pymc_dynamic`` — **weekly** national + race random walks with calendar-
  scaled innovations; polls observe θ[r, t_poll]. Labeled
  ``latent_path=weekly_random_walk``. Compare via ``compare-static-dynamic``.

Shared features: fundamentals prior, hierarchical house effects, Student-t
measurement, ENOP / pollster influence weights, hierarchical mode/population
effects (audit P1.2; prior means documented in ``midterms.model.effects``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.effects import (
    MODE_ORDER,
    POP_ORDER,
    encode_mode_pop,
    fixed_mode_offset,
    fixed_population_offset,
)
from midterms.model.fundamentals import fundamentals_mean
from midterms.model.poll_weights import attach_poll_weights, global_enop, race_enop_summary
from midterms.model.terminal import (
    active_scales,
    add_similarity_terminal,
    add_terminal_layers,
    error_budget_block,
    scales_kwargs,
)
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
    """Prior-mean mode offset for fast/state-space paths (PyMC estimates hierarchically)."""
    return fixed_mode_offset(mode)


def _population_offset(population: object) -> float:
    """Prior-mean population offset for fast/state-space paths."""
    return fixed_population_offset(population)


def _measurement_effects(pm, prep: dict):
    """Hierarchical mode + population effects shrunk toward documented priors."""
    coords_extra = {"mode": list(MODE_ORDER), "pop": list(POP_ORDER)}
    sigma_mode = pm.HalfNormal("sigma_mode", 0.6)
    sigma_pop = pm.HalfNormal("sigma_pop", 0.6)
    mode_eff = pm.Normal(
        "mode_eff",
        mu=prep["mode_prior"],
        sigma=sigma_mode,
        dims="mode",
    )
    pop_eff = pm.Normal(
        "pop_eff",
        mu=prep["pop_prior"],
        sigma=sigma_pop,
        dims="pop",
    )
    return mode_eff, pop_eff, coords_extra


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

    mode_idx, pop_idx, mode_prior, pop_prior = encode_mode_pop(polls)
    if len(polls):
        poll_race = polls["race_id"].map(race_index).astype(int).to_numpy()
        poll_house = polls["pollster_id"].map(pollster_index).astype(int).to_numpy()
        # Raw margins — mode/pop enter as hierarchical likelihood effects (P1.2)
        poll_y = polls["two_party_margin"].astype(float).to_numpy()
        poll_field_end = []
        for v in polls["field_end"].tolist() if "field_end" in polls.columns else []:
            try:
                poll_field_end.append(date.fromisoformat(str(v)[:10]))
            except ValueError:
                poll_field_end.append(None)
        while len(poll_field_end) < len(poll_y):
            poll_field_end.append(None)
        n_raw = pd.to_numeric(polls["sample_size"], errors="coerce")
        n = n_raw.where(np.isfinite(n_raw) & (n_raw > 0), 500.0).clip(lower=50.0).to_numpy(dtype=float)
        qw = (
            polls["quality_weight"].astype(float)
            if "quality_weight" in polls.columns
            else pd.Series(np.ones(len(polls)), index=polls.index)
        ).fillna(1.0).clip(0.2, 1.0).to_numpy(dtype=float)
        # Inflate SE when influence weight is low (down-weighted / capped polls)
        iw = (
            polls["influence_weight"].astype(float).fillna(1.0).clip(0.05, 3.0).to_numpy(dtype=float)
            if "influence_weight" in polls.columns
            else np.ones(len(polls), dtype=float)
        )
        poll_se = np.asarray(
            100.0 / np.sqrt(n) / np.sqrt(np.maximum(qw, 0.2)) / np.sqrt(np.maximum(iw, 0.05)),
            dtype=float,
        )
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
        poll_field_end = []

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
        "poll_mode": mode_idx,
        "poll_pop": pop_idx,
        "mode_prior": mode_prior,
        "pop_prior": pop_prior,
        "poll_y": poll_y,
        "poll_se": poll_se,
        "poll_field_end": poll_field_end,
        "influence": influence,
        "n_pollsters": len(pollster_ids),
        "pollster_ids": pollster_ids,
        "house_mu": house_mu,
        "extra_mu": extra_mu,
        "days_to_ed": days_to_ed,
        "election_day": ed,
        "as_of": snapshot.as_of,
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
    terminal_scales: dict | None = None,
) -> FitResult:
    import pymc as pm

    prep = _prepare(snapshot, generic_ballot=generic_ballot)
    n_races = len(prep["race_ids"])
    # Blueprint §7.2 Morris split: future movement contracts; terminal ED error does not.
    future_sd = float(4.5 * np.sqrt(max(prep["days_to_ed"], 1) / 120.0))
    nat_future_sd = float(0.55 * future_sd)
    scales = active_scales(**scales_kwargs(terminal_scales))

    coords = {
        "race": prep["race_ids"],
        "region": list(range(prep["n_regions"])),
        "pollster": prep["pollster_ids"] or ["_none"],
        "mode": list(MODE_ORDER),
        "pop": list(POP_ORDER),
    }

    with pm.Model(coords=coords) as model:  # noqa: F841
        sigma_nat = pm.HalfNormal("sigma_nat", 4.0)
        national = pm.StudentT("national", nu=4, mu=0.0, sigma=sigma_nat)
        sigma_region = pm.HalfNormal("sigma_region", 2.5)
        region_eff = pm.StudentT("region_eff", nu=5, mu=0.0, sigma=sigma_region, dims="region")
        sigma_local = pm.HalfNormal("sigma_local", 3.5)
        local = pm.StudentT("local", nu=5, mu=0.0, sigma=sigma_local, dims="race")

        mu_ed = pm.Deterministic(
            "mu_ed",
            prep["prior_mu"] + national + region_eff[prep["region_idx"]] + local,
            dims="race",
        )

        # current_latent = mu_ed - future_movement  (polls observe current)
        future_nat = pm.StudentT("future_nat", nu=5, mu=0.0, sigma=nat_future_sd)
        future_race = pm.StudentT(
            "future_race", nu=5, mu=0.0, sigma=float(np.sqrt(max(future_sd**2 - nat_future_sd**2, 0.25))), dims="race"
        )
        future = pm.Deterministic("future", future_nat + future_race, dims="race")
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

        mode_eff, pop_eff, _ = _measurement_effects(pm, prep)

        if len(prep["poll_y"]):
            sigma_obs = pm.math.sqrt(prep["poll_se"] ** 2 + extra[prep["poll_house"]] ** 2)
            mu_poll = (
                theta_now[prep["poll_race"]]
                + house[prep["poll_house"]]
                + mode_eff[prep["poll_mode"]]
                + pop_eff[prep["poll_pop"]]
            )
            pm.StudentT(
                "polls",
                nu=5,
                mu=mu_poll,
                sigma=sigma_obs,
                observed=prep["poll_y"],
            )

        # Layered terminal: national + race in-model; similarity post-draw (P1.3)
        terminal_nat = pm.StudentT(
            "terminal_nat", nu=4, mu=0.0, sigma=scales["terminal_nat_sd"]
        )
        terminal_race = pm.StudentT(
            "terminal_race",
            nu=5,
            mu=0.0,
            sigma=scales["terminal_race_sd"],
            dims="race",
        )
        mu_final = pm.Deterministic(
            "mu_final", mu_ed + terminal_nat + terminal_race, dims="race"
        )

        idata = pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            random_seed=seed,
            target_accept=0.9,
            progressbar=False,
            return_inferencedata=True,
            compute_convergence_checks=False,
            cores=1,
        )

    from midterms.validation.numerical_quality import idata_convergence

    conv = idata_convergence(idata, var_name="mu_final")

    posterior = idata.posterior
    mu = posterior["mu_final"].stack(sample=("chain", "draw")).values.T
    contested = prep["races"].set_index("race_id").loc[prep["race_ids"]].reset_index()
    rng = np.random.default_rng(seed + 17)
    mu = add_similarity_terminal(mu, contested, rng, **scales)
    mean = mu.mean(axis=0)
    sd = mu.std(axis=0)
    house_mean = {}
    if prep["pollster_ids"] and "house" in posterior:
        h = posterior["house"].stack(sample=("chain", "draw")).mean(dim="sample").values
        house_mean = {p: float(h[i]) for i, p in enumerate(prep["pollster_ids"])}
    mode_mean = {}
    pop_mean = {}
    if "mode_eff" in posterior:
        m = posterior["mode_eff"].stack(sample=("chain", "draw")).mean(dim="sample").values
        mode_mean = {MODE_ORDER[i]: float(m[i]) for i in range(len(MODE_ORDER))}
    if "pop_eff" in posterior:
        p = posterior["pop_eff"].stack(sample=("chain", "draw")).mean(dim="sample").values
        pop_mean = {POP_ORDER[i]: float(p[i]) for i in range(len(POP_ORDER))}

    budget = error_budget_block(scales)
    budget.update(
        {
            "sigma_nat_prior": 4.0,
            "sigma_region_prior": 2.5,
            "sigma_local_prior": 3.5,
            "future_movement_sd": float(future_sd),
            "future_nat_sd": float(nat_future_sd),
            "days_to_ed": prep["days_to_ed"],
        }
    )

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
            "n_posterior_samples": int(draws * chains),
            "latent_path": "static_election_day",
            "measurement_effects": "hierarchical_mode_pop",
            "terminal_layers": "national+race+similarity",
            "convergence": conv,
            "mode_effects_mean": mode_mean,
            "pop_effects_mean": pop_mean,
            "enop_global": prep["enop_global"],
            "enop_by_race_mean": float(np.mean(list(prep["enop_by_race"].values())))
            if prep["enop_by_race"]
            else 0.0,
            "error_budget": budget,
        },
        method="pymc",
    )


def _prepare_weekly_path(snapshot: EvidenceSnapshot, generic_ballot: float = 0.0) -> dict:
    """Extend _prepare with a weekly calendar index for dynamic latent paths."""
    from datetime import timedelta

    prep = _prepare(snapshot, generic_ballot=generic_ballot)
    ed = prep["election_day"]
    as_of = prep["as_of"]
    field_ends = [d for d in prep.get("poll_field_end") or [] if d is not None]
    origin = min([as_of, ed] + field_ends) if field_ends else min(as_of, ed)
    max_lookback = 180
    if (ed - origin).days > max_lookback:
        origin = ed - timedelta(days=max_lookback)
    n_weeks = max(int(np.ceil((ed - origin).days / 7.0)) + 1, 2)
    week_ends = [origin + timedelta(days=7 * i) for i in range(n_weeks)]
    if week_ends[-1] < ed:
        week_ends.append(ed)
        n_weeks = len(week_ends)

    def week_of(d: date) -> int:
        idx = int((d - origin).days // 7)
        return int(np.clip(idx, 0, n_weeks - 1))

    as_of_week = week_of(as_of)
    poll_week = np.full(len(prep["poll_y"]), as_of_week, dtype=int)
    for i, d in enumerate(prep.get("poll_field_end") or []):
        if i >= len(poll_week):
            break
        if d is not None:
            poll_week[i] = week_of(d)

    day_gaps = np.ones(n_weeks, dtype=float)
    for i in range(1, n_weeks):
        day_gaps[i] = max((week_ends[i] - week_ends[i - 1]).days, 1)
    day_gaps[0] = max(float(day_gaps[1] if n_weeks > 1 else 7.0), 1.0)

    prep.update(
        {
            "origin": origin,
            "n_weeks": n_weeks,
            "week_ends": [d.isoformat() for d in week_ends],
            "poll_week": poll_week,
            "as_of_week": as_of_week,
            "ed_week": n_weeks - 1,
            "day_gaps": day_gaps,
        }
    )
    return prep


def fit_pymc_dynamic(
    snapshot: EvidenceSnapshot,
    *,
    draws: int = 300,
    tune: int = 300,
    chains: int = 2,
    seed: int = 20260901,
    generic_ballot: float = 0.0,
    terminal_scales: dict | None = None,
) -> FitResult:
    """
    Unified dynamic hierarchical core (blueprint §7.1–7.2 / Finding 3).

    Weekly national + race random walks with calendar-scaled innovations.
    Process noise from ``as_of``→ED is calibrated to the Morris future-movement
    budget so RW future variance is **not** stacked again on full static
    terminal scales. Election-Day terminal = residual polling error only
    (reduced nat/race + light similarity). Polls observe ``theta[r, t_poll]``.

    Distinct from ``fit_pymc`` (static Election-Day latent). Competing OOS
    challenger — stack weight only if earned.
    """
    import pymc as pm

    prep = _prepare_weekly_path(snapshot, generic_ballot=generic_ballot)
    n_races = len(prep["race_ids"])
    t_weeks = int(prep["n_weeks"])
    day_gaps = np.asarray(prep["day_gaps"], dtype=float)
    step_scale = np.sqrt(day_gaps / 7.0)
    as_of_week = int(prep["as_of_week"])
    ed_week = int(prep["ed_week"])
    n_future = max(ed_week - as_of_week, 1)

    # Morris future-movement budget (same family as static / state_space).
    future_sd = float(4.5 * np.sqrt(max(prep["days_to_ed"], 1) / 120.0))
    nat_future_sd = float(0.55 * future_sd)
    race_future_sd = float(np.sqrt(max(future_sd**2 - nat_future_sd**2, 0.25)))
    # Per-week process scales so Var(cumsum over future weeks) ≈ Morris budget.
    # E[sum_i (s_i * sigma)^2] ≈ sigma^2 * sum(s_i^2); set sigma accordingly.
    future_step2 = float(np.sum(step_scale[as_of_week + 1 : ed_week + 1] ** 2)) or float(n_future)
    hist_step2 = float(np.sum(step_scale[: as_of_week + 1] ** 2)) or 1.0
    sigma_nat_future = float(nat_future_sd / np.sqrt(future_step2))
    sigma_race_future = float(race_future_sd / np.sqrt(future_step2))
    # Historical RW (weeks ≤ as_of): smaller — polls pin the path.
    sigma_nat_hist = float(min(1.2, sigma_nat_future * 0.85))
    sigma_race_hist = float(min(0.7, sigma_race_future * 0.85))

    base_scales = active_scales(**scales_kwargs(terminal_scales))
    # Residual ED polling error only (path already carries future movement).
    # Match state_space practice: light national + similarity; race residual in RW.
    dyn_scales = {
        **base_scales,
        "terminal_nat_sd": float(base_scales["terminal_nat_sd"] * 0.35),
        "terminal_race_sd": float(base_scales["terminal_race_sd"] * 0.25),
        "sim_scale": float(base_scales["sim_scale"] * 0.85),
    }

    # Per-week innovation multipliers (hist vs future)
    nat_step_sd = np.full(t_weeks, sigma_nat_hist, dtype=float)
    race_step_sd = np.full(t_weeks, sigma_race_hist, dtype=float)
    if as_of_week + 1 < t_weeks:
        nat_step_sd[as_of_week + 1 :] = sigma_nat_future
        race_step_sd[as_of_week + 1 :] = sigma_race_future

    coords = {
        "race": prep["race_ids"],
        "region": list(range(prep["n_regions"])),
        "week": list(range(t_weeks)),
        "pollster": prep["pollster_ids"] or ["_none"],
        "mode": list(MODE_ORDER),
        "pop": list(POP_ORDER),
    }

    with pm.Model(coords=coords) as model:  # noqa: F841
        # Anchors (partial pooling) — level around fundamentals
        sigma_region = pm.HalfNormal("sigma_region", 2.5)
        region_eff = pm.StudentT("region_eff", nu=5, mu=0.0, sigma=sigma_region, dims="region")
        sigma_local0 = pm.HalfNormal("sigma_local0", 3.0)
        local0 = pm.StudentT("local0", nu=5, mu=0.0, sigma=sigma_local0, dims="race")

        # National / race weekly RW with calendar + Morris-calibrated step sds
        nat_innov = pm.Normal("nat_innov", 0.0, 1.0, dims="week")
        nat_steps = nat_innov * (nat_step_sd * step_scale)
        nat = pm.Deterministic("nat", pm.math.cumsum(nat_steps), dims="week")

        race_innov = pm.Normal("race_innov", 0.0, 1.0, dims=("race", "week"))
        race_steps = race_innov * (race_step_sd[None, :] * step_scale[None, :])
        race_rw = pm.Deterministic("race_rw", pm.math.cumsum(race_steps, axis=1), dims=("race", "week"))

        level = prep["prior_mu"] + region_eff[prep["region_idx"]] + local0  # (race,)
        theta = pm.Deterministic(
            "theta",
            level[:, None] + nat[None, :] + race_rw,
            dims=("race", "week"),
        )

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

        mode_eff, pop_eff, _ = _measurement_effects(pm, prep)

        if len(prep["poll_y"]):
            sigma_obs = pm.math.sqrt(prep["poll_se"] ** 2 + extra[prep["poll_house"]] ** 2)
            mu_poll = (
                theta[prep["poll_race"], prep["poll_week"]]
                + house[prep["poll_house"]]
                + mode_eff[prep["poll_mode"]]
                + pop_eff[prep["poll_pop"]]
            )
            pm.StudentT(
                "polls",
                nu=5,
                mu=mu_poll,
                sigma=sigma_obs,
                observed=prep["poll_y"],
            )

        # Residual ED terminal only (future movement already in RW after as_of)
        terminal_nat = pm.StudentT(
            "terminal_nat", nu=4, mu=0.0, sigma=dyn_scales["terminal_nat_sd"]
        )
        terminal_race = pm.StudentT(
            "terminal_race",
            nu=5,
            mu=0.0,
            sigma=dyn_scales["terminal_race_sd"],
            dims="race",
        )
        mu_final = pm.Deterministic(
            "mu_final",
            theta[:, ed_week] + terminal_nat + terminal_race,
            dims="race",
        )
        pm.Deterministic("theta_as_of", theta[:, as_of_week], dims="race")
        pm.Deterministic(
            "future_move",
            theta[:, ed_week] - theta[:, as_of_week],
            dims="race",
        )

        idata = pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            random_seed=seed,
            target_accept=0.9,
            progressbar=False,
            return_inferencedata=True,
            compute_convergence_checks=False,
            cores=1,
        )

    from midterms.validation.numerical_quality import idata_convergence

    conv = idata_convergence(idata, var_name="mu_final")

    posterior = idata.posterior
    mu = posterior["mu_final"].stack(sample=("chain", "draw")).values.T
    contested = prep["races"].set_index("race_id").loc[prep["race_ids"]].reset_index()
    rng = np.random.default_rng(seed + 19)
    mu = add_similarity_terminal(mu, contested, rng, **dyn_scales)
    mean = mu.mean(axis=0)
    sd = mu.std(axis=0)
    house_mean = {}
    if prep["pollster_ids"] and "house" in posterior:
        h = posterior["house"].stack(sample=("chain", "draw")).mean(dim="sample").values
        house_mean = {p: float(h[i]) for i, p in enumerate(prep["pollster_ids"])}

    budget = error_budget_block(dyn_scales)
    budget.update(
        {
            "future_movement_sd_target": float(future_sd),
            "nat_future_sd_target": float(nat_future_sd),
            "race_future_sd_target": float(race_future_sd),
            "sigma_nat_future_step": float(sigma_nat_future),
            "sigma_race_future_step": float(sigma_race_future),
            "sigma_nat_hist_step": float(sigma_nat_hist),
            "sigma_race_hist_step": float(sigma_race_hist),
            "future_step2": float(future_step2),
            "hist_step2": float(hist_step2),
            "n_weeks": t_weeks,
            "n_future_weeks": int(n_future),
            "terminal_budget": "residual_ed_only_after_calibrated_rw",
            "double_count_guard": "morris_calibrated_rw_then_reduced_terminal",
        }
    )

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
            "n_weeks": t_weeks,
            "origin": str(prep["origin"]),
            "as_of_week": as_of_week,
            "ed_week": ed_week,
            "draws": draws,
            "tune": tune,
            "chains": chains,
            "seed": seed,
            "n_posterior_samples": int(draws * chains),
            "latent_path": "weekly_random_walk_morris_calibrated",
            "measurement_effects": "hierarchical_mode_pop",
            "terminal_layers": "reduced_nat+race+similarity_after_rw",
            "convergence": conv,
            "enop_global": prep["enop_global"],
            "enop_by_race_mean": float(np.mean(list(prep["enop_by_race"].values())))
            if prep["enop_by_race"]
            else 0.0,
            "error_budget": budget,
        },
        method="pymc_dynamic",
    )


def fit_fast_approximation(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 20260901,
    generic_ballot: float = 0.0,
    terminal_scales: dict | None = None,
) -> FitResult:
    """
    Principled fallback when PyMC sampling is too heavy: hierarchical shrinkage
    posterior with heavy-tailed national / regional / local shocks matching the
    generative story. Uses ENOP-aware influence weights and layered terminal
    (national + race + similarity; audit P1.3).
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
    scales = active_scales(**scales_kwargs(terminal_scales))

    for i, rid in enumerate(prep["race_ids"]):
        rp = polls[polls["race_id"] == rid] if len(polls) else polls
        if len(rp):
            n_samp = pd.to_numeric(rp["sample_size"], errors="coerce").fillna(500.0).clip(100)
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

    # Path shocks (opinion / future); terminal layers applied separately (P1.3)
    future_sd = float(4.5 * np.sqrt(max(prep["days_to_ed"], 1) / 120.0))
    nat_sd = float(2.8 + 0.015 * max(prep["days_to_ed"], 1) + 0.35 * future_sd)
    nat = rng.standard_t(4, size=n_draws) * nat_sd
    region_draws = {r: rng.standard_t(5, size=n_draws) * 1.8 for r in range(prep["n_regions"])}
    local = rng.standard_t(5, size=(n_draws, n)) * (sds * 0.6)
    contested = snapshot.races.set_index("race_id").loc[prep["race_ids"]].reset_index()
    mu = np.zeros((n_draws, n))
    for i in range(n):
        mu[:, i] = (
            means[i]
            + nat
            + region_draws[int(prep["region_idx"][i])]
            + local[:, i]
        )
    mu = add_terminal_layers(mu, contested, rng, **scales)

    budget = error_budget_block(scales)
    budget.update(
        {
            "national_path_sd": float(nat_sd),
            "future_movement_sd": float(future_sd),
            "region_sd": 1.8,
            "local_scale_of_race_sd": 0.6,
            "days_to_ed": prep["days_to_ed"],
        }
    )

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
            "terminal_layers": "national+race+similarity",
            "error_budget": budget,
            "measurement_effects": "prior_mean_mode_pop",
            "note": (
                "NON-PRODUCTION approximation: fast hierarchical-t with ENOP weights, "
                "mode/pop prior-mean offsets, layered terminal + similarity (P1.3). "
                "Prefer pymc / ensemble_stack for published forecasts."
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
