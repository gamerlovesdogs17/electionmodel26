"""Forward state-space opinion path with future movement + terminal ED error.

Blueprint §7.1–7.2: separate current latent, future movement, and Election-Day
polling error. Implemented as a lightweight Kalman-smoothed race path with
Student-t terminal shocks — stackable challenger / optional core method.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from midterms.evidence.schema import is_active_ballot_row
from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.fundamentals import fundamentals_mean
from midterms.model.poll_weights import attach_poll_weights
from midterms.model.pymc_model import FitResult
from midterms.model.similarity import correlated_shocks


def fit_state_space(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 20260901,
    generic_ballot: float = 0.0,
    student_t_df: float = 5.0,
    era_weight: float = 1.0,
    fund_pull: float = 0.35,
    flat_prior: bool = False,
) -> FitResult:
    """
    Per-race forward filter of poll margins → current latent, then project to ED.

    future_movement_sd shrinks with days_to_ed; terminal_error_sd does not.
    fund_pull blends the filtered latent toward the fundamentals prior at ED
    (set 0 for a poll-only challenger). flat_prior starts the filter at 0.
    """
    rng = np.random.default_rng(seed)
    races = snapshot.races.copy()
    if len(races):
        races = races[races.apply(is_active_ballot_row, axis=1)].reset_index(drop=True)
    if races.empty:
        return FitResult([], [], np.array([]), np.array([]), np.zeros((n_draws, 0)), {}, {}, "state_space")

    polls = attach_poll_weights(snapshot.polls.copy(), as_of=snapshot.as_of)
    polls = polls[polls["race_id"].isin(set(races["race_id"]))]

    real_income_yoy = None
    try:
        from midterms.evidence.economics import yoy_growth_as_of

        year = int(str(races["election_day"].iloc[0])[:4])
        real_income_yoy = yoy_growth_as_of(snapshot.as_of, election_year=year)
    except Exception:  # noqa: BLE001
        pass

    fund = fundamentals_mean(
        snapshot.races,
        generic_ballot=generic_ballot,
        real_income_yoy=real_income_yoy,
    )

    ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
    days_to_ed = max((ed - snapshot.as_of).days, 1)
    # Future movement contracts; terminal ED error stays (Morris-style)
    future_sd = 4.5 * np.sqrt(days_to_ed / 120.0) * float(era_weight)
    terminal_sd = 3.5 * float(era_weight)
    pull = float(np.clip(fund_pull, 0.0, 1.0))

    means = []
    sds = []
    race_ids = races["race_id"].tolist()
    states = races["state"].astype(str).tolist()

    for i, rid in enumerate(race_ids):
        prior = 0.0 if flat_prior else float(fund.get(rid, races.iloc[i]["prior_lean"]))
        rp = polls[polls["race_id"] == rid] if len(polls) else polls
        mu = prior
        var = 8.0**2
        if len(rp):
            rp = rp.sort_values("field_end")
            for _, row in rp.iterrows():
                y = float(row["two_party_margin"])
                n = float(max(row.get("sample_size") or 500, 50))
                qw = float(row.get("quality_weight") or 1.0)
                iw = float(row.get("influence_weight") or 1.0) if "influence_weight" in rp.columns else 1.0
                # Process noise between polls (days)
                var = var + 0.8**2
                obs_var = (100.0 / np.sqrt(n)) ** 2 / max(qw * iw, 0.05) + 2.0**2
                k = var / (var + obs_var)
                mu = mu + k * (y - mu)
                var = (1 - k) * var
        # Project to Election Day
        ed_mu = (1.0 - pull) * mu + pull * prior
        ed_sd = float(np.sqrt(var + future_sd**2 + terminal_sd**2))
        means.append(ed_mu)
        sds.append(ed_sd)

    means_a = np.asarray(means, dtype=float)
    sds_a = np.asarray(sds, dtype=float)
    # Joint: national + similarity + independent terminal Student-t
    nat = rng.standard_t(student_t_df, size=n_draws) * 2.0
    local = rng.standard_t(student_t_df, size=(n_draws, len(means_a))) * (sds_a * 0.55)
    sim = correlated_shocks(races, n_draws, rng, scale=sds_a * 0.3, nu=student_t_df)
    mu_draws = means_a[None, :] + nat[:, None] + local + sim

    return FitResult(
        race_ids=race_ids,
        states=states,
        mean_margin=mu_draws.mean(axis=0),
        sd_margin=mu_draws.std(axis=0),
        draws_margin=mu_draws,
        house_effects={},
        diagnostics={
            "n_polls": int(len(polls)),
            "n_races": len(race_ids),
            "days_to_ed": days_to_ed,
            "future_movement_sd": float(future_sd),
            "terminal_error_sd": float(terminal_sd),
            "student_t_df": float(student_t_df),
            "era_weight": float(era_weight),
            "fund_pull": float(pull),
            "flat_prior": bool(flat_prior),
            "draws": n_draws,
            "seed": seed,
            "note": "forward state-space with contracting future movement + terminal ED error",
        },
        method="state_space",
    )
