"""Cycle-cross-fitted reliability for the frozen production stack procedure.

This module never fits a forecasting model. It reads already-frozen outer-fold
predictive distributions, performs training-cycle-only component screening and
mixture-weight fitting, then evaluates each untouched outer cycle once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR, ROOT
from midterms.model.empirical_mixture import (
    evaluate_predictive_mixture,
    mixture_crps_scale_grid,
)
from midterms.validation.artifact_lineage import frozen_index_semantic_sha256
from midterms.validation.metrics import (
    calibration_slope_intercept,
    reliability_bins,
    reliability_overconfidence,
)
from midterms.validation.nested_component_loo import _g8_recommendations
from midterms.validation.stack_weights import (
    NESTED_LOO_PATH,
    STACK_WEIGHTS_PATH,
    fit_stack_weights_from_oof,
    verify_reproducible,
)

SCHEMA_VERSION = "stack_reliability_crossfit_v1"
PROCEDURE = "cycle_cross_fitted_production_stack"
DEFAULT_SCALE_GRID = tuple(round(1.0 + 0.05 * index, 2) for index in range(21))
DEFAULT_OUT_PATH = ARTIFACTS_DIR / "stack_reliability_crossfit_latest.json"


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str,
    ).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _case_cycle(case_id: str) -> int:
    try:
        return int(str(case_id).split(":", 1)[0])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OOF case ID lacks an outer-cycle prefix: {case_id!r}") from exc


def _case_lead(case_id: str) -> int:
    try:
        return int(str(case_id).split(":", 2)[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"OOF case ID lacks a lead-day prefix: {case_id!r}") from exc


def _subset_draws(
    draws: Mapping[str, Mapping[str, Sequence[float]]], case_ids: set[str]
) -> dict[str, dict[str, Sequence[float]]]:
    return {
        str(model): {
            str(case): values for case, values in cases.items() if case in case_ids
        }
        for model, cases in draws.items()
    }


def split_outer_cycle(
    nested: Mapping[str, Any], heldout_cycle: int
) -> dict[str, Any]:
    """Split a frozen archive before any fitting function receives truth."""
    years = [int(year) for year in nested.get("years") or []]
    if heldout_cycle not in years:
        raise ValueError(f"held-out cycle {heldout_cycle} is not in {years}")
    truths = {str(case): float(value) for case, value in (nested.get("oof_truths") or {}).items()}
    draws = nested.get("oof_draws") or {}
    training_cycles = [year for year in years if year != heldout_cycle]
    training_ids = {case for case in truths if _case_cycle(case) in training_cycles}
    heldout_ids = {case for case in truths if _case_cycle(case) == heldout_cycle}
    if not training_ids or not heldout_ids or training_ids & heldout_ids:
        raise ValueError("outer-cycle split is empty or overlapping")
    training_crps = {
        str(year): dict((nested.get("crps_by_fold") or {}).get(str(year)) or {})
        for year in training_cycles
    }
    return {
        "training_cycles": training_cycles,
        "training_crps_by_fold": training_crps,
        "training_truths": {case: truths[case] for case in sorted(training_ids)},
        "training_draws": _subset_draws(draws, training_ids),
        "heldout_cycle": int(heldout_cycle),
        "heldout_truths": {case: truths[case] for case in sorted(heldout_ids)},
        "heldout_draws": _subset_draws(draws, heldout_ids),
    }


def select_uncertainty_scale(
    draws: Mapping[str, Mapping[str, Sequence[float]]],
    truths: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    scales: Sequence[float] = DEFAULT_SCALE_GRID,
    seed: int = 21,
    max_draws: int = 128,
) -> dict[str, Any]:
    """Choose one predeclared scale on training CRPS; ties prefer 1.0."""
    scores = mixture_crps_scale_grid(
        draws, truths, weights, scales, seed=seed, max_draws=max_draws,
    )
    best_score = min(float(row["empirical_crps"]) for row in scores)
    tied = [
        row for row in scores
        if float(row["empirical_crps"]) <= best_score + 1e-12
    ]
    chosen = min(tied, key=lambda row: (abs(float(row["scale"]) - 1.0), float(row["scale"])))
    return {
        "objective": "training_empirical_margin_crps",
        "tie_break": "closest_to_1.0_then_lower",
        "selected_scale": float(chosen["scale"]),
        "selected_training_crps": float(chosen["empirical_crps"]),
        "candidate_scores": scores,
        "seed": int(seed),
        "max_draws": int(max_draws),
    }


def fit_training_plan(
    *,
    training_cycles: Sequence[int],
    training_crps_by_fold: Mapping[str, Mapping[str, float]],
    training_draws: Mapping[str, Mapping[str, Sequence[float]]],
    training_truths: Mapping[str, float],
    spine: str,
    temperature: float,
    seed: int,
    max_draws: int,
    scales: Sequence[float] = DEFAULT_SCALE_GRID,
) -> dict[str, Any]:
    """Fit screening, weights, and scale with training-cycle data only.

    The function signature intentionally has no held-out truth argument.
    """
    g8 = _g8_recommendations(dict(training_crps_by_fold), spine=str(spine))
    fitted = fit_stack_weights_from_oof(
        dict(training_crps_by_fold),
        g8_recommendations=g8,
        temperature=float(temperature),
        oof_draws=dict(training_draws),
        oof_truths=dict(training_truths),
        seed=int(seed),
        max_draws=int(max_draws),
    )
    weights = fitted["stack_weights_production"]
    eligible_draws = {model: training_draws[model] for model in weights}
    scale_selection = select_uncertainty_scale(
        eligible_draws,
        training_truths,
        weights,
        scales=scales,
        seed=seed,
        max_draws=max_draws,
    )
    considered = sorted({
        model for fold in training_crps_by_fold.values() for model in fold
    })
    plan = {
        "training_cycles": [int(year) for year in training_cycles],
        "n_training_cases": len(training_truths),
        "training_case_ids_sha256": _canonical_sha256(sorted(training_truths)),
        "training_truth_sha256": _canonical_sha256(training_truths),
        "models_considered": considered,
        "g8_training_only_recommendations": g8,
        "eligible_models": sorted(weights),
        "excluded_g8_disable": fitted.get("excluded_g8_disable") or [],
        "excluded_structural": fitted.get("excluded_structural") or [],
        "excluded_missing_predictions": fitted.get("excluded_missing_predictions") or {},
        "stack_weights": weights,
        "stack_fit": {
            "method": fitted.get("stacking_mode"),
            "objective_crps": (fitted.get("mixture_diagnostics") or {}).get("objective_crps"),
            "prediction_sha256": fitted.get("prediction_sha256"),
            "seed": int(seed),
            "max_draws": int(max_draws),
        },
        "scale_selection": scale_selection,
        "heldout_truth_available_to_fit": False,
        "heldout_scores_available_to_fit": False,
    }
    plan["plan_sha256"] = _canonical_sha256(plan)
    return plan


def _pit_summary(values: Sequence[float], *, n_bins: int = 10) -> dict[str, Any]:
    pits = np.asarray(values, dtype=np.float64)
    pits = pits[np.isfinite(pits)]
    if not len(pits):
        return {"n": 0, "ok": False}
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    hist = []
    for index in range(n_bins):
        if index < n_bins - 1:
            count = int(np.sum((pits >= edges[index]) & (pits < edges[index + 1])))
        else:
            count = int(np.sum((pits >= edges[index]) & (pits <= edges[index + 1])))
        hist.append({
            "bin_lo": float(edges[index]),
            "bin_hi": float(edges[index + 1]),
            "n": count,
        })
    return {
        "n": len(pits),
        "mean": float(pits.mean()),
        "var": float(pits.var()),
        "expected_mean": 0.5,
        "expected_var": 1.0 / 12.0,
        "histogram": hist,
        "ok": True,
        "method": "weighted_empirical_mid_pit",
    }


def aggregate_case_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate case-level fixed-mixture evaluations."""
    if not rows:
        raise ValueError("cannot aggregate an empty held-out case set")
    probs = np.asarray([float(row["probability"]) for row in rows])
    outcomes = np.asarray([float(row["outcome"]) for row in rows])
    selected = np.where(outcomes > 0.5, probs, 1.0 - probs)
    rel = reliability_bins(probs, outcomes)
    gate = reliability_overconfidence(rel)
    return {
        "n": len(rows),
        "empirical_crps": float(np.mean([float(row["empirical_crps"]) for row in rows])),
        "brier": float(np.mean((probs - outcomes) ** 2)),
        "log_score": float(np.mean(np.log(np.clip(selected, 1e-12, 1.0)))),
        "log_score_orientation": "higher_is_better",
        "reliability": rel,
        "reliability_overconfidence": gate,
        "calibration_claim_allowed": bool(gate.get("calibration_claim_allowed")),
        "calibration_slope_intercept": calibration_slope_intercept(probs, outcomes),
        "pit": _pit_summary([float(row["pit"]) for row in rows]),
    }


