"""Forward state-space opinion path with future movement + terminal ED error.

Blueprint §7.1–7.2: separate current latent, future movement, and Election-Day
polling error. National path shared across races + race residuals; Student-t
terminal shocks — stackable challenger / optional core method.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from midterms.config import ERA_WEIGHT, STUDENT_T_DF
from midterms.evidence.schema import is_active_ballot_row
from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.fundamentals import fundamentals_mean
from midterms.model.poll_weights import attach_poll_weights
from midterms.model.pymc_model import FitResult, _mode_offset, _population_offset
from midterms.model.terminal import add_terminal_layers, error_budget_block


def future_movement_sd(days_to_ed: int, *, era_weight: float = 1.0, base: float = 4.5) -> float:
    """Future movement contracts toward Election Day (Morris-style)."""
    return float(base * np.sqrt(max(days_to_ed, 1) / 120.0) * float(era_weight))


def terminal_error_sd(*, era_weight: float = 1.0, base: float = 3.5) -> float:
    """Terminal ED polling error does not vanish as days_to_ed → 0."""
    return float(base * float(era_weight))


def _safe_sample_size(value: object, *, default: float = 500.0, floor: float = 50.0) -> float:
    """Coerce poll N; NaN is truthy in Python so `nan or 500` must not be used."""
    try:
        n = float(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    if not np.isfinite(n) or n <= 0:
        n = default
    return float(max(n, floor))


def fit_state_space(
    snapshot: EvidenceSnapshot,
    *,
    n_draws: int = 2000,
    seed: int = 20260901,
    generic_ballot: float = 0.0,
    student_t_df: float = STUDENT_T_DF,
    era_weight: float = ERA_WEIGHT,
    fund_pull: float = 0.35,
    flat_prior: bool = False,
    future_base: float = 4.5,
    terminal_base: float = 3.5,
    national_path_sd: float | None = None,
) -> FitResult:
    """
    Per-race forward filter of poll margins → current latent, then project to ED.

    National latent path is shared; race residuals + similarity shocks remain.
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
    future_sd = future_movement_sd(days_to_ed, era_weight=era_weight, base=future_base)
    terminal_sd = terminal_error_sd(era_weight=era_weight, base=terminal_base)
    if national_path_sd is None:
        national_path_sd = float(2.5 + 0.02 * days_to_ed)
    pull = float(np.clip(fund_pull, 0.0, 1.0))

    means = []
    sds = []
    race_ids = races["race_id"].tolist()
    states = races["state"].astype(str).tolist()
    house_effects: dict[str, float] = {}

    for i, rid in enumerate(race_ids):
        prior = 0.0 if flat_prior else float(fund.get(rid, races.iloc[i]["prior_lean"]))
        if not np.isfinite(prior):
            try:
                prior = float(races.iloc[i]["prior_lean"] or 0.0)
            except (TypeError, ValueError):
                prior = 0.0
            if not np.isfinite(prior):
                prior = 0.0
        rp = polls[polls["race_id"] == rid] if len(polls) else polls
        mu = prior
        var = 8.0**2
        if len(rp):
            rp = rp.sort_values("field_end")
            for _, row in rp.iterrows():
                try:
                    y = float(row["two_party_margin"])
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(y):
                    continue
                # Measurement offsets (same channel as hierarchical spine)
                y = y - _mode_offset(row.get("mode")) - _population_offset(row.get("population"))
                if "house_effect_prior" in rp.columns and pd.notna(row.get("house_effect_prior")):
                    he = float(row["house_effect_prior"])
                    if np.isfinite(he):
                        y = y - he
                        pid = str(row.get("pollster_id") or "")
                        if pid:
                            house_effects[pid] = he
                n = _safe_sample_size(row.get("sample_size"))
                try:
                    qw = float(row.get("quality_weight") or 1.0)
                except (TypeError, ValueError):
                    qw = 1.0
                if not np.isfinite(qw) or qw <= 0:
                    qw = 1.0
                if "influence_weight" in rp.columns:
                    try:
                        iw = float(row["influence_weight"])
                    except (TypeError, ValueError):
                        iw = 1.0
                    if not np.isfinite(iw) or iw < 0:
                        iw = 1.0
                else:
                    iw = 1.0
                var = var + 0.8**2
                obs_var = (100.0 / np.sqrt(n)) ** 2 / max(qw * max(iw, 0.0), 0.05) + 2.0**2
                k = var / (var + obs_var)
                mu = mu + k * (y - mu)
                var = (1 - k) * var
        if not np.isfinite(mu):
            mu = prior
        if not np.isfinite(var) or var <= 0:
            var = 8.0**2
        ed_mu = (1.0 - pull) * mu + pull * prior
        ed_sd = float(np.sqrt(var + future_sd**2 + terminal_sd**2))
        means.append(float(ed_mu))
        sds.append(float(ed_sd))

    means_a = np.asarray(means, dtype=float)
    sds_a = np.asarray(sds, dtype=float)
    # Last-resort fill so a poisoned race cannot emit all-NaN stack columns.
    bad = ~np.isfinite(means_a)
    if bad.any():
        means_a = means_a.copy()
        means_a[bad] = 0.0
    bad_sd = ~np.isfinite(sds_a) | (sds_a <= 0)
    if bad_sd.any():
        sds_a = sds_a.copy()
        sds_a[bad_sd] = float(np.sqrt(8.0**2 + future_sd**2 + terminal_sd**2))
    # Shared national path + race residual; layered terminal (nat+race+similarity)
    nat = rng.standard_t(student_t_df, size=n_draws) * float(national_path_sd)
    local = rng.standard_t(student_t_df, size=(n_draws, len(means_a))) * (sds_a * 0.55)
    mu_draws = means_a[None, :] + nat[:, None] + local
    # Path already embeds future+terminal variance in sds; add correlated terminal
    # layers at calibrated absolute scales (similarity + residual nat/race).
    from midterms.model.terminal import active_scales

    scales = active_scales()
    # Race residual already in local; use similarity + light national only
    mu_draws = add_terminal_layers(
        mu_draws,
        races,
        rng,
        terminal_nat_sd=scales["terminal_nat_sd"] * 0.35,
        terminal_race_sd=0.0,
        sim_scale=scales["sim_scale"],
        length_scale=scales["length_scale"],
        nu=student_t_df,
    )

    budget = error_budget_block(scales)
    return FitResult(
        race_ids=race_ids,
        states=states,
        mean_margin=mu_draws.mean(axis=0),
        sd_margin=mu_draws.std(axis=0),
        draws_margin=mu_draws,
        house_effects=house_effects,
        diagnostics={
            "n_polls": int(len(polls)),
            "n_races": len(race_ids),
            "days_to_ed": days_to_ed,
            "future_movement_sd": float(future_sd),
            "terminal_error_sd": float(terminal_sd),
            "national_path_sd": float(national_path_sd),
            "student_t_df": float(student_t_df),
            "era_weight": float(era_weight),
            "fund_pull": float(pull),
            "flat_prior": bool(flat_prior),
            "future_base": float(future_base),
            "terminal_base": float(terminal_base),
            "terminal_layers": "national+race+similarity",
            "error_budget": budget,
            "draws": n_draws,
            "seed": seed,
            "note": (
                "national-path state-space: contracting future movement + layered "
                "terminal (P1.3); mode/pop/house offsets aligned with hierarchical measurement"
            ),
        },
        method="state_space",
    )
