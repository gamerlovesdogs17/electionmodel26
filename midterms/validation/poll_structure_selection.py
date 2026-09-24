"""Leakage-safe selection of single-term poll-structure challengers.

This module consumes scores from already-frozen outer-cycle predictions.  It
never fits a forecasting model and never reads truth directly.  The expensive
OOF command is responsible for producing the score table after freezing each
candidate distribution.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.validation.artifact_lineage import require_current_model_version

SELECTION_SCHEMA_VERSION = "poll-structure-crossfit-v1"
SELECTION_RULE_VERSION = "single-addition-majority-parsimony-v1"
BASE_STRUCTURE = "pymc"
POLL_STRUCTURE_CANDIDATES = (
    BASE_STRUCTURE,
    "hier_plus_study_effect",
    "hier_plus_sponsor_effect",
    "hier_plus_questionnaire_effect",
)


def select_structure(
    scores_by_cycle: dict[str, dict[str, float]],
    *,
    candidates: tuple[str, ...] = POLL_STRUCTURE_CANDIDATES,
) -> dict[str, Any]:
    """Choose one candidate from training cycles using a parsimonious rule.

    A challenger must beat the base CRPS in a strict majority of comparable
    cycles and have lower pooled mean CRPS.  Ties, incomplete evidence, and
    conflicting weak evidence select the all-off base.  This uses no tuned
    improvement threshold.
    """
    base = candidates[0]
    cycles = sorted(str(cycle) for cycle in scores_by_cycle)
    base_values = [
        float(scores_by_cycle[cycle][base])
        for cycle in cycles
        if base in scores_by_cycle[cycle]
        and np.isfinite(float(scores_by_cycle[cycle][base]))
    ]
    if not base_values:
        raise ValueError("base poll structure has no finite training score")
    summaries: dict[str, Any] = {}
    qualified: list[tuple[int, float, str]] = []
    for candidate in candidates:
        paired: list[tuple[float, float, str]] = []
        for cycle in cycles:
            table = scores_by_cycle[cycle]
            if candidate not in table or base not in table:
                continue
            candidate_score = float(table[candidate])
            base_score = float(table[base])
            if np.isfinite(candidate_score) and np.isfinite(base_score):
                paired.append((candidate_score, base_score, cycle))
        wins = sum(candidate_score < base_score for candidate_score, base_score, _ in paired)
        ties = sum(candidate_score == base_score for candidate_score, base_score, _ in paired)
        mean_score = float(np.mean([row[0] for row in paired])) if paired else None
        mean_base = float(np.mean([row[1] for row in paired])) if paired else None
        strict_majority = bool(paired) and wins > len(paired) / 2
        lower_mean = bool(paired) and mean_score is not None and mean_base is not None and mean_score < mean_base
        eligible = candidate == base or (len(paired) == len(cycles) and strict_majority and lower_mean)
        summaries[candidate] = {
            "n_training_cycles": len(paired),
            "wins_vs_base": wins,
            "ties_vs_base": ties,
            "mean_crps": mean_score,
            "mean_base_crps": mean_base,
            "strict_majority": strict_majority,
            "lower_mean_crps": lower_mean,
            "eligible": eligible,
            "cycle_deltas_base_minus_candidate": {
                cycle: float(base_score - candidate_score)
                for candidate_score, base_score, cycle in paired
            },
        }
        if candidate != base and eligible and mean_score is not None:
            qualified.append((-wins, mean_score, candidate))
    selected = sorted(qualified)[0][2] if qualified else base
    return {
        "selection_rule_version": SELECTION_RULE_VERSION,
        "training_cycles": cycles,
        "candidate_set": list(candidates),
        "candidate_summaries": summaries,
        "selected_structure": selected,
        "parsimony_default": base,
        "tie_policy": "base",
        "metric": "empirical_predictive_margin_crps",
    }


def crossfit_structure_selection(
    scores_by_cycle: dict[str, dict[str, float]],
    *,
    source_nested_sha256: str,
    source_frozen_draws_sha256: str,
    model_version: str = MODEL_VERSION,
) -> dict[str, Any]:
    """Evaluate the selection procedure with each cycle untouched once."""
    cycles = sorted(str(cycle) for cycle in scores_by_cycle)
    folds: list[dict[str, Any]] = []
    selected_scores: list[float] = []
    reference_scores: list[float] = []
    for holdout in cycles:
        training = {cycle: scores_by_cycle[cycle] for cycle in cycles if cycle != holdout}
        selection = select_structure(training)
        selected = selection["selected_structure"]
        heldout = scores_by_cycle[holdout]
        if selected not in heldout or BASE_STRUCTURE not in heldout:
            raise ValueError(f"held-out cycle {holdout} lacks {selected} or base score")
        selected_score = float(heldout[selected])
        reference_score = float(heldout[BASE_STRUCTURE])
        selected_scores.append(selected_score)
        reference_scores.append(reference_score)
        folds.append({
            "outer_heldout_cycle": holdout,
            "training_cycles": selection["training_cycles"],
            "training_scores": training,
            "selection": selection,
            "selected_structure": selected,
            "heldout_score": selected_score,
            "reference_heldout_score": reference_score,
            "delta_reference_minus_selected": reference_score - selected_score,
            "fold_recommendation": "candidate" if selected != BASE_STRUCTURE else "base",
            "heldout_truth_used_for_selection": False,
        })
    final = select_structure(scores_by_cycle)
    return {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "model_version": model_version,
        "source_nested_loo_sha256": source_nested_sha256,
        "source_frozen_draws_sha256": source_frozen_draws_sha256,
        "candidate_set": list(POLL_STRUCTURE_CANDIDATES),
        "single_term_additions_only": True,
        "multi_term_interactions": "intentionally_deferred",
        "selection_rule_version": SELECTION_RULE_VERSION,
        "freeze_before_truth": True,
        "outer_folds": folds,
        "cross_fitted_aggregate": {
            "n_cycles": len(folds),
            "selected_procedure_mean_crps": float(np.mean(selected_scores)),
            "base_mean_crps": float(np.mean(reference_scores)),
            "delta_base_minus_selected_procedure": float(
                np.mean(reference_scores) - np.mean(selected_scores)
            ),
        },
        "final_production_candidate_recommendation": {
            **final,
            "status": "recommendation_pending_adoption",
            "evidence_scope": "all_available_historical_cycles_after_crossfit_evaluation",
            "untouched_fifth_cycle": False,
            "note": (
                "The empirical claim concerns the cross-fitted selection procedure. "
                "The all-history recommendation has no untouched fifth cycle."
            ),
        },
    }


def write_poll_structure_crossfit(
    nested_path: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Build the cheap selection artifact from an existing compatible OOF file."""
    nested_path = nested_path or (ARTIFACTS_DIR / "nested_component_loo.json")
    nested = json.loads(nested_path.read_text(encoding="utf-8"))
    require_current_model_version(nested, label="poll-structure OOF")
    scores = nested.get("crps_by_fold") or {}
    missing = {
        cycle: sorted(set(POLL_STRUCTURE_CANDIDATES) - set(table))
        for cycle, table in scores.items()
        if set(POLL_STRUCTURE_CANDIDATES) - set(table)
    }
    if not scores or missing:
        raise ValueError(f"poll-structure OOF candidates are incomplete: {missing or 'no folds'}")
    source_sha = hashlib.sha256(nested_path.read_bytes()).hexdigest()
    report = crossfit_structure_selection(
        scores,
        source_nested_sha256=source_sha,
        source_frozen_draws_sha256=str(nested.get("frozen_draws_sha256") or ""),
        model_version=str(nested["model_version"]),
    )
    out_path = out_path or (ARTIFACTS_DIR / "poll_structure_crossfit_latest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["path"] = str(out_path)
    return report
