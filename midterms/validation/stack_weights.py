"""Distributional OOF stacking with independently reproduced frozen draws.

The fold CRPS matrix is diagnostic and supplies eligibility filters. It is not
the production optimization objective. G8-disabled and structural ablations
are excluded from the predictive-distribution simplex.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR, ROOT
from midterms.model.empirical_mixture import fit_predictive_mixture
from midterms.model.ensemble import weights_from_oof_scores
from midterms.validation.artifact_lineage import (
    FROZEN_INDEX_SEMANTIC_VERSION,
    frozen_index_semantic_sha256,
    json_text_sha256_variants,
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
FORMAL_REQUIRED_CANDIDATES = frozenset({
    "pymc", "pymc_dynamic", "state_space", "ridge_fundamentals",
})


def _matrix_fingerprint(crps_by_fold: dict[str, dict[str, float]]) -> str:
    blob = json.dumps(crps_by_fold, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _draws_fingerprint(draws: dict[str, Any]) -> str:
    blob = json.dumps(draws, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(blob).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


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
    oof_draws: dict[str, dict[str, list[float]]] | None = None,
    oof_truths: dict[str, float] | None = None,
    seed: int = 21,
    max_draws: int = 128,
) -> dict[str, Any]:
    """Fit production weights from complete frozen predictive distributions only."""
    disabled = disabled_from_g8(g8_recommendations)
    ban = set(EXCLUDE_FROM_STACK) | disabled | set(extra_exclude or ())
    filtered = filter_crps_matrix(crps_by_fold, exclude=ban)
    if not filtered:
        raise ValueError("empty OOF CRPS matrix after G8 / structural filters")

    if not oof_draws or not oof_truths:
        raise ValueError("production stacking requires frozen OOF draws and truths")
    eligible = {model for fold in filtered.values() for model in fold}
    required_cases = set(oof_truths)
    if not required_cases:
        raise ValueError("OOF truth set is empty")
    excluded_missing: dict[str, list[str]] = {}
    complete_draws: dict[str, dict[str, list[float]]] = {}
    for model in sorted(eligible):
        cases = oof_draws.get(model) or {}
        missing = sorted(required_cases - set(cases))
        if missing:
            excluded_missing[model] = missing
        else:
            complete_draws[model] = cases
    if not complete_draws:
        raise ValueError("no candidate model has complete frozen OOF draws")
    mixture = fit_predictive_mixture(
        complete_draws, oof_truths, seed=seed, max_draws=max_draws,
    )
    production = mixture["weights"]

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
        "stacking_mode": "empirical_predictive_mixture_crps_v1",
        "excluded_structural": sorted(EXCLUDE_FROM_STACK),
        "excluded_g8_disable": sorted(disabled),
        "excluded_extra": sorted(extra_exclude or ()),
        "excluded_missing_predictions": excluded_missing,
        "filtered_crps_by_fold": filtered,
        "diagnostic_mean_crps_by_candidate": mean_crps,
        "stack_weights": production,
        "stack_weights_production": production,
        "diagnostic_score_softmax_loo": loo,
        "matrix_sha256": _matrix_fingerprint(filtered),
        "prediction_sha256": mixture["prediction_sha256"],
        "mixture_diagnostics": mixture,
        "optimizer_seed": seed,
        "optimizer_max_draws": max_draws,
        "no_weight_remapping": True,
        "predictive_stack_fn": "midterms.model.empirical_mixture.fit_predictive_mixture",
        "note": "Production weights use frozen empirical draws; score softmax is diagnostic only.",
    }


def reproduce_weights(payload: dict[str, Any]) -> dict[str, float]:
    """Refit from the exact frozen predictions referenced by the stack artifact."""
    if payload.get("stacking_mode") != "empirical_predictive_mixture_crps_v1":
        raise ValueError("stale stack artifact: empirical predictive draws required")
    nested_path = Path(str(payload.get("source_nested_loo") or ""))
    if not nested_path.is_absolute():
        nested_path = ROOT / nested_path
    if not nested_path.is_file():
        raise FileNotFoundError(f"frozen prediction artifact missing: {nested_path}")
    nested_sha = payload.get("source_nested_sha256")
    if nested_sha:
        if hashlib.sha256(nested_path.read_bytes()).hexdigest() != nested_sha:
            raise ValueError("nested OOF artifact fingerprint changed")
    elif payload.get("source_stack_training_protocol") == "formal_60_30_v1":
        raise ValueError("formal stack lacks nested OOF artifact fingerprint")
    nested = json.loads(nested_path.read_text(encoding="utf-8"))
    if payload.get("source_stack_training_protocol") == "formal_60_30_v1":
        frozen_index = nested_path.with_name(f"{nested_path.stem}_frozen.json")
        if not frozen_index.is_file():
            raise ValueError("frozen prediction index lineage changed")
        expected_semantic = payload.get("source_frozen_index_semantic_sha256")
        if (not expected_semantic
                or payload.get("source_frozen_index_hash_mode")
                != FROZEN_INDEX_SEMANTIC_VERSION):
            raise ValueError("formal stack lacks semantic frozen prediction index lineage")
        actual_semantic = frozen_index_semantic_sha256(frozen_index)
        nested_semantic = nested.get("frozen_index_semantic_sha256")
        if (actual_semantic != expected_semantic
                or (nested_semantic is not None and nested_semantic != expected_semantic)):
            raise ValueError("frozen prediction index lineage changed")
        # Old nested artifacts recorded the platform-specific text digest. Keep
        # it verifiable during migration, allowing only LF/CRLF conversion.
        legacy_nested_sha = nested.get("frozen_index_sha256")
        if (nested_semantic is None and legacy_nested_sha not in
                json_text_sha256_variants(frozen_index)):
            raise ValueError("legacy frozen prediction index lineage changed")
    if (nested.get("prior_snapshot_sha256_by_fold_lead")
            != payload.get("source_prior_snapshot_sha256_by_fold_lead")):
        raise ValueError("prior snapshot lineage changed")
    if (nested.get("presidential_source_sha256_by_fold_lead")
            != payload.get("source_presidential_source_sha256_by_fold_lead")):
        raise ValueError("presidential source lineage changed")
    if nested.get("frozen_draws_sha256") != _draws_fingerprint(nested.get("oof_draws") or {}):
        raise ValueError("frozen draw archive fingerprint changed")
    excluded = set(payload.get("excluded_g8_disable") or []) | set(EXCLUDE_FROM_STACK)
    excluded |= set(payload.get("excluded_extra") or [])
    excluded |= set(payload.get("excluded_missing_predictions") or {})
    allowed = {name for fold in (payload.get("filtered_crps_by_fold") or {}).values() for name in fold}
    draws = {
        name: cases for name, cases in (nested.get("oof_draws") or {}).items()
        if name in allowed and name not in excluded
    }
    fitted = fit_predictive_mixture(
        draws, nested.get("oof_truths") or {},
        seed=int(payload.get("optimizer_seed", 21)),
        max_draws=int(payload.get("optimizer_max_draws", 128)),
    )
    if fitted["prediction_sha256"] != payload.get("prediction_sha256"):
        raise ValueError("frozen prediction fingerprint changed")
    return fitted["weights"]


def verify_reproducible(payload: dict[str, Any], *, atol: float = 1e-9) -> dict[str, Any]:
    """Exit check: stored weights match refitting the frozen distributions."""
    stored = payload.get("stack_weights_production") or payload.get("stack_weights") or {}
    try:
        recomputed = reproduce_weights(payload)
        prediction_ok = True
        prediction_error = None
    except (ValueError, FileNotFoundError) as exc:
        recomputed = {}
        prediction_ok = False
        prediction_error = str(exc)
    keys = sorted(set(stored) | set(recomputed))
    max_abs = 0.0
    for k in keys:
        max_abs = max(max_abs, abs(float(stored.get(k, 0.0)) - float(recomputed.get(k, 0.0))))
    fp_ok = payload.get("matrix_sha256") == _matrix_fingerprint(
        payload.get("filtered_crps_by_fold") or {}
    )
    return {
        "ok": max_abs <= atol and fp_ok and prediction_ok,
        "max_abs_weight_delta": max_abs,
        "fingerprint_ok": fp_ok,
        "prediction_fingerprint_ok": prediction_ok,
        "prediction_error": prediction_error,
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
    if nested.get("frozen_draws_sha256") != _draws_fingerprint(nested.get("oof_draws") or {}):
        raise ValueError("nested OOF frozen draw fingerprint missing or stale")
    if nested.get("stack_training_protocol") == "formal_60_30_v1":
        if nested.get("failures"):
            raise ValueError("formal production stack requires zero nested component failures")
        if nested.get("oof_crps_method") != "exact_empirical_predictive_draws":
            raise ValueError("formal production stack requires empirical OOF CRPS screening")
        frozen_index = nested_path.with_name(f"{nested_path.stem}_frozen.json")
        if not frozen_index.is_file():
            raise ValueError("formal production stack frozen index is missing or stale")
        actual_index_semantic = frozen_index_semantic_sha256(frozen_index)
        nested_index_semantic = nested.get("frozen_index_semantic_sha256")
        if nested_index_semantic is not None:
            if nested_index_semantic != actual_index_semantic:
                raise ValueError("formal production stack frozen index is missing or stale")
        elif (not nested.get("frozen_index_sha256")
              or nested["frozen_index_sha256"] not in json_text_sha256_variants(frozen_index)):
            raise ValueError("formal production stack frozen index is missing or stale")
        if nested.get("years") != [2018, 2020, 2022, 2024] or nested.get("lead_days") != [60, 30]:
            raise ValueError("formal production stack requires the declared four-cycle 60/30 grid")
        truths = set(nested.get("oof_truths") or {})
        if not truths:
            raise ValueError("formal production stack has no frozen OOF truth cases")
        for name in sorted(FORMAL_REQUIRED_CANDIDATES):
            cases = set((nested.get("oof_draws") or {}).get(name) or {})
            if cases != truths:
                raise ValueError(
                    f"formal production stack candidate {name} has incomplete frozen OOF draws "
                    f"({len(cases)}/{len(truths)} cases)"
                )
        for year in nested["years"]:
            for lead in nested["lead_days"]:
                if not (nested.get("prior_snapshot_sha256_by_fold_lead") or {}).get(str(year), {}).get(str(lead)):
                    raise ValueError("formal production stack lacks a prior snapshot for a fold/lead")
                if not (nested.get("presidential_source_sha256_by_fold_lead") or {}).get(str(year), {}).get(str(lead)):
                    raise ValueError("formal production stack lacks presidential source lineage")
    crps = nested.get("crps_by_fold") or {}
    g8 = nested.get("g8_recommendations") or {}
    fitted = fit_stack_weights_from_oof(
        crps,
        g8_recommendations=g8,
        temperature=temperature,
        oof_draws=nested.get("oof_draws"),
        oof_truths=nested.get("oof_truths"),
    )
    fitted["source_nested_loo"] = _portable_path(nested_path)
    fitted["source_nested_sha256"] = hashlib.sha256(nested_path.read_bytes()).hexdigest()
    fitted["source_spine_label"] = nested.get("spine_label")
    fitted["source_hierarchical_method"] = nested.get("hierarchical_method")
    fitted["source_years"] = nested.get("years")
    fitted["source_prior_snapshot_sha256_by_fold_lead"] = nested.get("prior_snapshot_sha256_by_fold_lead")
    fitted["source_presidential_source_sha256_by_fold_lead"] = nested.get("presidential_source_sha256_by_fold_lead")
    fitted["source_frozen_index_sha256"] = nested.get("frozen_index_sha256")
    if nested.get("stack_training_protocol") == "formal_60_30_v1":
        fitted["source_frozen_index_hash_mode"] = FROZEN_INDEX_SEMANTIC_VERSION
        fitted["source_frozen_index_semantic_sha256"] = frozen_index_semantic_sha256(
            nested_path.with_name(f"{nested_path.stem}_frozen.json")
        )
    fitted["source_model_version"] = nested.get("model_version")
    fitted["source_stack_training_protocol"] = nested.get("stack_training_protocol")
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
    if payload.get("stacking_mode") != "empirical_predictive_mixture_crps_v1":
        raise ValueError("stale stack artifact: rebuild from frozen predictive draws")
    provenance: dict[str, Any] = {
        "source": "stack_weights_oof.json",
        "path": str(path),
        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "matrix_sha256": payload.get("matrix_sha256"),
        "no_weight_remapping": payload.get("no_weight_remapping", True),
        "excluded_g8_disable": payload.get("excluded_g8_disable"),
        "source_spine_label": payload.get("source_spine_label"),
        "source_model_version": payload.get("source_model_version"),
        "source_stack_training_protocol": payload.get("source_stack_training_protocol"),
        "source_nested_sha256": payload.get("source_nested_sha256"),
        "source_frozen_index_semantic_sha256": payload.get(
            "source_frozen_index_semantic_sha256"
        ),
        "source_frozen_index_hash_mode": payload.get("source_frozen_index_hash_mode"),
        "source_prior_snapshot_sha256_by_fold_lead": payload.get("source_prior_snapshot_sha256_by_fold_lead"),
        "source_presidential_source_sha256_by_fold_lead": payload.get("source_presidential_source_sha256_by_fold_lead"),
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
