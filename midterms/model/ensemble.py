"""Out-of-fold predictive stacking for structurally different challengers."""

from __future__ import annotations

from typing import Any

import numpy as np


def softmax_neg_scores(scores: dict[str, float], temperature: float = 1.0) -> dict[str, float]:
    """
    Convert mean CRPS (lower better) into nonnegative mixture weights.
    Models with missing scores get near-zero weight.
    """
    keys = list(scores.keys())
    if not keys:
        return {}
    vals = np.array([float(scores[k]) if np.isfinite(scores[k]) else 1e6 for k in keys], dtype=float)
    # Lower CRPS → higher weight
    logits = -vals / max(temperature, 1e-6)
    logits = logits - logits.max()
    ex = np.exp(logits)
    w = ex / ex.sum()
    return {k: float(wi) for k, wi in zip(keys, w)}


def stack_margin_draws(
    component_draws: dict[str, np.ndarray],
    weights: dict[str, float],
    *,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Mixture of predictive distributions via discrete component selection per draw.

    Each component array is shape (n_draws, n_races). Output uses the minimum
    shared draw count across components.
    """
    rng = rng or np.random.default_rng(0)
    names = [n for n in component_draws if n in weights and weights[n] > 0]
    if not names:
        raise ValueError("no stackable components")
    n_draws = min(component_draws[n].shape[0] for n in names)
    n_races = component_draws[names[0]].shape[1]
    w = np.array([weights[n] for n in names], dtype=float)
    w = w / w.sum()
    picks = rng.choice(len(names), size=n_draws, p=w)
    out = np.zeros((n_draws, n_races))
    # Prefer components that are finite on every race; fall back cell-wise.
    arrays = [component_draws[n] for n in names]
    for i, pick in enumerate(picks):
        row = arrays[int(pick)][i % arrays[int(pick)].shape[0]].astype(float, copy=True)
        if not np.isfinite(row).all():
            for alt in range(len(names)):
                cand = arrays[alt][i % arrays[alt].shape[0]]
                miss = ~np.isfinite(row)
                row[miss] = cand[miss]
            # Any remaining holes → 0 (neutral margin) rather than poisoning means.
            row = np.where(np.isfinite(row), row, 0.0)
        out[i] = row
    return out


def default_weights_from_replay(replay_report: dict[str, Any]) -> dict[str, float]:
    """
    Build stack weights from a cycle-replay aggregate block.

    Expects keys like aggregate.fast_hierarchical_t.crps, aggregate.shrinkage_polls.crps.
    """
    agg = replay_report.get("aggregate") or {}
    scores = {}
    for name, block in agg.items():
        if isinstance(block, dict) and "crps" in block:
            scores[name] = float(block["crps"])
    if not scores:
        return {"fast_hierarchical_t": 1.0}
    return softmax_neg_scores(scores, temperature=0.75)
