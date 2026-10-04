"""Narrow poll-dominant adapter for binary non-major-party Senate contests.

The validated D-v-R models continue to receive only ordinary D-v-R races.  This
adapter operates on a different, candidate-neutral estimand:

    modeled_candidate_margin = modeled_share - opposing_share

after normalizing those two candidates to 100 percent.  Its validation sample
is tiny, so the method is intentionally simple and conservatively uncertain.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from midterms.evidence.non_major_contract import (
    NON_MAJOR_ADAPTER_METHOD,
    NON_MAJOR_TARGET,
    non_major_identity_supported,
    probability_support_status,
)
from midterms.evidence.warehouse import EvidenceSnapshot
from midterms.model.poll_weights import attach_poll_weights, race_enop_summary
from midterms.model.pymc_model import FitResult

# The presidential-relative state lean is a location anchor, not a claim that
# an Independent candidate is a Democrat.  The 30 point candidate deviation is
# intentionally much wider than an ordinary-race prior and is not estimated
# from the small exceptional-race outcome sample.
PRIOR_MEAN = 0.0  # retained only for the zero-centered validation comparator
PRIOR_SD = 30.0
ZERO_CENTERED_COMPARATOR_SD = 20.0
POLL_ERROR_FLOOR = 5.0
STRUCTURAL_PREDICTIVE_SD = 8.0
COMMON_VARIANCE_SHARE = 0.20
ADAPTER_SPEC_VERSION = "binary-non-major-adapter-v2"
STRUCTURAL_PRIOR_METHOD = "presidential_relative_state_anchor_plus_exceptional_candidate_deviation"


def modeled_candidate_margin(modeled_share: float, opposing_share: float) -> float:
    """Return the normalized two-candidate modeled-minus-opposing margin."""

    modeled = float(modeled_share)
    opposing = float(opposing_share)
    total = modeled + opposing
    if not np.isfinite(total) or total <= 0:
        raise ValueError("modeled and opposing shares must have a positive finite sum")
    return float(100.0 * (modeled - opposing) / total)


def _poll_margins(polls: pd.DataFrame) -> np.ndarray:
    if "modeled_margin" in polls.columns:
        values = pd.to_numeric(polls["modeled_margin"], errors="coerce")
    elif {"modeled_share", "opposing_share"}.issubset(polls.columns):
        values = polls.apply(
            lambda row: modeled_candidate_margin(
                row["modeled_share"], row["opposing_share"],
            ),
            axis=1,
        )
    else:
        raise ValueError("candidate-neutral polls require modeled_margin or both shares")
    if values.isna().any():
        raise ValueError("candidate-neutral poll margin contains missing/non-numeric values")
    return values.to_numpy(dtype=float)


def _name_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace(".", " ").split())


def _candidate_compatible_polls(polls: pd.DataFrame, race: pd.Series) -> pd.DataFrame:
    if polls.empty:
        return polls.copy()
    modeled_name = _name_key(race.get("modeled_candidate_name"))
    opposing_name = _name_key(race.get("opposing_candidate_name"))
    mask = pd.Series(True, index=polls.index)
    if "modeled_candidate_name" in polls.columns:
        mask &= polls["modeled_candidate_name"].map(_name_key).eq(modeled_name)
    if "opposing_candidate_name" in polls.columns:
        mask &= polls["opposing_candidate_name"].map(_name_key).eq(opposing_name)
    if "modeled_ballot_party" in polls.columns:
        mask &= polls["modeled_ballot_party"].fillna("").astype(str).str.upper().eq("I")
    if "opposing_ballot_party" in polls.columns:
        mask &= polls["opposing_ballot_party"].fillna("").astype(str).str.upper().eq("R")
    return polls[mask].copy()


def _posterior_location(
    polls: pd.DataFrame,
    *,
    as_of: date,
    prior_mean: float = PRIOR_MEAN,
    prior_sd: float = PRIOR_SD,
) -> dict[str, Any]:
    """Conjugate weak-prior aggregation using transferable poll weights only."""

    if polls.empty:
        posterior_sd = float(prior_sd)
        return {
            "mean": float(prior_mean),
            "posterior_sd": posterior_sd,
            "predictive_sd": float(np.sqrt(posterior_sd**2 + STRUCTURAL_PREDICTIVE_SD**2)),
            "n_polls": 0,
            "enop": 0.0,
            "poll_ids": [],
            "poll_weighted_location": None,
            "house_effect_treatment": "not_applicable_zero_polls",
            "weighted_polls": polls.copy(),
        }

    weighted = attach_poll_weights(polls, as_of=as_of)
    y = _poll_margins(weighted)
    sample = pd.to_numeric(weighted["sample_size"], errors="coerce").fillna(500.0)
    sample = sample.clip(lower=50.0).to_numpy(dtype=float)
    quality = pd.to_numeric(
        weighted.get("quality_weight", pd.Series(1.0, index=weighted.index)),
        errors="coerce",
    ).fillna(1.0).clip(lower=0.2, upper=1.25).to_numpy(dtype=float)
    influence = pd.to_numeric(weighted["influence_weight"], errors="coerce")
    influence = influence.fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
    # Party-direction house-effect estimates are not portable to an Independent
    # target.  Quality, recency, sample size, clustering, and pollster caps are.
    sampling_sd = 100.0 / np.sqrt(sample) / np.sqrt(quality)
    observation_var = np.square(sampling_sd) + POLL_ERROR_FLOOR**2
    info = influence / observation_var
    prior_precision = 1.0 / float(prior_sd) ** 2
    precision = prior_precision + float(info.sum())
    mean = (
        float(prior_mean) * prior_precision + float(np.dot(info, y))
    ) / precision
    posterior_sd = float(np.sqrt(1.0 / precision))
    predictive_sd = float(np.sqrt(posterior_sd**2 + STRUCTURAL_PREDICTIVE_SD**2))
    enop = race_enop_summary(weighted)
    poll_location = float(np.dot(info, y) / info.sum()) if float(info.sum()) > 0 else None
    return {
        "mean": float(mean),
        "posterior_sd": posterior_sd,
        "predictive_sd": predictive_sd,
        "n_polls": len(weighted),
        "enop": float(next(iter(enop.values()), 0.0)),
        "poll_ids": sorted(weighted["poll_id"].astype(str).tolist()),
        "poll_weighted_location": poll_location,
        "house_effect_treatment": "not_applied_party_direction_not_portable",
        "weighted_polls": weighted,
    }


def _common_shock(base_fit: FitResult | None, n_draws: int, rng: np.random.Generator) -> np.ndarray:
    if base_fit is None or base_fit.draws_margin.size == 0:
        z = rng.standard_normal(int(n_draws))
    else:
        draws = np.asarray(base_fit.draws_margin, dtype=float)
        if draws.shape[0] != int(n_draws):
            raise ValueError("ordinary and exceptional draws must have the same row count")
        scale = np.asarray(base_fit.sd_margin, dtype=float)
        scale = np.where(scale > 1e-9, scale, 1.0)
        z = np.mean((draws - np.asarray(base_fit.mean_margin)) / scale, axis=1)
    z = np.asarray(z, dtype=float)
    z = z - float(z.mean())
    sd = float(z.std())
    if sd <= 1e-9:
        z = rng.standard_normal(int(n_draws))
        z -= float(z.mean())
        sd = float(z.std())
    return z / sd


def fit_non_major_adapter(
    races: pd.DataFrame,
    polls: pd.DataFrame,
    *,
    as_of: str | date,
    n_draws: int,
    seed: int,
    base_fit: FitResult | None = None,
    prior_mean: float | None = None,
    prior_sd: float = PRIOR_SD,
    prior_mode: str = "state_structural",
    common_variance_share: float = COMMON_VARIANCE_SHARE,
) -> FitResult:
    """Fit supported exceptional races and return candidate-neutral margin draws."""

    cutoff = pd.Timestamp(as_of).date()
    rng = np.random.default_rng(int(seed))
    common = _common_shock(base_fit, int(n_draws), rng)
    common_share = float(common_variance_share)
    if not 0.0 <= common_share <= 1.0:
        raise ValueError("common_variance_share must be between zero and one")
    race_ids: list[str] = []
    states: list[str] = []
    columns: list[np.ndarray] = []
    records: list[dict[str, Any]] = []

    active = races[~races.get("not_up", pd.Series(False, index=races.index)).fillna(False)]
    for _, race in active.iterrows():
        race_id = str(race.get("race_id") or "")
        race_polls = polls[polls.get(
            "race_id", pd.Series("", index=polls.index),
        ).astype(str).eq(race_id)].copy()
        race_polls = _candidate_compatible_polls(race_polls, race)
        structural_raw = pd.to_numeric(race.get("prior_lean"), errors="coerce")
        structural_available = bool(pd.notna(structural_raw))
        if prior_mode == "state_structural":
            race_prior_mean = float(structural_raw) if structural_available else np.nan
        elif prior_mode == "zero_centered":
            race_prior_mean = float(PRIOR_MEAN if prior_mean is None else prior_mean)
        elif prior_mode == "fixed":
            if prior_mean is None or not np.isfinite(float(prior_mean)):
                raise ValueError("fixed exceptional prior requires a finite prior_mean")
            race_prior_mean = float(prior_mean)
        else:
            raise ValueError(f"unknown exceptional prior_mode: {prior_mode}")
        support_row = race.copy()
        if prior_mode != "state_structural":
            support_row["prior_lean"] = race_prior_mean
            support_row["prior_production_eligible"] = True
        supported, support_status, support_reason = probability_support_status(
            support_row, n_compatible_polls=len(race_polls),
        )
        if prior_mode == "state_structural" and not structural_available:
            supported = False
            support_status = "withheld"
            support_reason = "eligible_state_structural_prior_missing"
        if not non_major_identity_supported(race):
            continue
        record: dict[str, Any] = {
            "race_id": race_id,
            "state": str(race.get("state") or ""),
            "modeled_candidate_id": race.get("modeled_candidate_id"),
            "opposing_candidate_id": race.get("opposing_candidate_id"),
            "modeled_ballot_party": race.get("modeled_ballot_party"),
            "opposing_ballot_party": race.get("opposing_ballot_party"),
            "modeled_caucus": race.get("modeled_caucus"),
            "opposing_caucus": race.get("opposing_caucus"),
            "support_status": support_status,
            "support_reason": support_reason,
            "n_candidate_compatible_polls": len(race_polls),
            "prior_mode": prior_mode,
            "prior_location": None if not np.isfinite(race_prior_mean) else race_prior_mean,
            "prior_sd": float(prior_sd),
            "prior_only": len(race_polls) == 0,
        }
        if not supported:
            records.append(record)
            continue
        aggregate = _posterior_location(
            race_polls,
            as_of=cutoff,
            prior_mean=race_prior_mean,
            prior_sd=prior_sd,
        )
        idiosyncratic = rng.standard_t(5.0, size=int(n_draws)) / np.sqrt(5.0 / 3.0)
        idiosyncratic -= float(idiosyncratic.mean())
        idiosyncratic /= float(idiosyncratic.std())
        shock = (
            np.sqrt(common_share) * common
            + np.sqrt(1.0 - common_share) * idiosyncratic
        )
        draw = float(aggregate["mean"]) + float(aggregate["predictive_sd"]) * shock
        race_ids.append(race_id)
        states.append(str(race.get("state") or ""))
        columns.append(draw)
        records.append({
            **record,
            "poll_ids": aggregate["poll_ids"],
            "enop": aggregate["enop"],
            "posterior_location": aggregate["mean"],
            "posterior_sd": aggregate["posterior_sd"],
            "predictive_sd": aggregate["predictive_sd"],
            "poll_weighted_location": aggregate["poll_weighted_location"],
            "poll_location_shift": (
                None if aggregate["poll_weighted_location"] is None
                else float(aggregate["mean"] - race_prior_mean)
            ),
            "house_effect_treatment": aggregate["house_effect_treatment"],
        })

    draws = np.column_stack(columns) if columns else np.empty((int(n_draws), 0))
    return FitResult(
        race_ids=race_ids,
        states=states,
        mean_margin=draws.mean(axis=0) if columns else np.empty(0),
        sd_margin=draws.std(axis=0) if columns else np.empty(0),
        draws_margin=draws,
        house_effects={},
        diagnostics={
            "adapter_spec_version": ADAPTER_SPEC_VERSION,
            "method": NON_MAJOR_ADAPTER_METHOD,
            "target": NON_MAJOR_TARGET,
            "prior": {
                "mode": prior_mode,
                "mean": None if prior_mode == "state_structural" else float(
                    PRIOR_MEAN if prior_mean is None else prior_mean
                ),
                "sd": float(prior_sd),
                "kind": (
                    STRUCTURAL_PRIOR_METHOD
                    if prior_mode == "state_structural" else "fixed_weak_normal_comparator"
                ),
                "estimated_from_exceptional_outcomes": False,
            },
            "poll_error_floor": POLL_ERROR_FLOOR,
            "structural_predictive_sd": STRUCTURAL_PREDICTIVE_SD,
            "common_variance_share": common_share,
            "records": records,
            "validation_class": "limited_validation_exception_model",
        },
        method=NON_MAJOR_ADAPTER_METHOD,
    )


def ordinary_model_snapshot(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    """Copy a snapshot with only ordinary D-v-R active rows for standard models."""

    races = snapshot.races.copy()
    held = races.get("not_up", pd.Series(False, index=races.index)).fillna(False).astype(bool)
    if "binary_score_eligible" in races.columns:
        ordinary = races["binary_score_eligible"].fillna(True).astype(bool)
    else:
        ordinary = pd.Series(True, index=races.index)
    selected = races[held | ordinary].copy().reset_index(drop=True)
    race_ids = set(selected["race_id"].astype(str))
    polls = snapshot.polls[
        snapshot.polls.get("race_id", pd.Series("", index=snapshot.polls.index)).astype(str).isin(race_ids)
    ].copy().reset_index(drop=True)
    return replace(snapshot, races=selected, polls=polls)


def merge_fit_results(ordinary: FitResult, exceptional: FitResult) -> FitResult:
    """Append exceptional correlated columns without altering ordinary columns."""

    if not exceptional.race_ids:
        return ordinary
    if ordinary.draws_margin.shape[0] != exceptional.draws_margin.shape[0]:
        raise ValueError("ordinary and exceptional fits require aligned draw rows")
    overlap = set(ordinary.race_ids).intersection(exceptional.race_ids)
    if overlap:
        raise ValueError(f"exceptional adapter duplicated ordinary races: {sorted(overlap)}")
    draws = np.column_stack([ordinary.draws_margin, exceptional.draws_margin])
    return FitResult(
        race_ids=[*ordinary.race_ids, *exceptional.race_ids],
        states=[*ordinary.states, *exceptional.states],
        mean_margin=np.concatenate([ordinary.mean_margin, exceptional.mean_margin]),
        sd_margin=np.concatenate([ordinary.sd_margin, exceptional.sd_margin]),
        draws_margin=draws,
        house_effects=dict(ordinary.house_effects),
        diagnostics={
            **(ordinary.diagnostics or {}),
            "exceptional_adapter": exceptional.diagnostics,
            "ordinary_columns_preserved": True,
        },
        method=f"{ordinary.method}+{NON_MAJOR_ADAPTER_METHOD}",
    )
