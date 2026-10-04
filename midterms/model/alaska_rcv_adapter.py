"""Limited-validation Alaska ranked-choice adapter.

This model is intentionally separate from the ordinary D-v-R stack.  It uses
official primary first choices, pairwise polls only for Peltola/incumbent
relative preference, and uncertain transfer flows learned from official Alaska
RCV round reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from midterms.evidence.alaska_rcv import (
    METHOD,
    RACE_ID,
    classify_pairwise_polls,
    load_alaska_rcv_evidence,
)
from midterms.model.poll_weights import attach_poll_weights
from midterms.model.pymc_model import FitResult

WRITE_IN_ID = f"{RACE_ID}:write-in-aggregate"
ADAPTER_SPEC_VERSION = "alaska-rcv-adapter-v1"
FIRST_CHOICE_SPEC_VERSION = "official-primary-anchor-v1"
TRANSFER_MODEL_SPEC_VERSION = "official-rcv-party-transfer-dirichlet-v1"
TABULATION_SPEC_VERSION = "continuing-ballot-irv-v1"
SENSITIVITY_SPEC_VERSION = "alaska-rcv-sensitivity-grid-v1"
CAUCUS_POLICY_VERSION = "explicit-candidate-caucus-v1"
REFERENCE_TRANSFER_CONCENTRATION = 90.0
REFERENCE_MOVEMENT_SD = 0.22
REFERENCE_PAIRWISE_WEIGHT = 0.65
REFERENCE_COMMON_SHOCK_STRENGTH = 0.10


def adapter_specification() -> dict[str, Any]:
    """Return the deterministic, probability-affecting Alaska adapter contract."""

    return {
        "adapter_spec_version": ADAPTER_SPEC_VERSION,
        "method": METHOD,
        "target": "candidate_win_probability_and_chamber_caucus_probability",
        "first_choice_spec_version": FIRST_CHOICE_SPEC_VERSION,
        "transfer_model_spec_version": TRANSFER_MODEL_SPEC_VERSION,
        "tabulation_spec_version": TABULATION_SPEC_VERSION,
        "sensitivity_spec_version": SENSITIVITY_SPEC_VERSION,
        "caucus_policy_version": CAUCUS_POLICY_VERSION,
        "reference_settings": {
            "transfer_concentration": REFERENCE_TRANSFER_CONCENTRATION,
            "movement_sd": REFERENCE_MOVEMENT_SD,
            "pairwise_weight": REFERENCE_PAIRWISE_WEIGHT,
            "common_shock_strength": REFERENCE_COMMON_SHOCK_STRENGTH,
        },
        "pairwise_poll_contract": (
            "relative Peltola/incumbent-Sullivan preference; never RCV first choice"
        ),
        "write_in_treatment": "aggregate_residual_fail_closed_if_winner",
    }


@dataclass
class RCVTabulation:
    winner_index: int
    rounds: list[dict[str, Any]]
    final_support: np.ndarray
    exhausted: float
    elimination_order: list[int]


@dataclass
class AlaskaRCVFit:
    race_id: str
    candidate_ids: list[str]
    candidate_names: list[str]
    ballot_parties: list[str]
    caucuses: list[str | None]
    first_choice_draws: np.ndarray
    final_support_draws: np.ndarray
    winner_indices: np.ndarray
    exhausted_draws: np.ndarray
    elimination_orders: list[list[int]]
    chamber_dem_win: np.ndarray
    diagnostics: dict[str, Any]


def tabulate_irv(
    first_choice: np.ndarray,
    transfer_matrix: np.ndarray,
) -> RCVTabulation:
    """Tabulate one Alaska-style IRV draw using continuing-ballot majority."""
    support = np.asarray(first_choice, dtype=float).copy()
    matrix = np.asarray(transfer_matrix, dtype=float)
    n = len(support)
    if support.shape != (n,) or matrix.shape != (n, n + 1):
        raise ValueError("transfer matrix must be candidates x (candidates + exhaustion)")
    if np.any(support < 0) or not np.isclose(support.sum(), 1.0, atol=1e-9):
        raise ValueError("first-choice support must be nonnegative and sum to one")
    active = list(range(n))
    exhausted = 0.0
    rounds: list[dict[str, Any]] = []
    eliminated: list[int] = []
    while active:
        continuing = float(support[active].sum())
        shares = {index: float(support[index] / continuing) for index in active}
        rounds.append({"continuing": continuing, "support": support.copy(), "shares": shares})
        winner = max(active, key=lambda index: (support[index], -index))
        if support[winner] > continuing / 2.0 or len(active) == 1:
            return RCVTabulation(winner, rounds, support, exhausted, eliminated)
        loser = min(active, key=lambda index: (support[index], index))
        mass = float(support[loser])
        support[loser] = 0.0
        active.remove(loser)
        eliminated.append(loser)
        allowed = np.zeros(n + 1, dtype=float)
        allowed[active] = matrix[loser, active]
        allowed[-1] = matrix[loser, -1]
        total = float(allowed.sum())
        if total <= 0:
            exhausted += mass
        else:
            allowed /= total
            support += mass * allowed[:n]
            exhausted += mass * float(allowed[-1])
        if not np.isclose(float(support.sum()) + exhausted, 1.0, atol=1e-9):
            raise AssertionError("RCV transfer lost vote mass")
    raise RuntimeError("IRV tabulation failed to produce a winner")


def _common_shock(base_fit: FitResult | None, n_draws: int, rng: np.random.Generator) -> np.ndarray:
    if base_fit is None or base_fit.draws_margin.size == 0:
        values = rng.standard_normal(n_draws)
    else:
        if base_fit.draws_margin.shape[0] != n_draws:
            raise ValueError("Alaska and ordinary draw rows must align")
        scale = np.where(np.asarray(base_fit.sd_margin) > 1e-9, base_fit.sd_margin, 1.0)
        values = np.mean((base_fit.draws_margin - base_fit.mean_margin) / scale, axis=1)
    values = np.asarray(values, dtype=float) - float(np.mean(values))
    sd = float(np.std(values))
    return values / sd if sd > 1e-9 else rng.standard_normal(n_draws)


def _transfer_counts(evidence: dict[str, Any], *, exclude: str | None = None) -> dict[str, dict[str, float]]:
    counts = {party: {"D": 1.0, "R": 1.0, "I": 1.0, "exhaust": 1.0} for party in ("D", "R", "I")}
    for analog in evidence["historical_analogs"]:
        if analog["analog_id"] == exclude:
            continue
        for transfer in analog["transfers"]:
            source = transfer["source_party"] if transfer["source_party"] in counts else "I"
            destination = "exhaust" if transfer["exhausted"] else transfer.get("destination_party")
            destination = destination if destination in {"D", "R", "I", "exhaust"} else "I"
            counts[source][destination] += float(transfer["ballots"])
    return counts


def _poll_pairwise_location(polls: pd.DataFrame, cutoff: date) -> dict[str, Any]:
    compatible = classify_pairwise_polls(polls)
    if compatible.empty:
        return {"p_peltola": 0.5, "sd": 0.12, "n": 0, "poll_ids": [], "measurement_type": "none"}
    weighted = attach_poll_weights(compatible, as_of=cutoff)
    weights = pd.to_numeric(weighted["influence_weight"], errors="coerce").fillna(0).to_numpy(float)
    shares = pd.to_numeric(weighted["modeled_share"], errors="coerce").to_numpy(float) / 100.0
    if weights.sum() <= 0:
        weights = np.ones(len(weights))
    mean = float(np.average(shares, weights=weights))
    enop = float(weights.sum() ** 2 / np.square(weights).sum())
    sd = max(0.035, 0.10 / np.sqrt(max(enop, 1.0)))
    measurement_counts = {
        str(key): int(value)
        for key, value in weighted["measurement_type"].value_counts().sort_index().items()
    }
    return {
        "p_peltola": mean,
        "sd": sd,
        "n": len(weighted),
        "enop": enop,
        "poll_ids": sorted(weighted["poll_id"].astype(str)),
        "measurement_contract": "relative Peltola/incumbent-Sullivan preference; never RCV first choice",
        "measurement_type_counts": measurement_counts,
    }


def fit_alaska_rcv_adapter(
    polls: pd.DataFrame,
    *,
    as_of: str | date,
    n_draws: int,
    seed: int,
    base_fit: FitResult | None = None,
    transfer_concentration: float = REFERENCE_TRANSFER_CONCENTRATION,
    movement_sd: float = REFERENCE_MOVEMENT_SD,
    pairwise_weight: float = REFERENCE_PAIRWISE_WEIGHT,
    common_shock_strength: float = REFERENCE_COMMON_SHOCK_STRENGTH,
    analog_exclude: str | None = None,
    first_choice_override: np.ndarray | None = None,
) -> AlaskaRCVFit:
    evidence = load_alaska_rcv_evidence()
    cutoff = pd.Timestamp(as_of).date()
    rng = np.random.default_rng(seed)
    candidates = [*evidence["candidate_field"], {"candidate_id": WRITE_IN_ID, "name": "Certified write-ins (aggregate)", "ballot_party": "I", "caucus": None}]
    ids = [row["candidate_id"] for row in candidates]
    names = [row["name"] for row in candidates]
    parties = [row["ballot_party"] for row in candidates]
    caucuses = [row.get("caucus") for row in candidates]
    result_by_id = {row["candidate_id"]: row for row in evidence["primary_results"]}
    anchor = np.array([float(result_by_id[candidate_id]["share"]) for candidate_id in ids])
    # Voters for defeated primary candidates are redistributed proportionally;
    # certified write-ins retain only their own observed primary anchor.
    anchor[:4] += (1.0 - anchor.sum()) * anchor[:4] / anchor[:4].sum()
    if first_choice_override is not None:
        anchor = np.asarray(first_choice_override, dtype=float)
        anchor /= anchor.sum()
    pairwise = _poll_pairwise_location(polls, cutoff)
    common = _common_shock(base_fit, n_draws, rng)
    counts = _transfer_counts(evidence, exclude=analog_exclude)
    first_draws = np.empty((n_draws, len(ids)))
    final_draws = np.zeros_like(first_draws)
    winners = np.empty(n_draws, dtype=int)
    exhausted = np.empty(n_draws)
    orders: list[list[int]] = []
    transfer_samples: list[np.ndarray] = []
    pairwise_logits = np.log(pairwise["p_peltola"] / (1 - pairwise["p_peltola"]))
    for draw in range(n_draws):
        noise = rng.normal(0.0, movement_sd, size=len(ids))
        noise[1] += common_shock_strength * common[draw]
        noise[[0, 2, 3]] -= common_shock_strength * common[draw] / 3.0
        probs = np.exp(np.log(np.maximum(anchor, 1e-8)) + noise)
        probs /= probs.sum()
        first = rng.dirichlet(np.maximum(probs * 260.0, 0.25))
        matrix = np.zeros((len(ids), len(ids) + 1))
        sampled_pairwise = float(np.clip(rng.normal(pairwise["p_peltola"], pairwise["sd"]), 0.05, 0.95))
        pair_shift = pairwise_weight * (np.log(sampled_pairwise / (1 - sampled_pairwise)) - pairwise_logits)
        for source in range(len(ids)):
            source_party = parties[source] if parties[source] in counts else "I"
            base = counts[source_party]
            weights = np.zeros(len(ids) + 1)
            for destination in range(len(ids)):
                if destination == source:
                    continue
                destination_party = parties[destination] if parties[destination] in {"D", "R"} else "I"
                same_party_count = sum(1 for idx, party in enumerate(parties) if idx != source and party == destination_party)
                weights[destination] = base[destination_party] / max(same_party_count, 1)
            weights[-1] = base["exhaust"]
            weights[1] *= np.exp(pair_shift / 2.0)
            weights[2] *= np.exp(-pair_shift / 2.0)
            alpha = np.maximum(weights / weights.sum() * transfer_concentration, 0.15)
            matrix[source] = rng.dirichlet(alpha)
        tab = tabulate_irv(first, matrix)
        first_draws[draw] = first
        final_draws[draw] = tab.final_support
        winners[draw] = tab.winner_index
        exhausted[draw] = tab.exhausted
        orders.append(tab.elimination_order)
        if draw < 100:
            transfer_samples.append(matrix)
    winner_caucuses = np.array([caucuses[index] for index in winners], dtype=object)
    if np.any(pd.isna(winner_caucuses)):
        raise ValueError("Alaska simulated a write-in winner without a defensible caucus mapping")
    diagnostics = {
        "method": METHOD, "validation_class": "limited_validation_alaska_rcv_model",
        "adapter_spec_version": ADAPTER_SPEC_VERSION,
        "adapter_specification": adapter_specification(),
        "candidate_ids": ids, "pairwise_poll": pairwise,
        "first_choice_anchor": dict(zip(ids, anchor.tolist(), strict=True)),
        "transfer_source": "official Alaska 2022/2024 RCV round transitions",
        "transfer_counts": counts, "transfer_concentration": transfer_concentration,
        "movement_sd": movement_sd, "pairwise_weight": pairwise_weight,
        "common_shock_strength": common_shock_strength,
        "write_in_treatment": evidence["write_in_treatment"],
        "mean_transfer_matrix_first_100": np.mean(transfer_samples, axis=0).tolist(),
        "source_semantic_sha256": evidence["semantic_sha256"],
    }
    return AlaskaRCVFit(RACE_ID, ids, names, parties, caucuses, first_draws, final_draws, winners, exhausted, orders, winner_caucuses == "D", diagnostics)


def as_fit_result(result: AlaskaRCVFit) -> FitResult:
    """Encode only the chamber caucus draw by sign; keep candidate truth in diagnostics."""
    margin = np.where(result.chamber_dem_win, 1.0, -1.0)[:, None]
    return FitResult(
        race_ids=[result.race_id], states=["AK"], mean_margin=margin.mean(axis=0),
        sd_margin=margin.std(axis=0), draws_margin=margin, house_effects={},
        diagnostics={
            "method": METHOD,
            "race_id": result.race_id,
            "candidate_ids": result.candidate_ids,
            "candidate_names": result.candidate_names,
            "ballot_parties": result.ballot_parties,
            "caucuses": result.caucuses,
            "winner_indices": result.winner_indices.tolist(),
            "first_choice_mean": result.first_choice_draws.mean(axis=0).tolist(),
            "final_support_mean": result.final_support_draws.mean(axis=0).tolist(),
            "exhausted_mean": float(result.exhausted_draws.mean()),
            "elimination_orders": result.elimination_orders,
            "uncertainty": result.diagnostics,
            "authoritative_binary_aliases": False,
        },
        method=METHOD,
    )


def merge_alaska_fit(ordinary_and_nonmajor: FitResult, alaska: FitResult) -> FitResult:
    if ordinary_and_nonmajor.draws_margin.shape[0] != alaska.draws_margin.shape[0]:
        raise ValueError("Alaska and base draws are not aligned")
    if set(ordinary_and_nonmajor.race_ids) & set(alaska.race_ids):
        raise ValueError("Alaska race already exists in base fit")
    draws = np.column_stack([ordinary_and_nonmajor.draws_margin, alaska.draws_margin])
    return FitResult(
        race_ids=[*ordinary_and_nonmajor.race_ids, *alaska.race_ids],
        states=[*ordinary_and_nonmajor.states, *alaska.states],
        mean_margin=draws.mean(axis=0), sd_margin=draws.std(axis=0), draws_margin=draws,
        house_effects=dict(ordinary_and_nonmajor.house_effects),
        diagnostics={**(ordinary_and_nonmajor.diagnostics or {}), "alaska_rcv_adapter": alaska.diagnostics, "ordinary_columns_preserved": True},
        method=f"{ordinary_and_nonmajor.method}+{METHOD}",
    )
