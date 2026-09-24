"""End-to-end forecast pipeline: as-of → fit → joint chamber → artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.baselines.models import BASELINES
from midterms.config import (
    ARTIFACTS_DIR,
    DEMO_AS_OF,
    DEMO_ELECTION_ID,
    DEMO_JOINT_SIMS,
    DEMO_SEED,
    MANIFESTS_DIR,
    MODEL_VERSION,
    PRIMARY_HOLDOUT,
    PRODUCTION_JOINT_SIMS,
    PUBLIC_LIVE_ENABLED,
    PUBLICATION_SURFACE_DEFAULT,
    ROOT,
)
from midterms.evidence.outcome_identity import INDEPENDENT_DEM_CAUCUSES_BASIS
from midterms.evidence.warehouse import Warehouse, write_run_manifest
from midterms.model.pymc_model import (
    FitResult,
    draws_from_baseline_forecasts,
    fit_fast_approximation,
    fit_pymc,
    fit_pymc_dynamic,
)
from midterms.pipeline.inference_settings import resolve_inference_settings
from midterms.simulate.chamber import independent_bernoulli_foil, simulate_chamber


def _git_commit() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        return "unknown"


def _hash_obj(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _load_stack_weights(
    *, spine: str = "pymc", require_predictive_stack: bool = False
) -> tuple[dict[str, float], dict[str, Any]]:
    """Load OOF CRPS mixture weights (audit P2.2 — no silent remapping)."""
    from midterms.validation.stack_weights import load_oof_stack_weights

    # An existing but stale stack must never degrade to legacy/default weights.
    if (ARTIFACTS_DIR / "stack_weights_oof.json").exists():
        weights, provenance = load_oof_stack_weights(require_reproducible=True)
        if not weights:
            raise ValueError("OOF stack artifact contains no production weights")
        provenance = dict(provenance)
        provenance["spine"] = spine
        provenance["weights"] = weights
        return weights, provenance

    if require_predictive_stack:
        raise FileNotFoundError(
            "publication-quality execution requires a reproducible empirical "
            "predictive-mixture OOF stack artifact"
        )

    # A small deterministic default keeps development fixtures usable. It is
    # explicitly non-publication and never inherits diagnostic score softmax.
    defaults = {
        "pymc": 0.40,
        "state_space": 0.20,
        "ridge_fundamentals": 0.25,
        "last_election_swing": 0.15,
    }
    provenance = {
        "source": "development_defaults_non_publication",
        "path": None,
        "spine": spine,
        "no_weight_remapping": True,
        "predictive_mixture_validated": False,
    }
    weights = dict(defaults)
    provenance["weights"] = weights
    return weights, provenance


def _json_safe(obj: Any) -> Any:
    """Replace NaN/Inf with None so dumps are browser-parseable (strict JSON)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        val = float(obj)
        if np.isnan(val) or np.isinf(val):
            return None
        return val
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


def _available_stack_weights(
    weights: dict[str, float], component_draws: dict[str, np.ndarray],
    *, require_publishable: bool,
) -> dict[str, float]:
    """Never reassign missing model mass to a differently named component."""
    missing = sorted(name for name, mass in weights.items()
                     if mass > 0 and name not in component_draws)
    if missing and require_publishable:
        raise ValueError(f"production stack components unavailable: {', '.join(missing)}")
    return {name: float(mass) for name, mass in weights.items()
            if mass > 0 and name in component_draws}


