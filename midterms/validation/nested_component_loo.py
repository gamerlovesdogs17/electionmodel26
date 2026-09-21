"""Nested leave-one-cycle-out evaluation for every stackable component (audit P2.1).

Exit condition: predictions are frozen before the held-out truth is read.
Gate G8: optional components must improve a predeclared metric across outer
folds or be recommended for disable.

Honest labeling: no remapping of ``fast_hierarchical_t`` mass onto ``pymc``.
Failures are recorded explicitly; unscored components receive no weight credit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from midterms.baselines.models import BASELINES, RaceForecast
from midterms.baselines.score import score_forecasts
from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.evidence.warehouse import Warehouse
from midterms.model.challengers import (
    fit_poll_only_state_space,
    fit_ridge_fundamentals,
)
from midterms.model.ensemble import weights_from_oof_scores
from midterms.model.pymc_model import (
    FitResult,
    draws_from_baseline_forecasts,
    fit_fast_approximation,
    fit_pymc,
    fit_pymc_dynamic,
)
from midterms.model.state_space import fit_state_space
from midterms.model.terminal import active_scales
from midterms.validation.artifact_lineage import (
    frozen_index_semantic_sha256,
    json_text_sha256_variants,
)

# Predeclared metric for G8 keep/drop (lower better).
G8_METRIC = "crps"
OOF_PYMC_DRAWS_PER_CHAIN = 800
OOF_PYMC_TUNE_PER_CHAIN = 800
OOF_PYMC_CHAINS = 2
# Optional stack members subject to disable recommendation.
OPTIONAL_COMPONENTS = (
    "state_space",
    "poll_only_state_space",
    "ridge_fundamentals",
    "pymc_dynamic",
    "last_election_swing",
    "equal_weight_polls",
    "shrinkage_polls",
)
# Structural ablations scored as separate named variants of the hierarchical spine.
STRUCTURAL_VARIANTS = (
    "hier_no_similarity",
    "hier_no_terminal_race",
)


@dataclass
class FrozenPrediction:
    """Immutable predictive snapshot — built with no access to certified results."""

    component: str
    election_id: str
    holdout_year: int
    lead_days: int
    as_of: str
    race_ids: list[str]
    means: list[float]
    sds: list[float]
    method: str
    n_draws: int
    seed: int
    status: str  # ok | failed
    error: str | None = None
    draws_by_race: dict[str, list[float]] | None = None
    fit_settings: dict[str, Any] | None = None
    prediction_sha256: str | None = None
    evidence_snapshot_id: str | None = None
    prior_snapshot_sha256: str | None = None
    presidential_source_sha256: str | None = None


def _draws_fingerprint(draws: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(draws, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _forecasts_from_frozen(fp: FrozenPrediction) -> list[RaceForecast]:
    from scipy.stats import norm

    out = []
    for rid, mu, sd in zip(fp.race_ids, fp.means, fp.sds):
        sd_f = float(max(sd, 0.5))
        out.append(
            RaceForecast(
                race_id=rid,
                state=rid.split("-")[-1] if "-" in rid else "",
                mean_margin=float(mu),
                sd=sd_f,
                p_dem=float(norm.sf(0, loc=mu, scale=sd_f)),
            )
        )
    return out


def _freeze_from_fit(
    fit: FitResult,
    *,
    component: str,
    election_id: str,
    holdout_year: int,
    lead_days: int,
    as_of: date,
    seed: int,
) -> FrozenPrediction:
    matrix = np.asarray(fit.draws_margin, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != len(fit.race_ids) or not np.isfinite(matrix).all():
        raise ValueError(f"invalid frozen predictive draws for {component}")
    by_race = {
        str(rid): [float(x) for x in matrix[:, i]]
        for i, rid in enumerate(fit.race_ids)
    }
    diagnostics = fit.diagnostics or {}
    if component in {"pymc", "pymc_dynamic", "state_space", "ridge_fundamentals"}:
        if str(fit.method) != component:
            raise ValueError(f"{component} fit returned a different model identity: {fit.method}")
    if component in {"pymc", "pymc_dynamic"}:
        convergence = diagnostics.get("convergence") or {}
        if (not convergence.get("available")
                or not np.isfinite(float(convergence.get("r_hat_max", float("nan"))))
                or float(convergence["r_hat_max"]) > 1.05
                or not np.isfinite(float(convergence.get("ess_bulk_min_frac", float("nan"))))
                or float(convergence["ess_bulk_min_frac"]) < 0.10):
            raise ValueError(
                f"{component} validation inference did not meet R-hat/ESS diagnostics: "
                f"{convergence}"
            )
    return FrozenPrediction(
        component=component,
        election_id=election_id,
        holdout_year=holdout_year,
        lead_days=lead_days,
        as_of=as_of.isoformat(),
        race_ids=list(fit.race_ids),
        means=[float(x) for x in np.asarray(fit.mean_margin, dtype=float)],
        sds=[float(max(x, 0.5)) for x in np.asarray(fit.sd_margin, dtype=float)],
        method=str(fit.method),
        n_draws=int(fit.draws_margin.shape[0]) if getattr(fit, "draws_margin", None) is not None else 0,
        seed=seed,
        status="ok",
        draws_by_race=by_race,
        fit_settings={k: diagnostics.get(k) for k in ("draws", "tune", "chains", "seed", "convergence")},
        prediction_sha256=_draws_fingerprint(by_race),
    )


def _freeze_from_forecasts(
    forecasts: list[RaceForecast],
    *,
    component: str,
    election_id: str,
    holdout_year: int,
    lead_days: int,
    as_of: date,
    seed: int,
    n_draws: int = 600,
) -> FrozenPrediction:
    matrix = draws_from_baseline_forecasts(forecasts, n_draws=n_draws, seed=seed)
    by_race = {str(f.race_id): [float(x) for x in matrix[:, i]] for i, f in enumerate(forecasts)}
    return FrozenPrediction(
        component=component,
        election_id=election_id,
        holdout_year=holdout_year,
        lead_days=lead_days,
        as_of=as_of.isoformat(),
        race_ids=[f.race_id for f in forecasts],
        means=[float(f.mean_margin) for f in forecasts],
        sds=[float(max(f.sd, 0.5)) for f in forecasts],
        method=component,
        n_draws=n_draws,
        seed=seed,
        status="ok",
        draws_by_race=by_race,
        fit_settings={"draws": n_draws, "seed": seed, "method": "baseline_predictive_draws"},
        prediction_sha256=_draws_fingerprint(by_race),
    )


def _failed_freeze(
    component: str,
    *,
    election_id: str,
    holdout_year: int,
    lead_days: int,
    as_of: date,
    seed: int,
    error: str,
) -> FrozenPrediction:
    return FrozenPrediction(
        component=component,
        election_id=election_id,
        holdout_year=holdout_year,
        lead_days=lead_days,
        as_of=as_of.isoformat(),
        race_ids=[],
        means=[],
        sds=[],
        method=component,
        n_draws=0,
        seed=seed,
        status="failed",
        error=error,
    )


def _generic_ballot(snap) -> float:
    if not len(snap.polls):
        return 0.0
    merged = snap.polls.merge(snap.races[["race_id", "prior_lean"]], on="race_id", how="left")
    return float((merged["two_party_margin"] - merged["prior_lean"]).mean())


def _fit_hierarchical(snap, *, method: str, n_draws: int, seed: int, gb: float, terminal_scales=None):
    if method == "pymc":
        return fit_pymc(
            snap,
            draws=max(n_draws // 2, OOF_PYMC_DRAWS_PER_CHAIN),
            tune=max(n_draws // 2, OOF_PYMC_TUNE_PER_CHAIN),
            chains=OOF_PYMC_CHAINS,
            seed=seed,
            generic_ballot=gb,
            terminal_scales=terminal_scales,
        )
    if method in {"pymc_dynamic", "pymc-dynamic"}:
        return fit_pymc_dynamic(
            snap,
            draws=max(n_draws // 2, OOF_PYMC_DRAWS_PER_CHAIN),
            tune=max(n_draws // 2, OOF_PYMC_TUNE_PER_CHAIN),
            chains=OOF_PYMC_CHAINS,
            seed=seed,
            generic_ballot=gb,
            terminal_scales=terminal_scales,
        )
    return fit_fast_approximation(
        snap,
        n_draws=n_draws,
        seed=seed,
        generic_ballot=gb,
        terminal_scales=terminal_scales,
    )


def freeze_component_predictions(
    snap,
    *,
    election_id: str,
    holdout_year: int,
    lead_days: int,
    hierarchical_method: str = "fast",
    n_draws: int = 600,
    seed: int = 21,
) -> dict[str, FrozenPrediction]:
    """
    Fit every component and freeze predictive distributions.

    Intentionally does not accept or read certified results.
    """
    as_of = snap.as_of
    gb = _generic_ballot(snap)
    frozen: dict[str, FrozenPrediction] = {}
    hier_name = (
        "pymc_dynamic"
        if hierarchical_method.startswith("pymc_dynamic")
        else ("pymc" if hierarchical_method.startswith("pymc") else "fast_hierarchical_t")
    )

    def _safe(name: str, fn: Callable[[], FrozenPrediction]) -> None:
        try:
            frozen[name] = fn()
        except Exception as exc:  # noqa: BLE001
            frozen[name] = _failed_freeze(
                name,
                election_id=election_id,
                holdout_year=holdout_year,
                lead_days=lead_days,
                as_of=as_of,
                seed=seed,
                error=str(exc),
            )
        frozen[name].evidence_snapshot_id = getattr(snap, "snapshot_id", None)
        frozen[name].prior_snapshot_sha256 = getattr(snap, "prior_snapshot_sha256", None)
        frozen[name].presidential_source_sha256 = getattr(snap, "presidential_source_sha256", None)

    _safe(
        hier_name,
        lambda: _freeze_from_fit(
            _fit_hierarchical(
                snap, method=hierarchical_method, n_draws=n_draws, seed=seed, gb=gb
            ),
            component=hier_name,
            election_id=election_id,
            holdout_year=holdout_year,
            lead_days=lead_days,
            as_of=as_of,
            seed=seed,
        ),
    )

    # Structural variants of hierarchical spine (G8 optional structure)
    scales_no_sim = {**active_scales(), "sim_scale": 0.0}
    scales_no_race = {**active_scales(), "terminal_race_sd": 0.0}
    _safe(
        "hier_no_similarity",
        lambda: _freeze_from_fit(
            _fit_hierarchical(
                snap,
                method="fast",
                n_draws=n_draws,
                seed=seed + 3,
                gb=gb,
                terminal_scales=scales_no_sim,
            ),
            component="hier_no_similarity",
            election_id=election_id,
            holdout_year=holdout_year,
            lead_days=lead_days,
            as_of=as_of,
            seed=seed + 3,
        ),
    )
    _safe(
        "hier_no_terminal_race",
        lambda: _freeze_from_fit(
            _fit_hierarchical(
                snap,
                method="fast",
                n_draws=n_draws,
                seed=seed + 5,
                gb=gb,
                terminal_scales=scales_no_race,
            ),
            component="hier_no_terminal_race",
            election_id=election_id,
            holdout_year=holdout_year,
            lead_days=lead_days,
            as_of=as_of,
            seed=seed + 5,
        ),
    )

    _safe(
        "state_space",
        lambda: _freeze_from_fit(
            fit_state_space(snap, n_draws=n_draws, seed=seed + 11, generic_ballot=gb),
            component="state_space",
            election_id=election_id,
            holdout_year=holdout_year,
            lead_days=lead_days,
            as_of=as_of,
            seed=seed + 11,
        ),
    )
    _safe(
        "poll_only_state_space",
        lambda: _freeze_from_fit(
            fit_poll_only_state_space(snap, n_draws=n_draws, seed=seed + 13),
            component="poll_only_state_space",
            election_id=election_id,
            holdout_year=holdout_year,
            lead_days=lead_days,
            as_of=as_of,
            seed=seed + 13,
        ),
    )
    _safe(
        "ridge_fundamentals",
        lambda: _freeze_from_fit(
            fit_ridge_fundamentals(
                snap, n_draws=n_draws, seed=seed + 17, generic_ballot=gb,
                require_historical_fit=True,
            ),
            component="ridge_fundamentals",
            election_id=election_id,
            holdout_year=holdout_year,
            lead_days=lead_days,
            as_of=as_of,
            seed=seed + 17,
        ),
    )
    # Unified dynamic hierarchical PyMC — always freeze as its own candidate
    # (even when the spine is static pymc), so stacking can assign mass honestly.
    if hier_name == "pymc":
        _safe(
            "pymc_dynamic",
            lambda: _freeze_from_fit(
                fit_pymc_dynamic(
                    snap,
                    draws=max(n_draws // 2, OOF_PYMC_DRAWS_PER_CHAIN),
                    tune=max(n_draws // 2, OOF_PYMC_TUNE_PER_CHAIN),
                    chains=OOF_PYMC_CHAINS,
                    seed=seed + 19,
                    generic_ballot=gb,
                ),
                component="pymc_dynamic",
                election_id=election_id,
                holdout_year=holdout_year,
                lead_days=lead_days,
                as_of=as_of,
                seed=seed + 19,
            ),
        )
    elif hier_name == "pymc_dynamic":
        _safe(
            "pymc",
            lambda: _freeze_from_fit(
                fit_pymc(
                    snap, draws=max(n_draws // 2, OOF_PYMC_DRAWS_PER_CHAIN),
                    tune=max(n_draws // 2, OOF_PYMC_TUNE_PER_CHAIN),
                    chains=OOF_PYMC_CHAINS, seed=seed + 23, generic_ballot=gb,
                ),
                component="pymc", election_id=election_id,
                holdout_year=holdout_year, lead_days=lead_days,
                as_of=as_of, seed=seed + 23,
            ),
        )

    for bname, bfn in BASELINES.items():
        _safe(
            bname,
            lambda bname=bname, bfn=bfn: _freeze_from_forecasts(
                bfn(snap),
                component=bname,
                election_id=election_id,
                holdout_year=holdout_year,
                lead_days=lead_days,
                as_of=as_of,
                seed=seed,
                n_draws=n_draws,
            ),
        )

    return frozen


def score_frozen_predictions(
    frozen: dict[str, FrozenPrediction],
    results,
) -> dict[str, Any]:
    """Score only after predictions are frozen — first point of contact with truth."""
    from midterms.baselines.score import discrete_crps
    from midterms.evidence.score_targets import truth_margin_map

    truth_by_id = truth_margin_map(results)
    scores: dict[str, Any] = {}
    for name, fp in frozen.items():
        if fp.status != "ok" or not fp.race_ids:
            scores[name] = {
                "status": "failed",
                "error": fp.error,
                "n": 0,
                G8_METRIC: float("nan"),
            }
            continue
        sc = score_forecasts(_forecasts_from_frozen(fp), results)
        empirical: list[float] = []
        for rid, draws in (fp.draws_by_race or {}).items():
            if rid in truth_by_id:
                empirical.append(discrete_crps(np.asarray(draws, dtype=float), truth_by_id[rid]))
        if not empirical or len(empirical) != sc.get("n"):
            scores[name] = {
                "status": "failed", "error": "frozen predictive draws missing for scored cases",
                "n": 0, G8_METRIC: float("nan"),
            }
            continue
        scores[name] = {
            "status": "ok", **sc,
            "crps_gaussian_diagnostic": sc["crps"],
            "crps": float(np.mean(empirical)),
            "crps_method": "exact_empirical_predictive_draws",
        }
    return scores


def _nested_reliability_block(
    *,
    spine: str,
    oof_means: dict[str, dict[str, float]],
    oof_sds: dict[str, dict[str, float]],
    oof_truths: dict[str, float],
) -> dict[str, Any]:
    """Unadjusted reliability diagnostic on frozen outer-fold predictions.

    An uncertainty scale selected with these same held-out outcomes would not
    be an independently validated calibration result. Keep such fitting in a
    separate inner training procedure if it is introduced later.
    """
    from scipy.stats import norm

    from midterms.validation.metrics import reliability_bins, reliability_overconfidence

    means_map = oof_means.get(spine) or {}
    sds_map = oof_sds.get(spine) or {}
    pairs: list[tuple[float, float, float]] = []
    for key, y in oof_truths.items():
        if key not in means_map:
            continue
        mu = float(means_map[key])
        sd = float(max(sds_map.get(key, 5.0), 0.5))
        pairs.append((mu, sd, 1.0 if float(y) >= 0 else 0.0))
    if len(pairs) < 10:
        return {"n": len(pairs), "ok": False, "spine": spine}

    def _eval(scale: float) -> tuple[list[dict[str, float]], dict[str, Any], float]:
        probs = np.array(
            [float(norm.sf(0, loc=mu, scale=max(sd * scale, 0.5))) for mu, sd, _ in pairs]
        )
        outcomes = np.array([o for _, _, o in pairs], dtype=float)
        rel = reliability_bins(probs, outcomes)
        gate = reliability_overconfidence(rel)
        brier = float(np.mean((probs - outcomes) ** 2))
        return rel, gate, brier

    rel, gate, brier = _eval(1.0)

    return {
        "spine": spine,
        "n": len(pairs),
        "reliability": rel,
        "reliability_gate": gate,
        "brier": brier,
        "raw_brier": brier,
        "raw_reliability_gate": gate,
        "sd_inflation": 1.0,
        "widened": False,
        "ok": bool(gate.get("calibration_claim_allowed")),
    }


def _g8_recommendations(
    crps_by_fold: dict[str, dict[str, float]],
    *,
    spine: str,
) -> dict[str, Any]:
    """
    Keep optional component if ablating it (comparing to spine-only) would worsen
    mean CRPS on a majority of outer folds where both scored.
    """
    recs: dict[str, Any] = {}
    folds = list(crps_by_fold.keys())
    for name in list(OPTIONAL_COMPONENTS) + list(STRUCTURAL_VARIANTS):
        keep_votes = 0
        n = 0
        deltas: dict[str, float] = {}
        for fold in folds:
            table = crps_by_fold[fold]
            if name not in table or spine not in table:
                continue
            if not np.isfinite(table[name]) or not np.isfinite(table[spine]):
                continue
            n += 1
            # Positive delta => component better than spine (lower CRPS)
            delta = float(table[spine] - table[name])
            deltas[fold] = delta
            if name.startswith("hier_no_"):
                # Structural ablation: full spine should beat ablated variant
                # keep structure if spine CRPS < ablation CRPS
                if table[spine] < table[name]:
                    keep_votes += 1
            else:
                # Optional stack member: keep if it beats spine on this fold
                if table[name] < table[spine]:
                    keep_votes += 1
        if name.startswith("hier_no_"):
            # Recommend keeping the *feature* (similarity / race terminal)
            feature = "similarity_terminal" if "similarity" in name else "terminal_race"
            recommend = "keep" if n and keep_votes >= max(1, (n + 1) // 2) else "drop_or_shrink"
            recs[feature] = {
                "ablation_component": name,
                "keep_votes": keep_votes,
                "n_folds": n,
                "recommend": recommend,
                "deltas_spine_minus_ablation": deltas,
            }
        else:
            recommend = "keep" if n and keep_votes >= max(1, (n + 1) // 2) else "disable"
            recs[name] = {
                "keep_votes": keep_votes,
                "n_folds": n,
                "recommend": recommend,
                "deltas_spine_minus_component": deltas,
            }
    return recs


def rescore_frozen_oof_draws(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute OOF CRPS from sealed predictive draws, without any refit.

    This is also useful when migrating an older freeze archive whose
    screening scores were computed from a Gaussian moment approximation.
    """
    from midterms.baselines.score import discrete_crps

    draws = report.get("oof_draws") or {}
    truths = report.get("oof_truths") or {}
    if report.get("frozen_draws_sha256") != _draws_fingerprint(draws):
        raise ValueError("frozen OOF draws changed before rescoring")
    crps_by_fold: dict[str, dict[str, float]] = {}
    for year in report.get("years") or []:
        year_key = str(year)
        scores_by_model: dict[str, list[float]] = {}
        for lead in report.get("lead_days") or []:
            lead_key = str(lead)
            prefix = f"{year_key}:{lead_key}:"
            lead_scores = report["by_fold"][year_key]["leads"][lead_key]
            for model, block in lead_scores.items():
                if block.get("status") != "ok":
                    continue
                cases = {
                    case: values for case, values in (draws.get(model) or {}).items()
                    if case.startswith(prefix) and case in truths
                }
                if len(cases) != int(block.get("n", 0)) or not cases:
                    raise ValueError(f"incomplete frozen draw cases for {model}/{year}/{lead}")
                score = float(np.mean([
                    discrete_crps(np.asarray(values, dtype=float), float(truths[case]))
                    for case, values in sorted(cases.items())
                ]))
                if block.get("crps_method") != "exact_empirical_predictive_draws":
                    block["crps_gaussian_diagnostic"] = block.get("crps")
                block["crps"] = score
                block["crps_method"] = "exact_empirical_predictive_draws"
                scores_by_model.setdefault(model, []).append(score)
        fold_scores = {model: float(np.mean(scores)) for model, scores in scores_by_model.items()}
        report["by_fold"][year_key]["mean_crps"] = fold_scores
        crps_by_fold[year_key] = fold_scores
    report["crps_by_fold"] = crps_by_fold
    report["mean_crps_by_component"] = {
        model: float(np.mean([fold[model] for fold in crps_by_fold.values() if model in fold]))
        for model in {name for fold in crps_by_fold.values() for name in fold}
    }
    spine = report.get("spine_label")
    report["mean_crps"] = report["mean_crps_by_component"].get(spine)
    report["diagnostic_score_softmax_weights"] = weights_from_oof_scores(crps_by_fold)
    report["g8_recommendations"] = _g8_recommendations(crps_by_fold, spine=spine)
    report["oof_crps_method"] = "exact_empirical_predictive_draws"
    return report


