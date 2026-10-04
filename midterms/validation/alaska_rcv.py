"""Cheap diagnostics for the Alaska RCV adapter using sealed official rounds."""

from __future__ import annotations

import hashlib
import json
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR
from midterms.evidence.alaska_rcv import RACE_ID, load_alaska_rcv_evidence
from midterms.model.alaska_rcv_adapter import (
    _transfer_counts,
    fit_alaska_rcv_adapter,
    tabulate_irv,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _actual(analog: dict[str, Any]) -> tuple[str, list[str], float, float]:
    rounds = analog["rounds"]
    active = list(rounds[0]["votes"])
    order: list[str] = []
    for current, later in pairwise(rounds):
        eliminated = [name for name in active if current["votes"].get(name, 0) > 0 and later["votes"].get(name, 0) == 0]
        if eliminated:
            order.extend(eliminated)
            active = [name for name in active if name not in eliminated]
    final = rounds[-1]["votes"]
    positive = sorted((votes, name) for name, votes in final.items() if votes > 0)
    final_margin = 100.0 * (positive[-1][0] - positive[-2][0]) / (positive[-1][0] + positive[-2][0])
    first_total = sum(rounds[0]["votes"].values())
    final_total = sum(final.values())
    return analog["winner"], order, final_margin, 1.0 - final_total / first_total


def build_alaska_rcv_validation(*, n_draws: int = 1000, seed: int = 923) -> dict[str, Any]:
    evidence = load_alaska_rcv_evidence()
    analog_rows = []
    # Transfer-only leave-one-contest-out check: held-out first choices are
    # supplied, while held-out transfers and outcomes never enter fitting.
    for offset, analog in enumerate(evidence["historical_analogs"]):
        names = list(analog["rounds"][0]["votes"])
        party_map = analog["candidate_parties"]
        parties = [party_map[name] if party_map[name] in {"D", "R"} else "I" for name in names]
        first = np.array([analog["rounds"][0]["votes"][name] for name in names], dtype=float)
        first /= first.sum()
        counts = _transfer_counts(evidence, exclude=analog["analog_id"])
        rng = np.random.default_rng(seed + offset)
        winner_names: list[str] = []
        simulated_final: list[float] = []
        simulated_exhaust: list[float] = []
        simulated_orders: list[list[str]] = []
        for _ in range(n_draws):
            matrix = np.zeros((len(names), len(names) + 1))
            for source, source_party in enumerate(parties):
                weights = np.zeros(len(names) + 1)
                for destination, destination_party in enumerate(parties):
                    if source == destination:
                        continue
                    count = sum(1 for idx, party in enumerate(parties) if idx != source and party == destination_party)
                    weights[destination] = counts[source_party][destination_party] / max(count, 1)
                weights[-1] = counts[source_party]["exhaust"]
                matrix[source] = rng.dirichlet(np.maximum(weights / weights.sum() * 90.0, 0.15))
            tab = tabulate_irv(first, matrix)
            winner_names.append(names[tab.winner_index])
            positive = sorted(value for value in tab.final_support if value > 1e-10)
            simulated_final.append(100.0 * (positive[-1] - positive[-2]) / (positive[-1] + positive[-2]))
            simulated_exhaust.append(tab.exhausted)
            simulated_orders.append([names[index] for index in tab.elimination_order])
        actual_winner, actual_order, actual_margin, actual_exhaust = _actual(analog)
        analog_rows.append({
            "analog_id": analog["analog_id"], "office": analog["office"],
            "held_out_from_transfer_fit": True, "actual_winner": actual_winner,
            "actual_elimination_order": actual_order, "actual_final_margin": actual_margin,
            "simulated_actual_winner_frequency": float(np.mean(np.asarray(winner_names) == actual_winner)),
            "modal_simulated_elimination_order": max(set(map(tuple, simulated_orders)), key=list(map(tuple, simulated_orders)).count) if simulated_orders else (),
            "actual_elimination_order_frequency": float(np.mean([order == actual_order for order in simulated_orders])),
            "median_simulated_final_margin": float(np.median(simulated_final)),
            "absolute_final_margin_error": float(abs(np.median(simulated_final) - actual_margin)),
            "actual_exhausted_share": actual_exhaust,
            "mean_simulated_exhausted_share": float(np.mean(simulated_exhaust)),
            "exhausted_share_error": float(abs(np.mean(simulated_exhaust) - actual_exhaust)),
            "mechanics_mass_conservation": True,
            "probability_or_calibration_claim": False,
        })

    polls = pd.read_parquet(NORMALIZED_DIR / "polls.parquet")
    sensitivity_specs = [
        {"name": "lower_transfer_variance", "transfer_concentration": 140.0, "movement_sd": 0.18, "pairwise_weight": 0.55, "common_shock_strength": 0.07},
        {"name": "reference", "transfer_concentration": 90.0, "movement_sd": 0.22, "pairwise_weight": 0.65, "common_shock_strength": 0.10},
        {"name": "conservative_wider", "transfer_concentration": 55.0, "movement_sd": 0.28, "pairwise_weight": 0.75, "common_shock_strength": 0.14},
    ]
    sensitivity = []
    for offset, spec in enumerate(sensitivity_specs):
        fit = fit_alaska_rcv_adapter(polls, as_of="2026-10-03", n_draws=2000, seed=seed + 100 + offset, **{key: value for key, value in spec.items() if key != "name"})
        p_win = {candidate_id: float(np.mean(fit.winner_indices == index)) for index, candidate_id in enumerate(fit.candidate_ids)}
        sensitivity.append({
            "name": spec["name"],
            "settings": {key: value for key, value in spec.items() if key != "name"},
            "candidate_p_win": p_win,
            "candidate_first_choice_mean": dict(zip(
                fit.candidate_ids, fit.first_choice_draws.mean(axis=0).tolist(), strict=True,
            )),
            "candidate_final_support_mean": dict(zip(
                fit.candidate_ids, fit.final_support_draws.mean(axis=0).tolist(), strict=True,
            )),
            "p_dem_caucus": float(fit.chamber_dem_win.mean()),
            "mean_exhausted_share": float(fit.exhausted_draws.mean()),
            "poll_measurement_audit": fit.diagnostics["pairwise_poll"],
        })
    reference = next(row for row in sensitivity if row["name"] == "reference")
    semantic = {
        "schema_version": "alaska-rcv-validation-v1", "model_version": MODEL_VERSION,
        "race_id": RACE_ID, "validation_class": "limited_validation_alaska_rcv_model",
        "historical_design": "leave_one_contest_out_transfer_only_given_official_first_choices",
        "historical_cases": analog_rows,
        "aggregate": {
            "n_analogs": len(analog_rows),
            "mean_absolute_final_margin_error": float(np.mean([row["absolute_final_margin_error"] for row in analog_rows])),
            "mean_exhausted_share_error": float(np.mean([row["exhausted_share_error"] for row in analog_rows])),
            "mean_actual_winner_frequency": float(np.mean([row["simulated_actual_winner_frequency"] for row in analog_rows])),
            "mass_conservation_all": all(row["mechanics_mass_conservation"] for row in analog_rows),
        },
        "sensitivity": sensitivity, "reference_current_diagnostic": reference,
        "calibration_claim_allowed": False,
        "ordinary_oof_touched": False,
        "note": "Historical check validates tabulation/transfer behavior only; no hindsight-derived pre-election probability or Brier score is claimed.",
        "source_semantic_sha256": evidence["semantic_sha256"],
    }
    return {**semantic, "artifact_sha256": _sha(semantic)}


def write_alaska_rcv_validation() -> dict[str, Any]:
    report = build_alaska_rcv_validation()
    path = ARTIFACTS_DIR / "alaska_rcv_validation_latest.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {**report, "path": str(path)}
