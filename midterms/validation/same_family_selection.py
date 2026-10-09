"""Fold-safe same-family configuration selection after OOF scoring.

After the selection-phase nested LOO scores all models (including same-family
configuration challengers), this module selects:

  1. One state-space family member (reference or a single-parameter challenger).
  2. One national-environment specification (all terms enabled, or with one term
     disabled where removing it demonstrably improves held-out CRPS).

Selection uses the same majority-vote rule as G8: a challenger "wins" if it
achieves lower CRPS than the reference on a strict majority of outer folds
where both are scored.  Tie (0 winners) retains the reference.

These selections feed into the candidate model spec and are part of the
ordinary-statistical-spec identity (i.e. changing them changes the SHA256).

No OOF fitting is performed here; this module is purely post-hoc analysis.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from midterms.model.challengers import STATE_SPACE_CHALLENGERS, STATE_SPACE_REFERENCE_CONFIG
from midterms.model.national_environment_ablations import NATIONAL_ENVIRONMENT_ABLATIONS

# Name of the reference (production) model in each family.
STATE_SPACE_REFERENCE_NAME = "state_space"
NATIONAL_ENVIRONMENT_REFERENCE_NAME = "ridge_fundamentals"

# Mapping from national-environment ablation name to the config key it disables.
_NAT_ENV_ABLATION_TO_CONFIG_KEY: dict[str, str] = {
    "no_generic_ballot": "generic_ballot_enabled",
    "no_approval": "approval_enabled",
    "no_midterm_outparty": "midterm_outparty_enabled",
    "no_income_economic": "income_enabled",
}

# Reference (all-enabled) national-environment configuration.
NATIONAL_ENVIRONMENT_REFERENCE_CONFIG: dict[str, bool] = {
    "generic_ballot_enabled": True,
    "approval_enabled": True,
    "midterm_outparty_enabled": True,
    "income_enabled": True,
}


def _compare_challenger_to_reference(
    challenger: str,
    reference: str,
    crps_by_fold: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Majority-vote comparison: does *challenger* beat *reference* CRPS?

    Returns a dict with keys:
      beats_reference (bool), n_beats_reference (int), n_folds (int),
      deltas_reference_minus_challenger (dict[fold, float]).
    """
    folds = list(crps_by_fold.keys())
    n_beats = 0
    n_folds = 0
    deltas: dict[str, float] = {}
    for fold in folds:
        table = crps_by_fold[fold]
        if challenger not in table or reference not in table:
            continue
        c_val = table[challenger]
        r_val = table[reference]
        if not np.isfinite(c_val) or not np.isfinite(r_val):
            continue
        n_folds += 1
        delta = float(r_val) - float(c_val)  # positive => challenger better
        deltas[fold] = delta
        if float(c_val) < float(r_val):
            n_beats += 1
    beats = n_folds > 0 and n_beats >= max(1, (n_folds + 1) // 2)
    return {
        "beats_reference": beats,
        "n_beats_reference": n_beats,
        "n_folds": n_folds,
        "deltas_reference_minus_challenger": deltas,
    }


def _mean_crps(name: str, crps_by_fold: dict[str, dict[str, float]]) -> float:
    vals = [
        crps_by_fold[fold][name]
        for fold in crps_by_fold
        if name in crps_by_fold[fold] and np.isfinite(crps_by_fold[fold][name])
    ]
    return float(np.mean(vals)) if vals else float("inf")


def select_same_family_configs(
    nested_loo: dict[str, Any],
) -> dict[str, Any]:
    """Select one state-space config and one national-environment config.

    Uses the ``crps_by_fold`` from a nested LOO artifact.  If challengers were
    not scored (absent from the fold tables), the reference config is retained
    for that family — the function is safe to call on artifacts that predate
    same-family challenger scoring.

    Returns a dict with:
      selected_state_space_name         (str)
      selected_state_space_config       (dict matching STATE_SPACE_REFERENCE_CONFIG shape)
      selected_national_environment_name (str)
      selected_national_environment_config (dict matching NATIONAL_ENVIRONMENT_REFERENCE_CONFIG shape)
      same_family_selection_audit       (dict — full per-challenger evidence)
    """
    crps_by_fold: dict[str, dict[str, float]] = dict(nested_loo.get("crps_by_fold") or {})

    # ------------------------------------------------------------------
    # 1. State-space family
    # ------------------------------------------------------------------
    ss_reference = STATE_SPACE_REFERENCE_NAME
    ss_challenger_names = list(STATE_SPACE_CHALLENGERS.keys())

    ss_results: dict[str, dict[str, Any]] = {}
    for challenger in ss_challenger_names:
        ss_results[challenger] = _compare_challenger_to_reference(
            challenger, ss_reference, crps_by_fold
        )

    ss_winners = [name for name, r in ss_results.items() if r["beats_reference"]]
    if ss_winners:
        # Among winners pick the one with lowest mean CRPS
        selected_ss_name = min(ss_winners, key=lambda n: _mean_crps(n, crps_by_fold))
        selected_ss_config = {
            k: STATE_SPACE_CHALLENGERS[selected_ss_name][k]
            for k in STATE_SPACE_REFERENCE_CONFIG
        }
    else:
        selected_ss_name = ss_reference
        selected_ss_config = dict(STATE_SPACE_REFERENCE_CONFIG)

    # ------------------------------------------------------------------
    # 2. National-environment family
    # ------------------------------------------------------------------
    nat_reference = NATIONAL_ENVIRONMENT_REFERENCE_NAME
    nat_challenger_names = list(NATIONAL_ENVIRONMENT_ABLATIONS.keys())

    nat_results: dict[str, dict[str, Any]] = {}
    for challenger in nat_challenger_names:
        nat_results[challenger] = _compare_challenger_to_reference(
            challenger, nat_reference, crps_by_fold
        )

    # Build config: start all-enabled, then disable any term whose ablation wins.
    selected_nat_config: dict[str, bool] = dict(NATIONAL_ENVIRONMENT_REFERENCE_CONFIG)
    nat_winners = [name for name, r in nat_results.items() if r["beats_reference"]]
    for winner in nat_winners:
        config_key = _NAT_ENV_ABLATION_TO_CONFIG_KEY.get(winner)
        if config_key is not None:
            selected_nat_config[config_key] = False

    # Logical name: reference when nothing changes, else last winner (unusual).
    if not nat_winners:
        selected_nat_name = nat_reference
    elif len(nat_winners) == 1:
        selected_nat_name = nat_winners[0]
    else:
        selected_nat_name = "_".join(sorted(nat_winners))

    # ------------------------------------------------------------------
    # Audit bundle
    # ------------------------------------------------------------------
    audit: dict[str, Any] = {
        "state_space": {
            "reference": ss_reference,
            "challengers_scored": ss_results,
            "winners": ss_winners,
            "selected_name": selected_ss_name,
            "selected_config": selected_ss_config,
        },
        "national_environment": {
            "reference": nat_reference,
            "challengers_scored": nat_results,
            "winners": nat_winners,
            "selected_name": selected_nat_name,
            "selected_config": selected_nat_config,
        },
        "note": (
            "Majority-vote rule: challenger beats reference when it achieves "
            "lower CRPS on ≥ ceil((n_folds+1)/2) outer folds. "
            "Reference is retained when no challenger wins."
        ),
    }

    return {
        "selected_state_space_name": selected_ss_name,
        "selected_state_space_config": selected_ss_config,
        "selected_national_environment_name": selected_nat_name,
        "selected_national_environment_config": selected_nat_config,
        "same_family_selection_audit": audit,
    }