def repair_failed_oof_inference(
    *,
    year: int,
    lead_days: int,
    component: str,
    out_path: Path | None = None,
    draws_per_chain: int = 2000,
    tune_per_chain: int = 2000,
    chains: int = 4,
) -> dict[str, Any]:
    """Refit a failed PyMC freeze using only convergence-driven extra sampling.

    The compact freeze index, which contains no held-out truth, is the only
    persisted validation file read before fitting. The scored report is opened
    only after the replacement prediction has been frozen and checked.
    """
    if component not in {"pymc", "pymc_dynamic"}:
        raise ValueError("diagnostic inference repair supports PyMC candidates only")
    if draws_per_chain < 2000 or tune_per_chain < 2000 or chains < 4:
        raise ValueError("inference repair must meet the 2000/2000/4 recovery floor")
    out_path = out_path or (ARTIFACTS_DIR / "nested_component_loo.json")
    index_path = out_path.with_name(f"{out_path.stem}_frozen.json")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    matches = [entry for entry in index["entries"] if (
        entry["component"] == component and entry["holdout_year"] == year
        and entry["lead_days"] == lead_days and entry["status"] == "failed"
    )]
    if len(matches) != 1:
        raise ValueError("expected one failed, unscored frozen prediction in index")
    original = matches[0]
    election_id = f"senate-{year}"
    wh = Warehouse()
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        raise ValueError("historical race group missing")
    ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
    as_of = ed - timedelta(days=lead_days)
    if original["as_of"] != as_of.isoformat():
        raise ValueError("freeze index date differs from reconstructed as-of")
    snap = wh.build_as_of(as_of, election_id)
    fit_fn = fit_pymc if component == "pymc" else fit_pymc_dynamic
    replacement = _freeze_from_fit(
        fit_fn(
            snap, draws=draws_per_chain, tune=tune_per_chain, chains=chains,
            seed=int(original["seed"]), generic_ballot=_generic_ballot(snap),
        ),
        component=component, election_id=election_id, holdout_year=year,
        lead_days=lead_days, as_of=as_of, seed=int(original["seed"]),
    )
    replacement.evidence_snapshot_id = snap.snapshot_id
    replacement.prior_snapshot_sha256 = snap.prior_snapshot_sha256
    replacement.presidential_source_sha256 = snap.presidential_source_sha256

    # First truth/report contact is after replacement was frozen above.
    report = json.loads(out_path.read_text(encoding="utf-8"))
    original_index_sha = report.get("frozen_index_sha256")
    original_index_semantic = report.get("frozen_index_semantic_sha256")
    if (original_index_semantic is not None
            and original_index_semantic != frozen_index_semantic_sha256(index_path)):
        raise ValueError("frozen prediction index changed before inference repair")
    if (original_index_semantic is None and original_index_sha
            and original_index_sha not in json_text_sha256_variants(index_path)):
        raise ValueError("frozen prediction index changed before inference repair")
    if (report["prior_snapshot_sha256_by_fold_lead"][str(year)][str(lead_days)]
            != replacement.prior_snapshot_sha256
            or report["presidential_source_sha256_by_fold_lead"][str(year)][str(lead_days)]
            != replacement.presidential_source_sha256):
        raise ValueError("replacement prediction uses a different prior source snapshot")
    if not any(f.get("year") == year and f.get("lead") == lead_days
               and f.get("component") == component for f in report["failures"]):
        raise ValueError("scored report has no corresponding failed candidate")
    all_results = wh.results
    results = all_results[all_results["election_id"] == election_id]
    scored = score_frozen_predictions({component: replacement}, results)[component]
    if scored.get("status") != "ok":
        raise ValueError("replacement prediction could not be scored")
    prefix = f"{year}:{lead_days}:"
    truths = report["oof_truths"]
    for rid, mu, sd in zip(replacement.race_ids, replacement.means, replacement.sds):
        case = prefix + rid
        if case not in truths:
            continue
        report["oof_means"].setdefault(component, {})[case] = float(mu)
        report["oof_sds"].setdefault(component, {})[case] = float(sd)
        report["oof_draws"].setdefault(component, {})[case] = replacement.draws_by_race[rid]
    report["by_fold"][str(year)]["leads"][str(lead_days)][component] = scored
    report["failures"] = [f for f in report["failures"] if not (
        f.get("year") == year and f.get("lead") == lead_days
        and f.get("component") == component
    )]
    report["frozen_draws_sha256"] = _draws_fingerprint(report["oof_draws"])
    report = rescore_frozen_oof_draws(report)
    report.setdefault("inference_repairs", []).append({
        "year": year, "lead_days": lead_days, "component": component,
        "reason": "initial freeze failed convergence diagnostics; no truth used in refit",
        "draws_per_chain": draws_per_chain, "tune_per_chain": tune_per_chain,
        "chains": chains, "seed": int(original["seed"]),
    })
    original.update({
        "status": "ok", "n_races": len(replacement.race_ids),
        "method": replacement.method, "n_draws": replacement.n_draws,
        "fit_settings": replacement.fit_settings,
        "prediction_sha256": replacement.prediction_sha256,
        "evidence_snapshot_id": replacement.evidence_snapshot_id,
        "prior_snapshot_sha256": replacement.prior_snapshot_sha256,
        "presidential_source_sha256": replacement.presidential_source_sha256,
        "error": None,
    })
    report_tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    index_tmp = index_path.with_suffix(index_path.suffix + ".tmp")
    index_tmp.write_text(json.dumps(index, indent=2, default=str))
    report["frozen_index_sha256"] = hashlib.sha256(index_tmp.read_bytes()).hexdigest()
    report["frozen_index_semantic_sha256"] = frozen_index_semantic_sha256(index)
    report_tmp.write_text(json.dumps(report, separators=(",", ":"), default=str))
    # A process interruption between replacements is detected by the index
    # fingerprint before stack fitting.
    index_tmp.replace(index_path)
    report_tmp.replace(out_path)
    return {"year": year, "lead_days": lead_days, "component": component,
            "remaining_failures": len(report["failures"]), "n_oof_cases": len(truths)}