def run_forecast(
    *,
    election_id: str = DEMO_ELECTION_ID,
    as_of: str = DEMO_AS_OF,
    method: str = "pymc",
    draws: int | None = None,
    tune: int | None = None,
    chains: int | None = None,
    seed: int = DEMO_SEED,
    generic_ballot: float = -1.0,
    ensemble: bool = True,
    with_ratings: bool = True,
    with_markets: bool = True,
    rating_weight: float = 0.15,
    market_weight: float = 0.12,
    control_weight: float = 0.15,
    control_calibrate: bool = False,
    out_dir: Path | None = None,
    allow_fast_fallback: bool = False,
    generic_ballot_meta: dict[str, Any] | None = None,
    require_publishable: bool = False,
    allow_non_publication: bool = True,
    rebuild_mode: bool = False,
    n_joint_sims: int | None = None,
) -> dict[str, Any]:
    draws, tune, chains = resolve_inference_settings(
        draws=draws,
        tune=tune,
        chains=chains,
        require_publishable=require_publishable,
        method=method,
        allow_fast_fallback=allow_fast_fallback,
    )
    overlay_validation: dict[str, Any] = {
        "policy": "development_requested_overlays",
        "use_ratings": bool(with_ratings),
        "use_race_markets": bool(with_markets),
        "use_control_market": bool(with_markets),
    }
    if require_publishable:
        from midterms.validation.overlay_validation import publication_overlay_policy

        overlay_validation = publication_overlay_policy(
            rating_weight=rating_weight,
            market_weight=market_weight,
            control_weight=control_weight,
        )
        # Clear policy: unvalidated optional layers are compare-only and the
        # publication fit runs core-only for those layers.
        with_ratings = bool(with_ratings and overlay_validation["use_ratings"])
        with_markets = bool(
            with_markets
            and overlay_validation["use_race_markets"]
            and overlay_validation["use_control_market"]
        )
    from midterms.evidence.approval import approval_as_of, write_approval_store
    from midterms.evidence.demography import attach_demo_features
    from midterms.evidence.economics import try_refresh_alfred, yoy_growth_as_of
    from midterms.evidence.eligibility import (
        assert_publishable,
        effective_production_domain_contract,
        write_eligibility_report,
    )
    from midterms.evidence.expert_ratings import (
        ensure_expert_ratings_store,
        ratings_for_races,
    )
    from midterms.evidence.fec import attach_fundraising_to_races, write_finance_store
    from midterms.evidence.markets import (
        load_control_market,
        load_race_markets,
        write_markets_store,
    )
    from midterms.evidence.peers import compare_to_peers, write_peer_snapshots
    from midterms.model.challengers import build_challenger_draws
    from midterms.model.overlays import (
        apply_control_market_overlay,
        apply_market_overlay,
        apply_rating_overlay,
        calibrate_draws_to_control,
        overlay_report,
        shift_draws_to_means,
    )
    from midterms.model.scenarios import run_scenarios
    from midterms.model.state_space import fit_state_space
    from midterms.model.turnout import turnout_layer, undecided_allocation
    from midterms.ops.reproducibility import (
        environment_lock,
        portable_artifact_reference,
        snapshot_domain_hashes,
    )
    from midterms.ops.run_coherence import (
        evidence_manifest_fingerprint,
        stamp_eligibility_identity,
    )
    from midterms.ops.signing import sign_payload
    from midterms.simulate.institutional import (
        apply_vacancy_defaults,
        maybe_materialize_runoff_rows,
    )

    # Evidence eligibility (audit P0.4) — before fitting so ineligible runs are labeled
    effective_domain_contract = effective_production_domain_contract(
        use_ratings=with_ratings, use_markets=with_markets,
    )
    eligibility = assert_publishable(
        election_id,
        as_of=str(as_of)[:10],
        allow_non_publication=allow_non_publication and not require_publishable,
        domain_contract=effective_domain_contract,
    )
    if require_publishable and not eligibility.get("publishable"):
        raise ValueError(
            "require_publishable=True but evidence is ineligible: "
            + "; ".join(eligibility.get("reasons") or [])
        )

    joint_sims = int(
        n_joint_sims
        if n_joint_sims is not None
        else (PRODUCTION_JOINT_SIMS if require_publishable else DEMO_JOINT_SIMS)
    )

    # Ensure economic + finance + ratings + markets stores exist
    layer_warnings: list[dict[str, str]] = []
    if not eligibility.get("publishable"):
        layer_warnings.append(
            {
                "layer": "evidence_eligibility",
                "error": "non_publication: " + "; ".join(eligibility.get("reasons") or []),
                "run_class": "non_publication",
            }
        )
    if not rebuild_mode:
        try:
            # Never call write_economic_store() bare — that clobbers FRED with fixtures.
            econ_meta = try_refresh_alfred(as_of=str(as_of)[:10])
            if econ_meta.get("used_fixtures") and not econ_meta.get("live_rows"):
                err = econ_meta.get("error") or "FRED refresh unavailable; fixture canaries only"
                if econ_meta.get("timeout"):
                    err = f"economics read timed out: {err}"
                layer_warnings.append(
                    {
                        "layer": "economics",
                        "error": err,
                        "publication_eligible": False,
                        "note": econ_meta.get("note"),
                    }
                )
            elif econ_meta.get("timeout") and econ_meta.get("source") == "worldbank_gdppc_yoy":
                layer_warnings.append(
                    {
                        "layer": "economics",
                        "error": "FRED timed out; using World Bank GDPPC YoY substitute",
                        "publication_eligible": True,
                        "note": econ_meta.get("note"),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            layer_warnings.append(
                {"layer": "economics", "error": str(exc), "publication_eligible": False}
            )
        try:
            write_approval_store()
        except Exception as exc:  # noqa: BLE001
            layer_warnings.append({"layer": "approval", "error": str(exc)})
        try:
            from midterms.evidence.demography import write_demography_store

            write_demography_store()
        except Exception as exc:  # noqa: BLE001
            layer_warnings.append({"layer": "demographics", "error": str(exc)})
        # Never backdate living Wikipedia/curated ratings onto historical as-of stamps.
        if str(election_id).endswith("-2026"):
            ratings_meta = ensure_expert_ratings_store(
                election_id=election_id, available_at=str(as_of)[:10]
            )
            if ratings_meta.get("wiki_error"):
                layer_warnings.append(
                    {"layer": "expert_ratings", "error": str(ratings_meta["wiki_error"])}
                )
        else:
            ratings_meta = {
                "skipped": True,
                "reason": "historical election — refuse living ratings refresh with backdated available_at",
                "election_id": election_id,
                "as_of": str(as_of)[:10],
            }
        try:
            write_finance_store(election_id=election_id)
        except Exception as exc:  # noqa: BLE001
            layer_warnings.append({"layer": "finance", "error": str(exc)})
        markets_meta: dict[str, Any] = {}
        try:
            markets_meta = write_markets_store(election_id=election_id, available_at=str(as_of)[:10])
        except Exception as exc:  # noqa: BLE001
            markets_meta = {"error": str(exc), "n_races": 0}
            layer_warnings.append({"layer": "markets", "error": str(exc)})
        try:
            write_peer_snapshots()
        except Exception as exc:  # noqa: BLE001
            layer_warnings.append({"layer": "peers", "error": str(exc)})
        # Re-audit after store refresh so run_class matches the snapshot we fit on.
        eligibility = assert_publishable(
            election_id,
            as_of=str(as_of)[:10],
            allow_non_publication=allow_non_publication and not require_publishable,
            domain_contract=effective_domain_contract,
        )
        if require_publishable and not eligibility.get("publishable"):
            raise ValueError(
                "require_publishable=True but evidence still ineligible after refresh: "
                + "; ".join(eligibility.get("reasons") or [])
            )
    else:
        ratings_meta = {}
        markets_meta = {}

    wh = Warehouse(ensure_fixtures=False)
    snap = wh.build_as_of(as_of, election_id)
    evidence_bundle = None
    if require_publishable:
        import os

        from midterms.evidence.preparation import load_latest_evidence_bundle

        evidence_bundle = load_latest_evidence_bundle(
            expected_id=os.environ.get("EVIDENCE_BUNDLE_ID") or None,
        )
        if evidence_bundle.get("model_version") != MODEL_VERSION:
            raise ValueError("sealed evidence bundle model version differs from current code")
        if str(evidence_bundle.get("as_of")) != str(as_of)[:10]:
            raise ValueError("sealed evidence bundle as_of differs from requested forecast cutoff")
        if evidence_bundle.get("current_snapshot_id") != snap.snapshot_id:
            raise ValueError("sealed evidence bundle current snapshot differs from warehouse snapshot")
    if require_publishable and not bool((snap.candidate_timeline or {}).get("production_eligible")):
        raise ValueError(
            "publication candidate/race snapshot lacks a complete bitemporal timeline: "
            + str((snap.candidate_timeline or {}).get("status") or "missing")
        )
    # Overlay FEC fundraising shares onto race rows used by fundamentals
    snap.races = attach_fundraising_to_races(snap.races, as_of=as_of)
    from midterms.evidence.demography import demographic_snapshot_as_of

    demographic_lookup, _demographic_meta = demographic_snapshot_as_of(
        as_of,
        require_point_in_time=require_publishable,
    )
    snap.races = attach_demo_features(
        apply_vacancy_defaults(snap.races),
        lookup=demographic_lookup,
    )
    if election_id == "senate-2026":
        from midterms.evidence.outcome_identity import require_explicit_caucus

        active_ids = snap.races.loc[~snap.races["not_up"].fillna(False), "race_id"].astype(str).tolist()
        require_explicit_caucus(snap.races, active_ids)
    from midterms.evidence.outcome_identity import require_binary_chamber_compatibility

    require_binary_chamber_compatibility(snap.races)
    year = None
    try:
        year = int(str(snap.races["election_day"].iloc[0])[:4])
    except Exception:  # noqa: BLE001
        year = None
    yoy = yoy_growth_as_of(
        as_of, election_year=year,
        require_historical_vintage=require_publishable,
    )
    if require_publishable and yoy is None:
        raise ValueError("publication fundamentals require a verified real-time economic vintage")
    if yoy is not None:
        snap.races = snap.races.copy()
        snap.races["real_income_yoy"] = yoy
    appr = approval_as_of(as_of, election_year=year)
    snap.races = snap.races.copy()
    snap.races["pres_approval"] = float(appr["net_approval"])
    snap.races["white_house_party"] = appr["white_house_party"]

    if method == "pymc":
        try:
            fit = fit_pymc(
                snap, draws=draws, tune=tune, chains=chains, seed=seed, generic_ballot=generic_ballot
            )
        except Exception as exc:  # noqa: BLE001
            if not allow_fast_fallback:
                raise
            layer_warnings.append({"layer": "pymc", "error": str(exc), "degraded": "fast"})
            fit = fit_fast_approximation(
                snap, n_draws=max(draws * chains, 2000), seed=seed, generic_ballot=generic_ballot
            )
            fit.diagnostics = {
                **(fit.diagnostics or {}),
                "degraded_from": "pymc",
                "degraded_reason": str(exc),
                "production_note": "fast hierarchical-t is a non-production approximation",
            }
            fit.method = "degraded:fast"
    elif method == "pymc_dynamic":
        try:
            fit = fit_pymc_dynamic(
                snap, draws=draws, tune=tune, chains=chains, seed=seed, generic_ballot=generic_ballot
            )
        except Exception as exc:  # noqa: BLE001
            if not allow_fast_fallback:
                raise
            layer_warnings.append({"layer": "pymc_dynamic", "error": str(exc), "degraded": "pymc"})
            fit = fit_pymc(
                snap, draws=draws, tune=tune, chains=chains, seed=seed, generic_ballot=generic_ballot
            )
            fit.diagnostics = {
                **(fit.diagnostics or {}),
                "degraded_from": "pymc_dynamic",
                "degraded_reason": str(exc),
            }
    elif method == "state_space":
        fit = fit_state_space(
            snap, n_draws=max(draws * chains, 2000), seed=seed, generic_ballot=generic_ballot
        )
    else:
        fit = fit_fast_approximation(
            snap, n_draws=max(draws * chains, 2500), seed=seed, generic_ballot=generic_ballot
        )
        fit.diagnostics = {
            **(fit.diagnostics or {}),
            "production_note": "fast hierarchical-t is a non-production approximation; prefer pymc",
        }

    # Ensure predictive draw count meets MCSE floor (P2.3)
    from midterms.validation.numerical_quality import MIN_SIM_DRAWS

    if fit.draws_margin.shape[0] < MIN_SIM_DRAWS:
        need = MIN_SIM_DRAWS - fit.draws_margin.shape[0]
        extra = np.random.default_rng(seed + 99).choice(
            fit.draws_margin, size=need, replace=True, axis=0
        )
        padded = np.vstack([fit.draws_margin, extra])
        fit = FitResult(
            race_ids=fit.race_ids,
            states=fit.states,
            mean_margin=padded.mean(axis=0),
            sd_margin=padded.std(axis=0),
            draws_margin=padded,
            house_effects=fit.house_effects,
            diagnostics={**(fit.diagnostics or {}), "sim_draws_padded_to": MIN_SIM_DRAWS},
            method=fit.method,
        )

    stack_weights = None
    stack_provenance: dict[str, Any] | None = None
    spine_method = str(getattr(fit, "method", method))
    if ensemble and (
        method in {"fast", "state_space", "pymc", "pymc_dynamic"}
        or str(getattr(fit, "method", "")).startswith("degraded")
    ):
        weights, stack_provenance = _load_stack_weights(
            spine=spine_method.split("+")[0],
            require_predictive_stack=require_publishable,
        )
        if require_publishable:
            if stack_provenance.get("source_model_version") != MODEL_VERSION:
                raise ValueError("production stack model version differs from current code")
            if evidence_bundle and stack_provenance.get("source_evidence_bundle_id") != evidence_bundle.get("evidence_bundle_id"):
                raise ValueError("production stack evidence bundle differs from current forecast bundle")
            if stack_provenance.get("source_stack_training_protocol") != "formal_60_30_v1":
                raise ValueError("production stack was not trained on the declared 60/30 protocol")
            prior_folds = stack_provenance.get("source_prior_snapshot_sha256_by_fold_lead") or {}
            source_folds = stack_provenance.get("source_presidential_source_sha256_by_fold_lead") or {}
            prior_digests = [digest for leads in prior_folds.values() for digest in leads.values()]
            source_digests = [digest for leads in source_folds.values() for digest in leads.values()]
            if not prior_digests or any(not digest for digest in prior_digests):
                raise ValueError("production stack lacks complete prior snapshot lineage")
            if not source_digests or any(digest != snap.presidential_source_sha256 for digest in source_digests):
                raise ValueError("production stack presidential source lineage differs from current snapshot")
        component_draws: dict[str, np.ndarray] = {fit.method.split("+")[0]: fit.draws_margin}
        core_name = "fast_hierarchical_t"
        if fit.method.startswith("pymc_dynamic"):
            core_name = "pymc_dynamic"
            component_draws["pymc_dynamic"] = fit.draws_margin
        elif fit.method.startswith("pymc"):
            core_name = "pymc"
            component_draws["pymc"] = fit.draws_margin
        elif fit.method.startswith("fast") or fit.method.startswith("degraded"):
            component_draws["fast_hierarchical_t"] = fit.draws_margin
        elif fit.method.startswith("state_space"):
            core_name = "state_space"
            component_draws["state_space"] = fit.draws_margin
        else:
            component_draws[core_name] = fit.draws_margin
        # Static and dynamic PyMC are independent fitted candidates. Each
        # positive OOF weight requires its own predictive distribution.
        if weights.get("pymc", 0.0) > 0 and "pymc" not in component_draws:
            separate_static = fit_pymc(
                snap, draws=draws, tune=tune, chains=chains,
                seed=seed + 29, generic_ballot=generic_ballot,
            )
            component_draws["pymc"] = separate_static.draws_margin
        if weights.get("pymc_dynamic", 0.0) > 0 and "pymc_dynamic" not in component_draws:
            separate_dynamic = fit_pymc_dynamic(
                snap, draws=draws, tune=tune, chains=chains,
                seed=seed + 31, generic_ballot=generic_ballot,
            )
            component_draws["pymc_dynamic"] = separate_dynamic.draws_margin
        try:
            chall = build_challenger_draws(
                snap, n_draws=fit.draws_margin.shape[0], seed=seed,
                generic_ballot=generic_ballot,
                require_historical_fit=require_publishable,
            )
            for k, v in chall.items():
                if v.shape[1] == fit.draws_margin.shape[1]:
                    component_draws[k] = v
        except Exception as exc:  # noqa: BLE001
            layer_warnings.append({"layer": "challengers", "error": str(exc)})
        for name in ("shrinkage_polls", "last_election_swing", "equal_weight_polls"):
            if name in weights and name in BASELINES:
                bl = BASELINES[name](snap)
                component_draws[name] = draws_from_baseline_forecasts(
                    bl,
                    n_draws=fit.draws_margin.shape[0],
                    seed=seed + int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big") % 10_000,
                    race_ids=fit.race_ids,
                )
        use_w = _available_stack_weights(
            weights, component_draws, require_publishable=require_publishable,
        )
        if use_w:
            from midterms.model.ensemble import stack_margin_draws

            stacked = stack_margin_draws(
                component_draws,
                use_w,
                rng=np.random.default_rng(seed + 17),
            )
            fit = FitResult(
                race_ids=fit.race_ids,
                states=fit.states,
                mean_margin=stacked.mean(axis=0),
                sd_margin=stacked.std(axis=0),
                draws_margin=stacked,
                house_effects=fit.house_effects,
                diagnostics={
                    **fit.diagnostics,
                    "ensemble": True,
                    "stack_weights": use_w,
                    "ensemble_note": (
                        "discrete CRPS mixture via stack_margin_draws "
                        "(blueprint §9 predictive stacking)"
                    ),
                    "spine_method": spine_method,
                    "stack_provenance": stack_provenance,
                },
                method="ensemble_stack",
            )
            stack_weights = use_w

    # Core fit before overlays — keep for ablation
    core_fit = fit
    unadjusted_means = fit.mean_margin.copy()
    adj = unadjusted_means.copy()
    overlay_shifts: dict[str, np.ndarray] = {}

    expert_tbl = ratings_for_races(
        fit.race_ids,
        fit.states,
        as_of=as_of,
        election_id=election_id,
        fallback_probs=[float(1 / (1 + np.exp(-m / 5.0))) for m in adj],
    )
    market_df = load_race_markets(as_of=as_of)
    if len(market_df) and "election_id" in market_df.columns:
        market_df = market_df[market_df["election_id"] == election_id]
    # Align markets by race_id first; state only as fallback for sparse Kalshi ids
    if len(market_df):
        by_id = {str(r["race_id"]): r for _, r in market_df.iterrows()} if "race_id" in market_df.columns else {}
        by_state = {str(r["state"]): r for _, r in market_df.iterrows()} if "state" in market_df.columns else {}
        aligned = []
        for rid, st in zip(fit.race_ids, fit.states):
            src = by_id.get(rid)
            if src is None:
                src = by_state.get(st)
            if src is not None:
                row = src.to_dict() if hasattr(src, "to_dict") else dict(src)
                row["race_id"] = rid
                aligned.append(row)
        market_df = pd.DataFrame(aligned) if aligned else market_df.head(0)

    used_ratings = bool(with_ratings and len(expert_tbl))
    used_markets = bool(with_markets and len(market_df))
    control = load_control_market(as_of=str(as_of)[:10])
    control_p = control.get("p_dem")
    used_control = bool(with_markets and control_p is not None and control_weight > 0)
    control_cal_meta: dict[str, Any] = {"enabled": False}
    if used_ratings:
        before = adj.copy()
        adj = apply_rating_overlay(adj, fit.race_ids, expert_tbl, weight=rating_weight)
        overlay_shifts["expert_rating_overlay"] = adj - before
    if used_markets:
        before = adj.copy()
        adj = apply_market_overlay(adj, fit.race_ids, market_df, weight=market_weight)
        overlay_shifts["race_market_overlay"] = adj - before
    # Light mean-level control pull first (helps fundamentals location)
    if used_control:
        before = adj.copy()
        adj = apply_control_market_overlay(
            adj, control_p_dem=float(control_p), weight=control_weight
        )
        overlay_shifts["control_market_national_overlay"] = adj - before
    if used_ratings or used_markets or used_control:
        shifted = shift_draws_to_means(fit.draws_margin, adj)
        # Hard chamber calibration is OFF by default (blueprint §9.4: optional soft overlay).
        if used_control and control_calibrate:
            held = snap.races[snap.races["not_up"]] if len(snap.races) else snap.races
            held_ind = int((held["held_by"] == "I").sum()) if len(held) else 0
            held_dem = int((held["held_by"] == "D").sum()) + held_ind if len(held) else 0
            shifted, control_cal_meta = calibrate_draws_to_control(
                shifted,
                held_dem=held_dem,
                control_p_dem=float(control_p),
                weight=control_weight,
                vp_tiebreak_party="R",
            )
            control_cal_meta["mode"] = "calibrate_draws_to_control"
        elif used_control:
            control_cal_meta = {
                "enabled": True,
                "mode": "soft_national_pull_only",
                "weight": control_weight,
                "target_p_dem": control_p,
                "note": "Blueprint section 9.4 soft overlay; chamber P(control) not forced to market",
            }
        fit = FitResult(
            race_ids=fit.race_ids,
            states=fit.states,
            mean_margin=shifted.mean(axis=0),
            sd_margin=shifted.std(axis=0),
            draws_margin=shifted,
            house_effects=fit.house_effects,
            diagnostics={
                **fit.diagnostics,
                "overlays": overlay_report(
                    used_ratings=used_ratings,
                    used_markets=used_markets,
                    rating_weight=rating_weight if used_ratings else 0.0,
                    market_weight=market_weight if used_markets else 0.0,
                    control_weight=control_weight if used_control else 0.0,
                    used_control=used_control,
                ),
                "control_calibration": control_cal_meta,
                "mean_shift_mae": float(np.nanmean(np.abs(adj - unadjusted_means))),
                "n_market_races": int(len(market_df)),
                "n_expert_ratings": int((expert_tbl["source"] == "expert").sum())
                if len(expert_tbl)
                else 0,
            },
            method=fit.method + "+overlays",
        )

    sim, race_summaries = simulate_chamber(
        fit,
        snap.races,
        vp_tiebreak_party="R",
        n_sims=joint_sims,
        sim_seed=seed + 101,
    )
    contested_active = snap.races[snap.races["race_id"].isin(fit.race_ids)].copy()
    # Align contested to fit order
    contested_active = contested_active.set_index("race_id").loc[fit.race_ids].reset_index()
    multiway = undecided_allocation(contested_active, fit.mean_margin)
    turnout = turnout_layer(contested_active, seed=seed)
    snap.races = maybe_materialize_runoff_rows(
        snap.races,
        multiway,
        as_of=snap.as_of if hasattr(snap, "as_of") else None,
    )
    scenarios = run_scenarios(fit, snap.races, vp_tiebreak_party="R")
    # Attach expert/market fields — display rating stays model-derived
    expert_by_id = expert_tbl.set_index("race_id") if len(expert_tbl) else None
    market_by_id = market_df.set_index("race_id") if len(market_df) else None
    for s in race_summaries:
        if expert_by_id is not None and s["race_id"] in expert_by_id.index:
            s["expert_rating"] = str(expert_by_id.loc[s["race_id"], "rating"])
            s["expert_source"] = str(expert_by_id.loc[s["race_id"], "source"])
        if market_by_id is not None and s["race_id"] in market_by_id.index:
            s["market_p_dem"] = float(market_by_id.loc[s["race_id"], "p_dem"])
            s["market_liquidity"] = float(market_by_id.loc[s["race_id"], "liquidity"])

    # Ablation: unadjusted core chamber
    core_sim, _ = simulate_chamber(
        core_fit,
        snap.races,
        vp_tiebreak_party="R",
        n_sims=joint_sims,
        sim_seed=seed + 103,
    )
    ablation = {
        "unadjusted": {
            "p_dem_majority": core_sim.p_dem_majority,
            "p_rep_majority": core_sim.p_rep_majority,
            "p_fifty_fifty": core_sim.p_fifty_fifty,
            "expected_dem_seats": core_sim.expected_dem_seats,
        },
        "adjusted": {
            "p_dem_majority": sim.p_dem_majority,
            "p_rep_majority": sim.p_rep_majority,
            "p_fifty_fifty": sim.p_fifty_fifty,
            "expected_dem_seats": sim.expected_dem_seats,
        },
        "delta_p_dem_majority": float(sim.p_dem_majority - core_sim.p_dem_majority),
        "delta_expected_dem_seats": float(sim.expected_dem_seats - core_sim.expected_dem_seats),
    }

    from midterms.validation.numerical_quality import evaluate_numerical_quality

    numerical = evaluate_numerical_quality(
        seat_draws=sim.seat_draws,
        draws_margin=fit.draws_margin,
        p_dem_control=float(sim.p_dem_majority),
        n_posterior_samples=(fit.diagnostics or {}).get("n_posterior_samples")
        or (fit.diagnostics or {}).get("draws"),
        draws=(fit.diagnostics or {}).get("draws"),
        tune=(fit.diagnostics or {}).get("tune"),
        chains=(fit.diagnostics or {}).get("chains"),
        convergence=(fit.diagnostics or {}).get("convergence"),
        seed=seed,
        publishable=bool(require_publishable),
    )
    if require_publishable and not numerical.get("ok"):
        raise ValueError(
            "require_publishable=True but numerical quality gate failed: "
            + "; ".join(numerical.get("alerts") or ["unknown"])
        )
    # Evidence eligibility alone does not make a demo inference run publishable.
    run_publishable = bool(require_publishable and eligibility.get("publishable") and numerical.get("ok"))
    run_class = "publication" if run_publishable else "non_publication"

    foil = independent_bernoulli_foil(
        race_summaries, sim.held_dem, n_draws=len(sim.seat_draws), seed=seed
    )
    baselines = {}
    for name, fn in BASELINES.items():
        forecasts = fn(snap)
        baselines[name] = [f.as_dict() for f in forecasts]

    out_dir = out_dir or ARTIFACTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{election_id}_{as_of}_{fit.method}_{seed}"

    seat_hist = [
        {"dem_seats": int(k), "count": int(v), "probability": v / len(sim.seat_draws)}
        for k, v in sorted(sim.seat_histogram.items(), key=lambda kv: int(kv[0]))
    ]
    expected_rep = 100.0 - sim.expected_dem_seats
    # control already loaded above for overlay; refresh for artifact snapshot
    control = load_control_market(as_of=str(as_of)[:10])

    evidence_fp = evidence_manifest_fingerprint()
    from midterms.evidence.markets import verify_market_store_integrity

    market_integrity = verify_market_store_integrity(as_of=str(as_of)[:10])
    market_manifest = (
        json.loads((MANIFESTS_DIR / "markets_kalshi.json").read_text(encoding="utf-8"))
        if market_integrity["ok"] else {}
    )
    market_store_sha = market_manifest.get("normalized_races_sha256")
    market_audit_sha = market_integrity.get("audit_sha256")
    stack_artifact_sha = (stack_provenance or {}).get("artifact_sha256")
    from midterms.validation.decomposition_hook import build_decomposition_rows

    if snap.prior_snapshot_path:
        prior_payload = json.loads((ROOT / snap.prior_snapshot_path).read_text(encoding="utf-8"))
        prior_by_state = {row["state"]: row["provenance"] for row in prior_payload["rows"]}
    else:
        prior_by_state = {
            str(state): {"source_kind": "legacy_unverified_fixture", "production_eligible": False}
            for state in snap.races["state"].astype(str).unique()
        }
    decomposition_rows = build_decomposition_rows(
        races=snap.races, polls=snap.polls, as_of=snap.as_of,
        race_ids=fit.race_ids,
        core_means=core_fit.mean_margin, core_sds=core_fit.sd_margin,
        final_means=fit.mean_margin, final_sds=fit.sd_margin,
        prior_provenance_by_state=prior_by_state,
        generic_ballot=generic_ballot, overlay_shifts=overlay_shifts,
        error_budget=(core_fit.diagnostics or {}).get("error_budget"),
        exact_overlay_chain=not bool(used_control and control_calibrate),
    )
    decomposition_path = out_dir / "race_decomposition_latest.json"
    eligibility = stamp_eligibility_identity(
        eligibility,
        run_id=run_id,
        snapshot_id=str(snap.snapshot_id),
        forecast_generated_at=datetime.now(timezone.utc).isoformat(),
    )

    artifact = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast_as_of": as_of,
        "election_id": election_id,
        "model_version": MODEL_VERSION,
        "method": fit.method,
        "run_class": run_class,
        "publishable": run_publishable,
        "evidence_eligibility": {
            "publishable": bool(eligibility.get("publishable")),
            "run_class": eligibility.get("run_class"),
            "reasons": eligibility.get("reasons"),
            "domains": eligibility.get("domains"),
            "effective_domain_contract": eligibility.get("effective_domain_contract"),
            "forecast_run_id": run_id,
            "snapshot_id": str(snap.snapshot_id),
            "evidence_fingerprint": evidence_fp,
        },
        "evidence_fingerprint": evidence_fp,
        "evidence_bundle_id": (
            evidence_bundle.get("evidence_bundle_id") if evidence_bundle else None
        ),
        "evidence_bundle_sha256": (
            evidence_bundle.get("evidence_bundle_sha256") if evidence_bundle else None
        ),
        "presidential_source_sha256": snap.presidential_source_sha256,
        "presidential_source_years": list(snap.presidential_source_years),
        "prior_store_sha256": snap.prior_snapshot_sha256,
        "market_store_sha256": market_store_sha,
        "market_audit_sha256": market_audit_sha,
        "stack_artifact_sha256": stack_artifact_sha,
        "decomposition_artifact": decomposition_path.name,
        "snapshot_ids": {"evidence": str(snap.snapshot_id)},
        "generic_ballot": float(generic_ballot),
        "generic_ballot_meta": generic_ballot_meta
        or {"margin": float(generic_ballot), "method": "caller_supplied"},
        "snapshot": {
            **snap.to_dict(),
            "real_income_yoy": yoy,
            "n_kalshi_races": int(markets_meta.get("n_races") or len(market_df)),
            "kalshi_control_p_dem": control.get("p_dem"),
            "generic_ballot": float(generic_ballot),
        },
        "chamber": {
            "held_dem": sim.held_dem,
            "held_rep": sim.held_rep,
            "held_ind": getattr(sim, "held_ind", 0),
            "independent_caucus_policy": {
                "ballot_party": "I",
                "seat_accounting_caucus": "D",
                "basis": INDEPENDENT_DEM_CAUCUSES_BASIS,
                "type": "user_declared_model_assumption",
            },
            "majority_threshold": sim.majority_threshold,
            "p_dem_majority": sim.p_dem_majority,
            "p_rep_majority": sim.p_rep_majority,
            "p_fifty_fifty": sim.p_fifty_fifty,
            "p_tie": sim.p_fifty_fifty,
            "vp_tiebreak_party": sim.vp_tiebreak_party,
            "expected_dem_seats": sim.expected_dem_seats,
            "expected_rep_seats": expected_rep,
            "n_joint_sims": int(getattr(sim, "n_joint_sims", len(sim.seat_draws))),
            "n_posterior_margin_draws": int(
                getattr(sim, "n_posterior_margin_draws", fit.draws_margin.shape[0])
            ),
            "seat_histogram": seat_hist,
            "independent_bernoulli_foil_expected": float(np.mean(foil)),
            "note": (
                "Chamber totals from joint correlated draws. "
                "Independent candidates retain their ballot identity and enter "
                "Democratic-caucus seat totals under the declared model assumption. "
                "Display ratings are model-derived from P(Dem); expert/Kalshi overlays are ablatable. "
                "p_tie/p_fifty_fifty is P(exactly 50 Dem seats); with VP=R that outcome is Rep control."
            ),
        },
        "races": race_summaries,
        "baselines": baselines,
        "diagnostics": {
            **(fit.diagnostics or {}),
            "layer_warnings": layer_warnings,
            "core_method": spine_method,
            "allow_fast_fallback": bool(allow_fast_fallback),
            "run_class": run_class,
            "publishable": run_publishable,
            "publication_inference_requested": bool(require_publishable),
            "overlay_validation": overlay_validation,
            "n_joint_sims": int(getattr(sim, "n_joint_sims", len(sim.seat_draws))),
            "n_posterior_margin_draws": int(
                getattr(sim, "n_posterior_margin_draws", fit.draws_margin.shape[0])
            ),
            "numerical_quality": numerical,
        },
        "numerical_quality": numerical,
        "warnings": layer_warnings,
        "house_effects": fit.house_effects,
        "stack_weights": stack_weights,
        "stack_provenance": stack_provenance,
        "overlays": {
            **overlay_report(
                used_ratings=used_ratings,
                used_markets=used_markets,
                rating_weight=rating_weight if used_ratings else 0.0,
                market_weight=market_weight if used_markets else 0.0,
                control_weight=control_weight if used_control else 0.0,
                used_control=used_control,
            ),
            "markets_meta": {
                "n_races": int(len(market_df)),
                "control_p_dem": control.get("p_dem"),
                "control_p_rep": control.get("p_rep"),
            },
            "expert_source": (
                None
                if not len(expert_tbl)
                else (
                    "expert"
                    if (expert_tbl["source"] == "expert").any()
                    else str(expert_tbl["source"].iloc[0])
                )
            ),
            "control_calibration": control_cal_meta,
        },
        "ablation": ablation,
        "scenarios": scenarios,
        "auxiliary": {
            "multiway_shares": multiway,
            "turnout": turnout,
            "approval": appr,
            "note": "Turnout/multiway are auxiliary translation layers; chamber seats use two-party joint draws.",
        },
        "peer_comparison": compare_to_peers(
            {"chamber": {"p_dem_majority": sim.p_dem_majority}, "races": race_summaries}
        ),
        "publication_surface": (
            "live"
            if PUBLIC_LIVE_ENABLED and bool(eligibility.get("publishable"))
            else PUBLICATION_SURFACE_DEFAULT
        ),
        "limitations": [
            (
                "PUBLIC_LIVE_ENABLED=True after independent re-audit; publish still "
                "fail-closes on red gates / ineligible evidence / numerical quality."
                if PUBLIC_LIVE_ENABLED
                else "PUBLIC_LIVE_ENABLED=False (fresh audit Stage-0 containment)."
            ),
            (
                "Finance prefers OpenFEC totals; on API rate-limits falls back to "
                "FEC weball bulk downloads (same filings, no API key)."
            ),
            (
                "Historical ledger uses scaled two-party counts for non-digitized FEC races; "
                "OH2018/AZ2024 are exact canvass totals."
            ),
            (
                "Ensemble predictive mixture OOF-scored; learned weights favor "
                "state_space / ridge when static pymc CRPS does not earn mass. "
                "pymc_dynamic competes as a separate stack candidate after OOF freeze."
            ),
            (
                "Reference generative spine may be static pymc or pymc_dynamic; "
                "effective production mixture is whatever OOF stacking assigns. "
                "Chamber totals always come from correlated joint sims, not independent Bernoullis."
            ),
        ],
        "research_only_notice": (
            None
            if PUBLIC_LIVE_ENABLED and bool(eligibility.get("publishable"))
            else (
                "Research use only. Do not treat chamber or race probabilities as a "
                "public live product until independent re-audit clears live publish."
            )
        ),
    }

    artifact_path = out_dir / f"forecast_{run_id}.json"
    demo_path = out_dir / "forecast_latest.json"
    text = json.dumps(_json_safe(artifact), indent=2, allow_nan=False)
    payload = text.encode("utf-8")
    artifact_path.write_bytes(payload)
    demo_path.write_bytes(payload)
    decomposition_path.write_text(json.dumps(_json_safe({
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "snapshot_id": snap.snapshot_id,
        "evidence_fingerprint": evidence_fp,
        "prior_store_sha256": snap.prior_snapshot_sha256,
        "market_store_sha256": market_store_sha,
        "market_audit_sha256": market_audit_sha,
        "stack_artifact_sha256": stack_artifact_sha,
        "forecast_sha256": hashlib.sha256(payload).hexdigest(),
        "rows": decomposition_rows,
    }), indent=2, allow_nan=False), encoding="utf-8")

    prior_diagnostic = (fit.diagnostics or {}).get("prior_predictive") or {}
    (out_dir / "prior_predictive_latest.json").write_text(
        json.dumps(_json_safe({
            "schema_version": "current-model-prior-predictive-v1",
            "model_version": MODEL_VERSION,
            "run_id": run_id,
            "snapshot_id": snap.snapshot_id,
            "forecast_sha256": hashlib.sha256(payload).hexdigest(),
            "diagnostic": prior_diagnostic,
            "ok": bool(prior_diagnostic.get("ok")),
        }), indent=2, allow_nan=False),
        encoding="utf-8",
    )

    # Keep eligibility artifact identity-tied to this exact forecast run.
    write_eligibility_report(
        election_id,
        as_of=str(as_of)[:10],
        run_id=run_id,
        snapshot_id=str(snap.snapshot_id),
        forecast_generated_at=str(artifact.get("generated_at")),
        domain_contract=effective_domain_contract,
    )

    draws_path = out_dir / f"draws_{run_id}.npz"
    np.savez_compressed(
        draws_path,
        margins=fit.draws_margin.astype(np.float32),
        dem_seats=sim.seat_draws.astype(np.int16),
        race_ids=np.array(fit.race_ids),
    )

    web_copy = Path(__file__).resolve().parents[2] / "web" / "public" / "data" / "forecast_latest.json"
    if out_dir.resolve() == ARTIFACTS_DIR.resolve() and not rebuild_mode:
        web_copy.parent.mkdir(parents=True, exist_ok=True)
        web_copy.write_bytes(payload)

    configuration = {
        "method": method,
        "fit_method": method,
        "draws": draws,
        "tune": tune,
        "chains": chains,
        "seed": seed,
        "generic_ballot": generic_ballot,
        "ensemble": ensemble,
        "stack_weights": stack_weights,
        "with_ratings": with_ratings,
        "with_markets": with_markets,
        "rating_weight": rating_weight,
        "market_weight": market_weight,
        "control_weight": control_weight,
        "control_calibrate": control_calibrate,
        "allow_fast_fallback": allow_fast_fallback,
        "independent_caucus_basis": INDEPENDENT_DEM_CAUCUSES_BASIS,
        "effective_domain_contract": effective_domain_contract,
    }
    manifest = {
        "run_id": run_id,
        "generated_at": artifact["generated_at"],
        "forecast_as_of": as_of,
        "election_id": election_id,
        "model_version": MODEL_VERSION,
        "code_commit": _git_commit(),
        "configuration": configuration,
        "configuration_hash": _hash_obj(configuration),
        "snapshot_ids": {"evidence": snap.snapshot_id},
        "presidential_source_sha256": snap.presidential_source_sha256,
        "presidential_source_years": list(snap.presidential_source_years),
        "prior_store_sha256": snap.prior_snapshot_sha256,
        "market_store_sha256": market_store_sha,
        "market_audit_sha256": market_audit_sha,
        "stack_artifact_sha256": stack_artifact_sha,
        "effective_domain_contract": effective_domain_contract,
        "domain_hashes": snapshot_domain_hashes(),
        "environment_lock": environment_lock(),
        "freshness_policy_version": __import__(
            "midterms.evidence.freshness", fromlist=["FRESHNESS_POLICY_VERSION"]
        ).FRESHNESS_POLICY_VERSION,
        "seed": seed,
        "draws": fit.diagnostics.get("draws"),
        "tune": tune if method.startswith("pymc") else None,
        "chains": chains if method.startswith("pymc") else None,
        "output_hashes": {
            "forecast_json": hashlib.sha256(payload).hexdigest(),
            "draws": hashlib.sha256(draws_path.read_bytes()).hexdigest(),
        },
        "paths": {
            "forecast": portable_artifact_reference(artifact_path),
            "forecast_latest": portable_artifact_reference(demo_path),
            "draws": portable_artifact_reference(draws_path),
        },
        "rebuild_mode": bool(rebuild_mode),
    }
    if not rebuild_mode:
        manifest["signature"] = sign_payload(manifest)
        write_run_manifest(manifest)
        try:
            from midterms.ops.monitor import append_release_index, archive_release

            append_release_index(manifest)
            archive_release(manifest, text)
        except Exception:  # noqa: BLE001
            pass
    return {"artifact": artifact, "manifest": manifest, "paths": manifest["paths"]}


def replay_baselines(holdout_year: int = PRIMARY_HOLDOUT) -> dict[str, Any]:
    """As-of replay of baselines on a held-out Senate cycle (legacy entrypoint)."""
    from midterms.validation.cycle_replay import replay_cycle

    report = replay_cycle(holdout_year, include_hierarchical=False)
    out = ARTIFACTS_DIR / f"baseline_replay_{holdout_year}.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    # Strip hierarchical-only fields for backward-compatible baseline artifact
    slim = {
        "election_id": report["election_id"],
        "lead_days": {
            k: {n: s for n, s in v.items() if n in BASELINES}
            for k, v in report["lead_days"].items()
        },
        "aggregate": {n: report["aggregate"][n] for n in BASELINES if n in report["aggregate"]},
        "path": str(out),
    }
    out.write_text(json.dumps(slim, indent=2))
    slim["path"] = str(out)
    return slim
