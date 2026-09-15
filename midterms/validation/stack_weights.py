"""Genuine OOF predictive stacking weights (audit P2.2 / Finding 6).

Weights reproduce from the nested-component LOO CRPS matrix. No silent
remapping of ``fast_hierarchical_t`` onto ``pymc``. G8-disabled and structural
ablation variants are excluded from the production simplex.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR
from midterms.model.ensemble import (
    predictive_stack_weights,
    softmax_neg_scores,
    weights_from_oof_scores,
)


# Structural ablation labels are diagnostics, not stack members.
EXCLUDE_FROM_STACK = frozenset(
    {
        "hier_no_similarity",
        "hier_no_terminal_race",
    }
)

STACK_WEIGHTS_PATH = ARTIFACTS_DIR / "stack_weights_oof.json"
NESTED_LOO_PATH = ARTIFACTS_DIR / "nested_component_loo.json"


def _matrix_fingerprint(crps_by_fold: dict[str, dict[str, float]]) -> str:
    blob = json.dumps(crps_by_fold, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def disabled_from_g8(g8: dict[str, Any] | None) -> set[str]:
    disabled: set[str] = set()
    for name, block in (g8 or {}).items():
        if not isinstance(block, dict):
            continue
        if block.get("recommend") == "disable":
            disabled.add(str(name))
    return disabled


def filter_crps_matrix(
    crps_by_fold: dict[str, dict[str, float]],
    *,
    exclude: set[str] | frozenset[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Drop excluded / disabled components from every fold."""
    ban = set(EXCLUDE_FROM_STACK) | set(exclude or ())
    out: dict[str, dict[str, float]] = {}
    for fold, scores in crps_by_fold.items():
        kept = {
            k: float(v)
            for k, v in scores.items()
            if k not in ban and v == v and np.isfinite(float(v))
        }
        if kept:
            out[str(fold)] = kept
    return out