def run_nested_component_loo(
    *,
    years: tuple[int, ...] = (2018, 2020, 2022, 2024),
    lead_days: tuple[int, ...] = (60, 30),
    hierarchical_method: str = "pymc",
    n_draws: int = 1600,
    seed: int = 21,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """
    Outer leave-one-cycle-out: freeze every component's predictions, then score.

    Writes ``nested_component_loo.json`` with OOF CRPS matrix, failures, and G8
    recommendations. Does **not** remap fast→pymc.
    """
    wh = Warehouse()
    spine = (
        "pymc_dynamic"
        if hierarchical_method.startswith("pymc_dynamic")
        else ("pymc" if hierarchical_method.startswith("pymc") else "fast_hierarchical_t")
    )
    by_fold: dict[str, Any] = {}
    crps_by_fold: dict[str, dict[str, float]] = {}
    oof_means_by_fold: dict[str, dict[str, dict[str, float]]] = {}
    oof_sds_by_fold: dict[str, dict[str, dict[str, float]]] = {}
    oof_draws_by_fold: dict[str, dict[str, dict[str, list[float]]]] = {}
    truths_by_fold: dict[str, dict[str, float]] = {}
    failures: list[dict[str, Any]] = []
    frozen_archive: list[dict[str, Any]] = []
    prior_snapshot_sha256_by_fold_lead: dict[str, dict[str, str | None]] = {}
    source_set_sha256_by_fold_lead: dict[str, dict[str, str | None]] = {}

    for year in years:
        election_id = f"senate-{year}"
        races = wh.races[wh.races["election_id"] == election_id]
        if races.empty:
            failures.append({"year": year, "error": "no races"})
            continue
        ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
        # Load results only after freeze — but we need them for scoring after.
        # Structure: freeze all leads first, then score.
        lead_frozen: dict[str, dict[str, FrozenPrediction]] = {}
        prior_snapshot_sha256_by_fold_lead[str(year)] = {}
        source_set_sha256_by_fold_lead[str(year)] = {}
        for lead in lead_days:
            as_of = ed - timedelta(days=lead)
            snap = wh.build_as_of(as_of, election_id)
            prior_snapshot_sha256_by_fold_lead[str(year)][str(lead)] = snap.prior_snapshot_sha256
            source_set_sha256_by_fold_lead[str(year)][str(lead)] = snap.presidential_source_sha256
            frozen = freeze_component_predictions(
                snap,
                election_id=election_id,
                holdout_year=year,
                lead_days=lead,
                hierarchical_method=hierarchical_method,
                n_draws=n_draws,
                seed=seed + year + lead,
            )
            lead_frozen[str(lead)] = frozen
            for name, fp in frozen.items():
                frozen_archive.append(asdict(fp))
                if fp.status == "failed":
                    failures.append(
                        {
                            "year": year,
                            "lead": lead,
                            "component": name,
                            "error": fp.error,
                        }
                    )

        # Truth contact — after all freezes for this cycle
        all_results = wh.results
        results = all_results[all_results["election_id"] == election_id]
        from midterms.evidence.score_targets import truth_margin_map

        truth_by_id = truth_margin_map(results)
        fold_scores_acc: dict[str, list[float]] = {}
        lead_blocks: dict[str, Any] = {}
        oof_means_fold: dict[str, dict[str, float]] = {}
        oof_sds_fold: dict[str, dict[str, float]] = {}
        oof_draws_fold: dict[str, dict[str, list[float]]] = {}
        for lead, frozen in lead_frozen.items():
            scored = score_frozen_predictions(frozen, results)
            lead_blocks[lead] = scored
            for name, sc in scored.items():
                if sc.get("status") == "ok" and sc.get("n") and np.isfinite(sc.get(G8_METRIC, np.nan)):
                    fold_scores_acc.setdefault(name, []).append(float(sc[G8_METRIC]))
            # Each predeclared lead is a separate OOF case. Never overwrite an
            # earlier lead with a later freeze for the same subject.
            for name, fp in frozen.items():
                if fp.status != "ok":
                    continue
                block = oof_means_fold.setdefault(name, {})
                sblock = oof_sds_fold.setdefault(name, {})
                dblock = oof_draws_fold.setdefault(name, {})
                for rid, mu, sd in zip(fp.race_ids, fp.means, fp.sds):
                    if rid in truth_by_id:
                        case_id = f"{lead}:{rid}"
                        block[case_id] = float(mu)
                        sblock[case_id] = float(max(sd, 0.5))
                        if fp.draws_by_race and rid in fp.draws_by_race:
                            dblock[case_id] = fp.draws_by_race[rid]

        fold_mean = {k: float(np.mean(v)) for k, v in fold_scores_acc.items() if v}
        crps_by_fold[str(year)] = fold_mean
        oof_means_by_fold[str(year)] = oof_means_fold
        oof_sds_by_fold[str(year)] = oof_sds_fold
        oof_draws_by_fold[str(year)] = oof_draws_fold
        truths_by_fold[str(year)] = {
            f"{lead}:{rid}": float(y)
            for lead in lead_frozen for rid, y in truth_by_id.items()
        }
        by_fold[str(year)] = {
            "election_id": election_id,
            "spine": spine,
            "leads": lead_blocks,
            "mean_crps": fold_mean,
            "freeze_before_truth": True,
            "n_oof_races": len(truth_by_id),
            "n_oof_cases": len(truth_by_id) * len(lead_frozen),
        }
        print(f"[nested-loo] year={year} components={len(fold_mean)}", flush=True)

    mean_crps = {
        k: float(np.mean([fold[k] for fold in crps_by_fold.values() if k in fold]))
        for k in {n for fold in crps_by_fold.values() for n in fold}
    }
    spine_mean = mean_crps.get(spine)
    # Honest OOF weights — no spine remapping
    diagnostic_score_softmax = weights_from_oof_scores(crps_by_fold)
    g8 = _g8_recommendations(crps_by_fold, spine=spine)

    # Flatten race-level OOF means / sds / truths across folds for predictive stacking (R-08)
    oof_means_flat: dict[str, dict[str, float]] = {}
    oof_sds_flat: dict[str, dict[str, float]] = {}
    oof_draws_flat: dict[str, dict[str, list[float]]] = {}
    truths_flat: dict[str, float] = {}
    for year, by_comp in oof_means_by_fold.items():
        for comp, races in by_comp.items():
            dest = oof_means_flat.setdefault(comp, {})
            for rid, mu in races.items():
                dest[f"{year}:{rid}"] = float(mu)
        sds_comp = oof_sds_by_fold.get(year) or {}
        draws_comp = oof_draws_by_fold.get(year) or {}
        for comp, races in sds_comp.items():
            dest_s = oof_sds_flat.setdefault(comp, {})
            for rid, sd in races.items():
                dest_s[f"{year}:{rid}"] = float(sd)
        for comp, races in draws_comp.items():
            dest_d = oof_draws_flat.setdefault(comp, {})
            for rid, values in races.items():
                dest_d[f"{year}:{rid}"] = values
        for rid, y in (truths_by_fold.get(year) or {}).items():
            truths_flat[f"{year}:{rid}"] = float(y)

    reliability_block = _nested_reliability_block(
        spine=spine,
        oof_means=oof_means_flat,
        oof_sds=oof_sds_flat,
        oof_truths=truths_flat,
    )

    report = {
        "audit_item": "P2.1",
        "g8_metric": G8_METRIC,
        "oof_crps_method": "exact_empirical_predictive_draws",
        "years": list(years),
        "lead_days": list(lead_days),
        "hierarchical_method": hierarchical_method,
        "model_version": MODEL_VERSION,
        "stack_training_protocol": "formal_60_30_v1" if lead_days == (60, 30) else "diagnostic_custom_leads",
        "pymc_validation_inference": {
            "draws_per_chain_floor": OOF_PYMC_DRAWS_PER_CHAIN,
            "tune_per_chain_floor": OOF_PYMC_TUNE_PER_CHAIN,
            "chains": OOF_PYMC_CHAINS,
        },
        "prior_snapshot_sha256_by_fold_lead": prior_snapshot_sha256_by_fold_lead,
        "presidential_source_sha256_by_fold_lead": source_set_sha256_by_fold_lead,
        "spine_label": spine,
        "n_draws": n_draws,
        "freeze_before_truth": True,
        "no_weight_remapping": True,
        "crps_by_fold": crps_by_fold,
        "oof_means": oof_means_flat,
        "oof_sds": oof_sds_flat,
        "oof_draws": oof_draws_flat,
        "frozen_draws_sha256": _draws_fingerprint(oof_draws_flat),
        "oof_truths": truths_flat,
        "mean_crps": spine_mean,
        "mean_crps_by_component": mean_crps,
        "diagnostic_score_softmax_weights": diagnostic_score_softmax,
        "g8_recommendations": g8,
        "reliability": reliability_block,
        "failures": failures,
        "by_fold": by_fold,
        "n_frozen_predictions": len(frozen_archive),
        "n_outer_years": len(crps_by_fold),
        "n_oof_races": len({case.split(":", 2)[2] for case in truths_flat}),
        "n_oof_cases": len(truths_flat),
        "exit_condition": "Predictions are frozen before the held-out truth is read.",
        "note": (
            "Outer leave-one-cycle-out for every stackable component + structural "
            "terminal ablations. Each declared lead is retained as a separate frozen "
            "case; empirical draws feed predictive-mixture stacking."
        ),
    }
    out_path = out_path or (ARTIFACTS_DIR / "nested_component_loo.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report["path"] = str(out_path)
    # Persist a compact freeze index (not full means) alongside report
    freeze_path = out_path.with_name(f"{out_path.stem}_frozen.json")
    freeze_path.write_text(
        json.dumps(
            {
                "n": len(frozen_archive),
                "entries": [
                    {
                        "component": e["component"],
                        "holdout_year": e["holdout_year"],
                        "lead_days": e["lead_days"],
                        "as_of": e["as_of"],
                        "status": e["status"],
                        "n_races": len(e["race_ids"]),
                        "method": e["method"],
                        "n_draws": e["n_draws"],
                        "seed": e["seed"],
                        "fit_settings": e.get("fit_settings"),
                        "prediction_sha256": e.get("prediction_sha256"),
                        "evidence_snapshot_id": e.get("evidence_snapshot_id"),
                        "prior_snapshot_sha256": e.get("prior_snapshot_sha256"),
                        "presidential_source_sha256": e.get("presidential_source_sha256"),
                        "error": e.get("error"),
                    }
                    for e in frozen_archive
                ],
            },
            indent=2,
        )
    )
    report["frozen_index_path"] = str(freeze_path)
    report["frozen_index_sha256"] = hashlib.sha256(freeze_path.read_bytes()).hexdigest()
    report["frozen_index_semantic_sha256"] = frozen_index_semantic_sha256(freeze_path)
    # Frozen draws dominate this artifact. Compact encoding keeps the
    # reproducibility record practical to version and transfer.
    out_path.write_text(json.dumps(report, separators=(",", ":"), default=str))
    return report