def _summary_without_cases(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    return aggregate_case_metrics(evaluation["cases"])


def build_crossfit_report(
    nested: Mapping[str, Any],
    stack_source: Mapping[str, Any],
    *,
    source_lineage: Mapping[str, Any] | None = None,
    scales: Sequence[float] = DEFAULT_SCALE_GRID,
) -> dict[str, Any]:
    """Build the crossfit report from frozen artifacts already in memory."""
    years = [int(year) for year in nested.get("years") or []]
    leads = [int(lead) for lead in nested.get("lead_days") or []]
    if len(years) < 2 or not leads:
        raise ValueError("cycle crossfit needs at least two cycles and one lead")
    if nested.get("stack_training_protocol") == "formal_60_30_v1":
        if years != [2018, 2020, 2022, 2024] or leads != [60, 30]:
            raise ValueError("formal crossfit requires the declared four-cycle 60/30 grid")
        if nested.get("failures"):
            raise ValueError("formal crossfit source contains component failures")
        if nested.get("oof_crps_method") != "exact_empirical_predictive_draws":
            raise ValueError("formal crossfit requires empirical frozen-draw CRPS screening")
    if not nested.get("freeze_before_truth"):
        raise ValueError("nested source does not assert freeze-before-truth")
    draws = nested.get("oof_draws") or {}
    truths = nested.get("oof_truths") or {}
    if nested.get("frozen_draws_sha256") != _canonical_sha256(draws):
        raise ValueError("frozen predictive draw fingerprint is stale")
    if any(_case_cycle(case) not in years or _case_lead(case) not in leads for case in truths):
        raise ValueError("OOF case identity falls outside declared cycles/leads")
    seed = int(stack_source.get("optimizer_seed", 21))
    max_draws = int(stack_source.get("optimizer_max_draws", 128))
    temperature = float(stack_source.get("temperature", 0.75))
    spine = str(nested.get("spine_label") or "pymc")
    folds: dict[str, Any] = {}
    all_raw: list[dict[str, Any]] = []
    all_calibrated: list[dict[str, Any]] = []
    for heldout in years:
        split = split_outer_cycle(nested, heldout)
        plan = fit_training_plan(
            training_cycles=split["training_cycles"],
            training_crps_by_fold=split["training_crps_by_fold"],
            training_draws=split["training_draws"],
            training_truths=split["training_truths"],
            spine=spine,
            temperature=temperature,
            seed=seed,
            max_draws=max_draws,
            scales=scales,
        )
        weights = plan["stack_weights"]
        heldout_draws = {model: split["heldout_draws"][model] for model in weights}
        raw = evaluate_predictive_mixture(
            heldout_draws, split["heldout_truths"], weights,
            scale=1.0, seed=seed, max_draws=max_draws,
        )
        selected_scale = float(plan["scale_selection"]["selected_scale"])
        calibrated = evaluate_predictive_mixture(
            heldout_draws, split["heldout_truths"], weights,
            scale=selected_scale, seed=seed, max_draws=max_draws,
        )
        case_rows = []
        calibrated_by_case = {row["case_id"]: row for row in calibrated["cases"]}
        for raw_row in raw["cases"]:
            calibrated_row = calibrated_by_case[raw_row["case_id"]]
            case_rows.append({
                "case_id": raw_row["case_id"],
                "outer_cycle": int(heldout),
                "lead_days": _case_lead(raw_row["case_id"]),
                "truth_margin": raw_row["truth"],
                "outcome": raw_row["outcome"],
                "raw": {key: raw_row[key] for key in (
                    "probability", "predictive_mean", "empirical_crps", "pit",
                )},
                "calibrated": {key: calibrated_row[key] for key in (
                    "probability", "predictive_mean", "empirical_crps", "pit",
                )},
            })
        all_raw.extend(raw["cases"])
        all_calibrated.extend(calibrated["cases"])
        folds[str(heldout)] = {
            "outer_cycle": int(heldout),
            "training_cycles": split["training_cycles"],
            "n_heldout_cases": len(split["heldout_truths"]),
            "candidate_eligibility": {
                "considered": plan["models_considered"],
                "eligible": plan["eligible_models"],
                "g8_training_only": plan["g8_training_only_recommendations"],
                "excluded_g8_disable": plan["excluded_g8_disable"],
                "excluded_structural": plan["excluded_structural"],
                "excluded_missing_predictions": plan["excluded_missing_predictions"],
            },
            "stack_weights": weights,
            "stack_fit": plan["stack_fit"],
            "training_case_ids_sha256": plan["training_case_ids_sha256"],
            "training_truth_sha256": plan["training_truth_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "scale_selection": plan["scale_selection"],
            "raw": _summary_without_cases(raw),
            "calibrated": _summary_without_cases(calibrated),
            "heldout_cases": case_rows,
            "leakage_audit": {
                "heldout_truth_available_to_screening": False,
                "heldout_truth_available_to_weight_fit": False,
                "heldout_truth_available_to_scale_selection": False,
                "heldout_evaluated_once_after_plan_frozen": True,
            },
        }

    full_split_ids = {str(case) for case in truths}
    all_draws = _subset_draws(draws, full_split_ids)
    all_crps = {str(year): dict((nested.get("crps_by_fold") or {})[str(year)]) for year in years}
    final_plan = fit_training_plan(
        training_cycles=years,
        training_crps_by_fold=all_crps,
        training_draws=all_draws,
        training_truths={str(case): float(value) for case, value in truths.items()},
        spine=spine,
        temperature=temperature,
        seed=seed,
        max_draws=max_draws,
        scales=scales,
    )
    raw_aggregate = aggregate_case_metrics(all_raw)
    calibrated_aggregate = aggregate_case_metrics(all_calibrated)
    source = dict(source_lineage or {})
    source.update({
        "frozen_draws_sha256": nested.get("frozen_draws_sha256"),
        "truth_sha256": _canonical_sha256(truths),
        "nested_model_version": nested.get("model_version"),
        "nested_stack_training_protocol": nested.get("stack_training_protocol"),
        "stack_prediction_sha256": stack_source.get("prediction_sha256"),
        "stacking_mode": stack_source.get("stacking_mode"),
    })
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "procedure": PROCEDURE,
        "generated_at": datetime.now(UTC).isoformat(),
        "complete": True,
        "freeze_before_truth": (
            "Every outer-cycle plan is fit from other cycles only; held-out truth is read "
            "only after component eligibility, stack weights, and scale are frozen."
        ),
        "source": source,
        "outer_cycles": years,
        "lead_days": leads,
        "model_candidates_considered": sorted(draws),
        "optimizer": {
            "stack_method": "empirical_predictive_mixture_crps_v1",
            "seed": seed,
            "max_draws": max_draws,
            "temperature": temperature,
            "scale_grid": [float(value) for value in scales],
            "scale_objective": "training_empirical_margin_crps",
        },
        "folds": folds,
        "raw_production_stack": {
            **raw_aggregate,
            "label": "cycle_cross_fitted_production_stack",
            "calibration_applied": False,
            "g7_eligible": True,
        },
        "experimental_calibrated_stack": {
            **calibrated_aggregate,
            "label": "cycle_cross_fitted_candidate_calibration",
            "calibration_applied": True,
            "production_adopted": False,
            "g7_eligible": False,
        },
        "recommended_final_scale_if_adopted": {
            "value": float(final_plan["scale_selection"]["selected_scale"]),
            "status": "NOT_YET_PRODUCTION",
            "selection": final_plan["scale_selection"],
            "all_history_stack_weights": final_plan["stack_weights"],
            "note": "Diagnostic only; run_forecast does not apply this scale.",
        },
        "leakage_audit": {
            "outer_unit": "cycle",
            "screening_is_training_only": True,
            "weight_fit_is_training_only": True,
            "scale_selection_is_training_only": True,
            "heldout_truth_used_only_for_evaluation": True,
        },
    }
    semantic = {key: value for key, value in report.items() if key != "generated_at"}
    report["artifact_fingerprint_sha256"] = _canonical_sha256(semantic)
    return report


def validate_crossfit_artifact(
    payload: Mapping[str, Any],
    *,
    nested_path: Path = NESTED_LOO_PATH,
    stack_path: Path = STACK_WEIGHTS_PATH,
) -> dict[str, Any]:
    """Validate source lineage and ensure G7 can only consume the raw block."""
    failures: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported schema_version")
    if payload.get("procedure") != PROCEDURE or not payload.get("complete"):
        failures.append("crossfit procedure is incomplete or mislabeled")
    source = payload.get("source") or {}
    if not nested_path.is_file() or not stack_path.is_file():
        failures.append("source nested/stack artifact missing")
        nested: dict[str, Any] = {}
    else:
        nested = json.loads(nested_path.read_text(encoding="utf-8"))
        if source.get("nested_artifact_sha256") != _file_sha256(nested_path):
            failures.append("nested artifact fingerprint mismatch")
        if source.get("stack_artifact_sha256") != _file_sha256(stack_path):
            failures.append("stack artifact fingerprint mismatch")
        if source.get("frozen_draws_sha256") != nested.get("frozen_draws_sha256"):
            failures.append("frozen draw fingerprint mismatch")
        if source.get("truth_sha256") != _canonical_sha256(nested.get("oof_truths") or {}):
            failures.append("truth fingerprint mismatch")
        expected_index = source.get("frozen_index_semantic_sha256")
        index_path = nested_path.with_name(f"{nested_path.stem}_frozen.json")
        if expected_index and (
            not index_path.is_file() or frozen_index_semantic_sha256(index_path) != expected_index
        ):
            failures.append("frozen prediction index fingerprint mismatch")
    raw = payload.get("raw_production_stack") or {}
    experimental = payload.get("experimental_calibrated_stack") or {}
    if raw.get("calibration_applied") is not False or raw.get("g7_eligible") is not True:
        failures.append("raw production-stack block is not G7 eligible")
    if experimental.get("production_adopted") is not False or experimental.get("g7_eligible") is not False:
        failures.append("experimental calibration adoption status is unsafe")
    audit = payload.get("leakage_audit") or {}
    if not all(audit.get(key) is True for key in (
        "screening_is_training_only",
        "weight_fit_is_training_only",
        "scale_selection_is_training_only",
        "heldout_truth_used_only_for_evaluation",
    )):
        failures.append("crossfit leakage audit is incomplete")
    cycles = [int(value) for value in payload.get("outer_cycles") or []]
    folds = payload.get("folds") or {}
    if not cycles or cycles != [int(value) for value in nested.get("years") or []]:
        failures.append("outer cycles differ from the nested source")
    if ([int(value) for value in payload.get("lead_days") or []]
            != [int(value) for value in nested.get("lead_days") or []]):
        failures.append("lead days differ from the nested source")
    if int(raw.get("n") or 0) != len(nested.get("oof_truths") or {}):
        failures.append("raw crossfit case count differs from the nested truth set")
    for cycle in cycles:
        fold = folds.get(str(cycle)) or {}
        training_cycles = [int(value) for value in fold.get("training_cycles") or []]
        if cycle in training_cycles:
            failures.append(f"held-out cycle {cycle} appears in its training cycles")
        if sorted(training_cycles) != sorted(value for value in cycles if value != cycle):
            failures.append(f"outer cycle {cycle} does not use exactly the other cycles")
        fold_audit = fold.get("leakage_audit") or {}
        if not fold_audit.get("heldout_evaluated_once_after_plan_frozen"):
            failures.append(f"outer cycle {cycle} lacks freeze-before-evaluation evidence")
    semantic = {
        key: value for key, value in payload.items()
        if key not in {"generated_at", "artifact_fingerprint_sha256", "path"}
    }
    if payload.get("artifact_fingerprint_sha256") != _canonical_sha256(semantic):
        failures.append("crossfit artifact fingerprint mismatch")
    return {"ok": not failures, "failures": failures}


def write_stack_reliability_crossfit(
    *,
    nested_path: Path = NESTED_LOO_PATH,
    stack_path: Path = STACK_WEIGHTS_PATH,
    out_path: Path = DEFAULT_OUT_PATH,
    scales: Sequence[float] = DEFAULT_SCALE_GRID,
) -> dict[str, Any]:
    """Read frozen OOF artifacts, crossfit them, and write the diagnostic."""
    nested = json.loads(nested_path.read_text(encoding="utf-8"))
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
    reproduction = verify_reproducible(stack)
    if not reproduction.get("ok"):
        raise ValueError(f"production stack source failed reproduction: {reproduction}")
    index_path = nested_path.with_name(f"{nested_path.stem}_frozen.json")
    lineage = {
        "nested_artifact_path": _portable_path(nested_path),
        "nested_artifact_sha256": _file_sha256(nested_path),
        "stack_artifact_path": _portable_path(stack_path),
        "stack_artifact_sha256": _file_sha256(stack_path),
        "frozen_index_path": _portable_path(index_path),
        "frozen_index_semantic_sha256": frozen_index_semantic_sha256(index_path),
    }
    report = build_crossfit_report(
        nested, stack, source_lineage=lineage, scales=scales,
    )
    report["path"] = _portable_path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    validation = validate_crossfit_artifact(
        report, nested_path=nested_path, stack_path=stack_path,
    )
    if not validation["ok"]:
        out_path.unlink(missing_ok=True)
        raise ValueError(f"written crossfit artifact failed validation: {validation}")
    return report