def fit_stack_weights_from_oof(
    crps_by_fold: dict[str, dict[str, float]],
    *,
    g8_recommendations: dict[str, Any] | None = None,
    temperature: float = 0.75,
    extra_exclude: set[str] | None = None,
    oof_means: dict[str, dict[str, float]] | None = None,
    oof_truths: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Fit nonnegative simplex weights from OOF predictions.

    Prefers race-level predictive mixture (R-08) when ``oof_means`` / ``oof_truths``
    are supplied; otherwise falls back to softmax of mean CRPS.
    """
    disabled = disabled_from_g8(g8_recommendations)
    ban = set(EXCLUDE_FROM_STACK) | disabled | set(extra_exclude or ())
    filtered = filter_crps_matrix(crps_by_fold, exclude=ban)
    if not filtered:
        raise ValueError("empty OOF CRPS matrix after G8 / structural filters")

    stacking_mode = "softmax_mean_crps"
    if oof_means and oof_truths:
        means_kept = {k: v for k, v in oof_means.items() if k not in ban and v}
        if len(means_kept) >= 2:
            production = predictive_stack_weights(means_kept, oof_truths)
            stacking_mode = "predictive_mixture_crps"
        else:
            production = weights_from_oof_scores(filtered, temperature=temperature)
    else:
        production = weights_from_oof_scores(filtered, temperature=temperature)

    # Drop near-zeros for a clean simplex
    production = {k: float(v) for k, v in production.items() if float(v) >= 1e-6}
    total = sum(production.values())
    production = {k: v / total for k, v in production.items()}

    loo: dict[str, dict[str, float]] = {}
    for fold in filtered:
        w = weights_from_oof_scores(filtered, temperature=temperature, exclude_fold=fold)
        w = {k: float(v) for k, v in w.items() if float(v) >= 1e-6}
        t = sum(w.values())
        loo[str(fold)] = {k: v / t for k, v in w.items()} if t > 0 else {}

    mean_crps = {
        k: float(np.mean([fold[k] for fold in filtered.values() if k in fold]))
        for k in {n for fold in filtered.values() for n in fold}
    }

    return {
        "audit_item": "P2.2",
        "temperature": temperature,
        "stacking_mode": stacking_mode,
        "excluded_structural": sorted(EXCLUDE_FROM_STACK),
        "excluded_g8_disable": sorted(disabled),
        "excluded_extra": sorted(extra_exclude or ()),
        "filtered_crps_by_fold": filtered,
        "mean_crps_eligible": mean_crps,
        "stack_weights": production,
        "stack_weights_production": production,
        "stack_weights_loo": loo,
        "matrix_sha256": _matrix_fingerprint(filtered),
        "no_weight_remapping": True,
        "predictive_stack_fn": "midterms.model.ensemble.predictive_stack_weights",
        "note": (
            "Weights from race-level predictive mixture when oof_means present; "
            "else OOF CRPS softmax. Component identity preserved."
        ),
    }


def reproduce_weights(payload: dict[str, Any]) -> dict[str, float]:
    """Recompute production weights from the artifact's filtered matrix / OOF means."""
    temperature = float(payload.get("temperature") or 0.75)
    if payload.get("stacking_mode") == "predictive_mixture_crps":
        # Fall back to stored filtered CRPS softmax for exact reproduction when
        # oof_means are not re-embedded in the stack artifact.
        nested_path = Path(str(payload.get("source_nested_loo") or NESTED_LOO_PATH))
        if nested_path.exists():
            nested = json.loads(nested_path.read_text(encoding="utf-8"))
            means = nested.get("oof_means") or {}
            truths = nested.get("oof_truths") or {}
            ban = set(EXCLUDE_FROM_STACK) | set(payload.get("excluded_g8_disable") or [])
            means = {k: v for k, v in means.items() if k not in ban}
            if means and truths:
                w = predictive_stack_weights(means, truths)
                w = {k: float(v) for k, v in w.items() if float(v) >= 1e-6}
                total = sum(w.values())
                return {k: v / total for k, v in w.items()} if total > 0 else {}
    filtered = payload.get("filtered_crps_by_fold") or {}
    w = weights_from_oof_scores(filtered, temperature=temperature)
    w = {k: float(v) for k, v in w.items() if float(v) >= 1e-6}
    total = sum(w.values())
    return {k: v / total for k, v in w.items()} if total > 0 else {}


def verify_reproducible(payload: dict[str, Any], *, atol: float = 1e-9) -> dict[str, Any]:
    """Exit check: stored weights match recomputation from the OOF matrix."""
    stored = payload.get("stack_weights_production") or payload.get("stack_weights") or {}
    recomputed = reproduce_weights(payload)
    keys = sorted(set(stored) | set(recomputed))
    max_abs = 0.0
    for k in keys:
        max_abs = max(max_abs, abs(float(stored.get(k, 0.0)) - float(recomputed.get(k, 0.0))))
    fp_ok = payload.get("matrix_sha256") == _matrix_fingerprint(
        payload.get("filtered_crps_by_fold") or {}
    )
    return {
        "ok": max_abs <= atol and fp_ok,
        "max_abs_weight_delta": max_abs,
        "fingerprint_ok": fp_ok,
        "recomputed": recomputed,
        "stored": {k: float(v) for k, v in stored.items()},
    }


def fit_stack_weights_from_nested_loo(
    *,
    nested_path: Path | None = None,
    out_path: Path | None = None,
    temperature: float = 0.75,
) -> dict[str, Any]:
    """Load P2.1 artifact, fit P2.2 weights, write ``stack_weights_oof.json``."""
    nested_path = nested_path or NESTED_LOO_PATH
    if not nested_path.exists():
        raise FileNotFoundError(
            f"missing {nested_path}; run nested-component-loo (P2.1) first"
        )
    nested = json.loads(nested_path.read_text())
    crps = nested.get("crps_by_fold") or {}
    g8 = nested.get("g8_recommendations") or {}
    fitted = fit_stack_weights_from_oof(
        crps,
        g8_recommendations=g8,
        temperature=temperature,
        oof_means=nested.get("oof_means"),
        oof_truths=nested.get("oof_truths"),
    )
    fitted["source_nested_loo"] = str(nested_path)
    fitted["source_spine_label"] = nested.get("spine_label")
    fitted["source_hierarchical_method"] = nested.get("hierarchical_method")
    fitted["source_years"] = nested.get("years")
    ver = verify_reproducible(fitted)
    fitted["reproduction"] = ver
    if not ver["ok"]:
        raise RuntimeError(f"stack weights failed reproduction check: {ver}")

    out_path = out_path or STACK_WEIGHTS_PATH
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    fitted["path"] = str(out_path)
    out_path.write_text(json.dumps(fitted, indent=2, default=str))
    return fitted


def load_oof_stack_weights(
    *,
    path: Path | None = None,
    require_reproducible: bool = True,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Load production OOF weights; optionally verify reproduction."""
    path = path or STACK_WEIGHTS_PATH
    if not path.exists():
        return {}, {"source": "missing", "path": str(path)}
    payload = json.loads(path.read_text())
    provenance: dict[str, Any] = {
        "source": "stack_weights_oof.json",
        "path": str(path),
        "matrix_sha256": payload.get("matrix_sha256"),
        "no_weight_remapping": payload.get("no_weight_remapping", True),
        "excluded_g8_disable": payload.get("excluded_g8_disable"),
        "source_spine_label": payload.get("source_spine_label"),
        "note": payload.get("note"),
    }
    if require_reproducible:
        ver = verify_reproducible(payload)
        provenance["reproduction"] = ver
        if not ver["ok"]:
            raise RuntimeError(f"stack_weights_oof failed reproduction: {ver}")
    weights = {
        k: float(v)
        for k, v in (payload.get("stack_weights_production") or payload.get("stack_weights") or {}).items()
        if float(v) > 0
    }
    provenance["weights"] = weights
    return weights, provenance
