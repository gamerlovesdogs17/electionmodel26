"""Unified G1–G11 acceptance gates (first publishable milestone).

Aggregates existing validation artifacts into a single pass/fail report.
Does not recompute heavy fits by default.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, PUBLIC_LIVE_ENABLED, ROOT


GATE_FAILURE = {
    "G1": "Stop replay.",
    "G2": "Stop scoring.",
    "G3": "Mark thin races; prohibit complete-cycle claim.",
    "G4": "Leakage failure.",
    "G5": "Invalidate run.",
    "G6": "No calibration claim.",
    "G7": "Recalibrate or widen.",
    "G8": "Disable component.",
    "G9": "Do not publish.",
    "G10": "Reject release.",
    "G11": "Withhold public artifact.",
}

MILESTONE_ARCHIVE_NOTE = (
    "Milestone archive scope is 2014–2024 official ballots + certified margins "
    "(chamber G1/G2). Poll-coverage production gate remains 2018–2024 until FTE "
    "identity polls are sealed for 2014/2016."
)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _gate(
    gate_id: str,
    *,
    name: str,
    ok: bool,
    status: str,
    detail: Any = None,
    evidence: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "name": name,
        "ok": bool(ok),
        "status": status,  # pass | fail | partial | n_a
        "detail": detail,
        "evidence": evidence or [],
        "failure_behavior": GATE_FAILURE.get(gate_id),
        "notes": notes or [],
    }


def evaluate_acceptance_gates(
    *,
    artifacts_dir: Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Build G1–G11 acceptance report from sealed artifacts."""
    art_dir = artifacts_dir or ARTIFACTS_DIR
    gates: list[dict[str, Any]] = []

    chamber = _load_json(art_dir / "chamber_reconcile_latest.json")
    poll_cov = _load_json(art_dir / "poll_coverage_latest.json")
    eligibility = _load_json(art_dir / "evidence_eligibility_latest.json")
    nested = _load_json(art_dir / "nested_component_loo.json")
    stack = _load_json(art_dir / "stack_weights_oof.json")
    forecast = _load_json(art_dir / "forecast_latest.json")
    val_report = _load_json(art_dir / "validation_report_latest.json")
    model_card = ROOT / "MODEL_CARD.md"

    # --- G1 Race universe / G2 Chamber truth ---
    # Always recompute from current warehouse/ledger (v0.9.21 — refuse stale artifacts).
    try:
        from midterms.validation.chamber_reconcile import reconcile_all_cycles

        chamber = reconcile_all_cycles()
        if write:
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            (art_dir / "chamber_reconcile_latest.json").write_text(
                json.dumps(chamber, indent=2, default=str), encoding="utf-8"
            )
    except Exception as exc:  # noqa: BLE001
        chamber = {"ok": False, "error": f"recompute_failed: {exc}", "by_cycle": {}}

    if not chamber or chamber.get("error"):
        gates.append(
            _gate(
                "G1",
                name="Race universe",
                ok=False,
                status="fail",
                detail=chamber.get("error") if chamber else "missing chamber reconcile",
                evidence=[],
            )
        )
        gates.append(
            _gate(
                "G2",
                name="Chamber truth",
                ok=False,
                status="fail",
                detail=chamber.get("error") if chamber else "missing chamber reconcile",
                evidence=[],
            )
        )
    else:
        g1_ok = bool(chamber.get("ok"))
        # Extra race/missing race signals live per-cycle
        missing = []
        for cy, block in (chamber.get("by_cycle") or {}).items():
            if block.get("missing_race_ids") or block.get("extra_race_ids"):
                missing.append(cy)
            if not block.get("ok"):
                missing.append(cy)
        gates.append(
            _gate(
                "G1",
                name="Race universe",
                ok=g1_ok and not missing,
                status="pass" if g1_ok and not missing else "fail",
                detail={
                    "gate_years": chamber.get("gate_years"),
                    "problem_cycles": sorted(set(missing)),
                    "failures": chamber.get("failures"),
                },
                evidence=[str(art_dir / "chamber_reconcile_latest.json")],
                notes=[MILESTONE_ARCHIVE_NOTE],
            )
        )
        gates.append(
            _gate(
                "G2",
                name="Chamber truth",
                ok=g1_ok,
                status="pass" if g1_ok else "fail",
                detail={
                    "gate_years": chamber.get("gate_years"),
                    "failures": chamber.get("failures"),
                },
                evidence=[str(art_dir / "chamber_reconcile_latest.json")],
                notes=[MILESTONE_ARCHIVE_NOTE],
            )
        )

    # --- G3 Poll coverage ---
    if poll_cov is None:
        gates.append(
            _gate(
                "G3",
                name="Poll coverage",
                ok=False,
                status="fail",
                detail="missing poll_coverage_latest.json",
            )
        )
    else:
        g3_ok = bool(poll_cov.get("ok"))
        gates.append(
            _gate(
                "G3",
                name="Poll coverage",
                ok=g3_ok,
                status="pass" if g3_ok else "fail",
                detail={"failures": poll_cov.get("failures"), "years": poll_cov.get("years")},
                evidence=[str(art_dir / "poll_coverage_latest.json")],
            )
        )

    # --- G4 Vintage integrity / eligibility ---
    if eligibility is None and forecast:
        eligibility = (forecast.get("evidence_eligibility") or forecast.get("diagnostics") or {}).get(
            "evidence_eligibility"
        ) or forecast.get("evidence_eligibility")
        if isinstance(eligibility, dict) is False:
            eligibility = None
    if eligibility is None:
        gates.append(
            _gate(
                "G4",
                name="Vintage integrity",
                ok=False,
                status="partial",
                detail="missing evidence_eligibility_latest.json",
                notes=["Re-run evidence-eligibility before claiming publication."],
            )
        )
    else:
        publishable = bool(eligibility.get("publishable") or eligibility.get("ok"))
        reasons = eligibility.get("reasons") or []
        run_class = eligibility.get("run_class") or "unknown"
        domains = eligibility.get("domains") or {}
        required_live = ("finance", "economics", "approval")
        domain_blockers = [
            d
            for d in required_live
            if isinstance(domains.get(d), dict)
            and domains[d].get("eligible") is False
            and domains[d].get("tier") in {"synthetic", "imputed", "untraceable"}
        ]
        coherence_notes: list[str] = []
        coherence_ok = True
        if forecast:
            fc_pub = bool(forecast.get("publishable"))
            if fc_pub != bool(eligibility.get("publishable")):
                coherence_ok = False
                coherence_notes.append(
                    f"eligibility.publishable={eligibility.get('publishable')} "
                    f"!= forecast.publishable={fc_pub}"
                )
            elig_run = str(eligibility.get("forecast_run_id") or "")
            fc_run = str(forecast.get("run_id") or "")
            if elig_run and fc_run and elig_run != fc_run:
                coherence_ok = False
                coherence_notes.append(
                    f"eligibility.forecast_run_id={elig_run} != forecast.run_id={fc_run}"
                )
            elig_fp = str((eligibility.get("evidence_fingerprint") or {}).get("sha256") or "")
            fc_fp = str((forecast.get("evidence_fingerprint") or {}).get("sha256") or "")
            if elig_fp and fc_fp and elig_fp != fc_fp:
                coherence_ok = False
                coherence_notes.append("evidence_fingerprint mismatch forecast vs eligibility")
            if not elig_fp:
                coherence_notes.append(
                    "eligibility lacks evidence_fingerprint — regenerate with current forecast"
                )
                # Fingerprint is required once a publishable forecast exists
                if fc_pub:
                    coherence_ok = False
        # Fresh audit R-04/R-05: fixture finance/economics must force non_publication
        if domain_blockers and run_class == "publication":
            g4_ok = False
            status = "fail"
        elif domain_blockers and run_class == "non_publication":
            g4_ok = True
            status = "pass"  # honest containment
        else:
            g4_ok = bool(eligibility.get("ok")) or run_class == "non_publication"
            status = "pass" if g4_ok else "fail"
            if not eligibility.get("ok") and run_class == "non_publication":
                status = "pass"
                g4_ok = True
        if not coherence_ok:
            g4_ok = False
            status = "fail"
        gates.append(
            _gate(
                "G4",
                name="Vintage integrity",
                ok=g4_ok,
                status=status,
                detail={
                    "publishable": eligibility.get("publishable"),
                    "run_class": run_class,
                    "reasons": reasons[:12],
                    "domain_blockers": domain_blockers,
                    "domains_checked": sorted(domains.keys()),
                    "coherence_ok": coherence_ok,
                    "coherence_notes": coherence_notes,
                    "forecast_run_id": eligibility.get("forecast_run_id"),
                    "evidence_fingerprint": (eligibility.get("evidence_fingerprint") or {}).get(
                        "sha256"
                    ),
                },
                evidence=[str(art_dir / "evidence_eligibility_latest.json")],
                notes=[
                    "All configured domains (finance/economics/approval/…) must be enumerated; "
                    "fixture_hash / *_FIXTURE blocks publication.",
                    "Eligibility artifact must match forecast_latest run_id / fingerprint.",
                ],
            )
        )

    # --- G5 Model identity ---
    g5_notes = []
    g5_ok = True
    g5_status = "pass"
    g5_detail: dict[str, Any] = {}
    if nested:
        g5_detail["nested_spine"] = nested.get("spine_label")
        g5_detail["nested_method"] = nested.get("hierarchical_method")
        g5_detail["no_weight_remapping"] = nested.get("no_weight_remapping")
        if nested.get("no_weight_remapping") is False:
            g5_ok = False
            g5_status = "fail"
            g5_notes.append("nested LOO remapped weights")
    if stack:
        g5_detail["stack_source_spine"] = stack.get("source_spine_label")
        g5_detail["no_weight_remapping"] = stack.get("no_weight_remapping")
        g5_detail["reproduction_ok"] = (stack.get("reproduction") or {}).get("ok")
        g5_detail["matrix_sha256"] = stack.get("matrix_sha256")
        if stack.get("no_weight_remapping") is False:
            g5_ok = False
            g5_status = "fail"
        if (stack.get("reproduction") or {}).get("ok") is False:
            g5_ok = False
            g5_status = "fail"
            g5_notes.append("stack weight reproduction failed")
        # Honest identity: pymc must not silently inherit fast weight
        weights = stack.get("stack_weights") or {}
        if "pymc" in weights and stack.get("source_spine_label") == "fast_hierarchical_t":
            g5_ok = False
            g5_status = "fail"
            g5_notes.append("pymc present in weights while OOF spine was fast (remap risk)")
    if forecast:
        method = str(forecast.get("method") or "")
        diag = forecast.get("diagnostics") or {}
        core = str(diag.get("spine_method") or diag.get("core_method") or "")
        g5_detail["forecast_method"] = method
        g5_detail["forecast_core"] = core
    if nested is None and stack is None:
        g5_ok = False
        g5_status = "fail"
        g5_notes.append("missing nested LOO / stack weight identity artifacts")
    elif stack and str(stack.get("source_hierarchical_method") or "").startswith("pymc"):
        g5_notes.append("OOF stack / nested LOO spine is pymc (aligned with production).")
        if g5_ok:
            g5_status = "pass"
    elif stack and stack.get("source_hierarchical_method") == "fast":
        # Align OOF and production: if forecast core is also fast, pass; else fail closed.
        core = str(g5_detail.get("forecast_core") or "")
        method = str(g5_detail.get("forecast_method") or "")
        forecast_is_fast = "fast" in core or method.startswith("fast")
        if forecast_is_fast and g5_ok:
            g5_status = "pass"
            g5_notes.append("OOF and forecast both use fast hierarchical spine.")
        elif g5_ok:
            g5_ok = False
            g5_status = "fail"
            g5_notes.append(
                "OOF spine is fast_hierarchical_t while production forecast is pymc — "
                "rerun nested-component-loo with --hierarchical-method pymc"
            )
    gates.append(
        _gate(
            "G5",
            name="Model identity",
            ok=g5_ok,
            status=g5_status,
            detail=g5_detail,
            evidence=[
                str(art_dir / "nested_component_loo.json"),
                str(art_dir / "stack_weights_oof.json"),
            ],
            notes=g5_notes,
        )
    )

    # --- G6 Proper scores ---
    g6_detail: dict[str, Any] = {}
    g6_ok = False
    g6_status = "fail"
    scores_present = False
    mean_crps = None
    n_folds = 0
    n_outer_years = 0
    if nested and nested.get("crps_by_fold"):
        scores_present = True
        mean_crps = nested.get("mean_crps") or nested.get("crps_mean")
        if mean_crps is None:
            by_comp = nested.get("mean_crps_by_component") or {}
            spine = nested.get("spine_label") or "fast_hierarchical_t"
            mean_crps = by_comp.get(spine) or (
                next(iter(by_comp.values()), None) if by_comp else None
            )
        n_folds = len(nested.get("crps_by_fold") or {})
        n_outer_years = len(nested.get("years") or []) or n_folds
        g6_detail["g8_metric"] = nested.get("g8_metric")
        g6_detail["mean_crps"] = mean_crps
        g6_detail["mean_crps_by_component"] = nested.get("mean_crps_by_component")
        g6_detail["n_folds"] = n_folds
        g6_detail["n_outer_years"] = n_outer_years
        g6_detail["n_oof_races"] = nested.get("n_oof_races")
        g6_detail["lead_days"] = nested.get("lead_days")
        g6_detail["freeze_before_truth"] = nested.get("freeze_before_truth")
        g6_detail["spine_label"] = nested.get("spine_label")
    cal = (val_report or {}).get("calibration") or {}
    if cal.get("brier") is not None or cal.get("margin_scores"):
        scores_present = True
        g6_detail["validation_calibration"] = {
            "n": cal.get("n"),
            "brier": cal.get("brier"),
            "has_margin_scores": bool(cal.get("margin_scores")),
            "log_score": (cal.get("margin_scores") or {}).get("log_score"),
            "calibration_slope_intercept": (cal.get("margin_scores") or {}).get(
                "calibration_slope_intercept"
            ),
            "pit": (cal.get("margin_scores") or {}).get("pit"),
        }
    # Shadow evaluations
    shadow_evals = []
    shadow_root = ROOT / "data" / "shadow"
    if shadow_root.exists():
        for p in sorted(shadow_root.glob("*/evaluation.json")):
            ev = _load_json(p)
            if ev and ev.get("scores"):
                shadow_evals.append({"path": str(p), "spine_crps": ev.get("spine_crps")})
    if shadow_evals:
        scores_present = True
        g6_detail["shadow_evaluations"] = shadow_evals[-3:]
    # Fail closed on missing/null CRPS or single-fold nested evidence (audit P1).
    multi_cycle = n_outer_years >= 2 and n_folds >= 2
    crps_ok = mean_crps is not None and isinstance(mean_crps, (int, float)) and (
        mean_crps == mean_crps
    )
    if scores_present and (nested or {}).get("freeze_before_truth") and multi_cycle and crps_ok:
        g6_ok = True
        g6_status = "pass"
    elif scores_present and crps_ok:
        g6_ok = True
        g6_status = "partial"
        g6_detail["alert"] = (
            "proper scores present but nested matrix thinner than multi-cycle requirement"
        )
    elif scores_present:
        g6_ok = False
        g6_status = "fail"
        g6_detail["alert"] = "scores present but mean_crps null/missing — blocking"
    else:
        g6_detail["alert"] = "no proper scores found"
    gates.append(
        _gate(
            "G6",
            name="Proper scores",
            ok=g6_ok,
            status=g6_status,
            detail=g6_detail,
            evidence=[
                str(art_dir / "nested_component_loo.json"),
                str(art_dir / "validation_report_latest.json"),
            ],
            notes=["Race CRPS/Brier/log + chamber scores required before calibration claims."],
        )
    )

    # --- G7 Reliability ---
    g7_ok = False
    g7_status = "fail"
    g7_detail: dict[str, Any] = {}
    rel = cal.get("reliability") or (cal.get("margin_scores") or {}).get("reliability")
    rel_gate = (cal.get("margin_scores") or {}).get("reliability_gate")
    g7_multi_cycle = multi_cycle
    g7_n = cal.get("n")
    # Prefer a source-matching raw cycle-cross-fitted production-stack block.
    # The experimental calibrated block is diagnostic only until production
    # explicitly adopts that transform.
    crossfit_path = art_dir / "stack_reliability_crossfit_latest.json"
    crossfit = _load_json(crossfit_path)
    crossfit_valid = False
    if crossfit is not None:
        try:
            from midterms.validation.stack_reliability_crossfit import (
                validate_crossfit_artifact,
            )

            crossfit_check = validate_crossfit_artifact(
                crossfit,
                nested_path=art_dir / "nested_component_loo.json",
                stack_path=art_dir / "stack_weights_oof.json",
            )
        except Exception as exc:  # noqa: BLE001
            crossfit_check = {"ok": False, "failures": [str(exc)]}
        g7_detail["crossfit_validation"] = crossfit_check
        crossfit_valid = bool(crossfit_check.get("ok"))
    else:
        g7_detail["crossfit_validation"] = {
            "ok": False,
            "failures": ["crossfit artifact missing"],
        }
    if crossfit_valid:
        raw_crossfit = crossfit.get("raw_production_stack") or {}
        rel = raw_crossfit.get("reliability")
        rel_gate = raw_crossfit.get("reliability_overconfidence")
        g7_detail["source"] = "stack_reliability_crossfit.raw_production_stack"
        g7_detail["crossfit_procedure"] = crossfit.get("procedure")
        g7_detail["crossfit_raw_metrics"] = {
            key: raw_crossfit.get(key)
            for key in ("n", "empirical_crps", "brier", "log_score")
        }
        experimental = crossfit.get("experimental_calibrated_stack") or {}
        g7_detail["experimental_calibration_not_production"] = {
            "production_adopted": experimental.get("production_adopted"),
            "g7_eligible": experimental.get("g7_eligible"),
            "reliability_overconfidence": experimental.get("reliability_overconfidence"),
            "empirical_crps": experimental.get("empirical_crps"),
            "brier": experimental.get("brier"),
            "log_score": experimental.get("log_score"),
        }
        g7_n = raw_crossfit.get("n")
        g7_multi_cycle = len(crossfit.get("outer_cycles") or []) >= 2

    # Transparently fall back to component-spine reliability when the crossfit
    # artifact is absent or rejected by lineage/leakage validation.
    nested_rel_block = (nested or {}).get("reliability") or {}
    if not crossfit_valid and nested_rel_block.get("reliability"):
        rel = nested_rel_block["reliability"]
        rel_gate = nested_rel_block.get("reliability_gate")
        g7_detail["source"] = "nested_component_loo.reliability"
        g7_detail["crossfit_fallback"] = True
        g7_detail["nested_reliability_n"] = nested_rel_block.get("n")
        g7_detail["nested_reliability_spine"] = nested_rel_block.get("spine")
        g7_detail["nested_brier"] = nested_rel_block.get("brier")
        g7_n = nested_rel_block.get("n")
    if rel_gate is None and rel:
        from midterms.validation.metrics import reliability_overconfidence

        rel_gate = reliability_overconfidence(rel)
    if rel is not None:
        g7_detail["n_bins"] = len(rel)
        g7_detail["reliability_gate"] = rel_gate
        g7_detail["sample_sizes_disclosed"] = all("n" in b for b in rel)
        g7_detail["calibration_n"] = g7_n
        g7_detail["nested_multi_cycle"] = g7_multi_cycle
        claim_ok = bool(
            rel_gate and rel_gate.get("calibration_claim_allowed") and g7_multi_cycle
        )
        overconf = bool(rel_gate and rel_gate.get("n_overconfident"))
        thin = bool((rel_gate or {}).get("thin_sample"))
        if claim_ok:
            g7_ok = True
            g7_status = "pass"
        elif overconf and not thin:
            g7_ok = False
            g7_status = "fail"
            g7_detail["alert"] = (
                "material overconfidence — recalibrate or widen; no calibration claim"
            )
        else:
            g7_ok = False
            g7_status = "fail"
            g7_detail["alert"] = (
                "reliability too thin or incomplete for a calibration claim — "
                "expand multi-cycle OOF reliability"
            )
            g7_detail["calibration_claim_allowed"] = False
    else:
        g7_detail["alert"] = "no reliability bins"
        g7_status = "fail"
        g7_ok = False
    gates.append(
        _gate(
            "G7",
            name="Reliability",
            ok=g7_ok,
            status=g7_status,
            detail=g7_detail,
            evidence=[
                str(art_dir / "validation_report_latest.json"),
                str(art_dir / "nested_component_loo.json"),
                str(crossfit_path),
            ],
            notes=[
                (
                    "G7 uses raw cross-fitted production-stack reliability when its lineage "
                    "matches; experimental calibration cannot satisfy G7 until production "
                    "adopts it."
                )
            ],
        )
    )

    # --- G8 Ablation ---
    if nested is None or not nested.get("g8_recommendations"):
        gates.append(
            _gate(
                "G8",
                name="Ablation",
                ok=False,
                status="fail",
                detail="missing g8_recommendations",
            )
        )
    else:
        recs = nested["g8_recommendations"]
        disabled = [k for k, v in recs.items() if (v or {}).get("recommend") == "disable"]
        kept = [k for k, v in recs.items() if (v or {}).get("recommend") == "keep"]
        stack_w = (stack or {}).get("stack_weights") or (stack or {}).get("stack_weights_production") or {}
        leaked = [k for k in disabled if float(stack_w.get(k) or 0) > 1e-6]
        g8_ok = len(leaked) == 0
        gates.append(
            _gate(
                "G8",
                name="Ablation",
                ok=g8_ok,
                status="pass" if g8_ok else "fail",
                detail={
                    "keep": kept,
                    "disable": disabled,
                    "n_components": len(recs),
                    "disabled_still_weighted": leaked,
                },
                evidence=[str(art_dir / "nested_component_loo.json")],
                notes=["G8-disable recommendations must be absent from production stack weights."],
            )
        )

    # --- G9 Numerical quality ---
    nq = None
    if forecast:
        nq = forecast.get("numerical_quality") or (forecast.get("diagnostics") or {}).get(
            "numerical_quality"
        )
    if nq is None:
        gates.append(
            _gate(
                "G9",
                name="Numerical quality",
                ok=False,
                status="fail",
                detail="missing numerical_quality on forecast_latest",
                notes=["Regenerate forecast on v0.9.11+; required before publish."],
            )
        )
    else:
        g9_ok = bool(nq.get("ok"))
        gates.append(
            _gate(
                "G9",
                name="Numerical quality",
                ok=g9_ok,
                status="pass" if g9_ok else "fail",
                detail={
                    "alerts": nq.get("alerts"),
                    "mcse_control": (nq.get("chamber_mcse") or {}).get("mcse_p_dem_control"),
                },
                evidence=[str(art_dir / "forecast_latest.json")],
            )
        )

    # --- G10 Reproducibility ---
    g10_detail: dict[str, Any] = {"mode": "hash_seal_and_independent"}
    g10_ok = False
    g10_status = "fail"
    rebuild_ok = False
    independent_ok = False
    shadow_all_ok = False
    try:
        from midterms.ops.reproducibility import verify_rebuild

        rebuild = verify_rebuild()
        rebuild_ok = bool(rebuild.get("ok"))
        g10_detail["verify_rebuild"] = {
            "ok": rebuild_ok,
            "run_id": rebuild.get("run_id"),
            "error": rebuild.get("error"),
            "forecast_hash_expected": rebuild.get("forecast_hash_expected"),
            "forecast_hash_actual": rebuild.get("forecast_hash_actual"),
        }
    except Exception as exc:  # noqa: BLE001
        g10_detail["verify_rebuild_error"] = str(exc)
    ind_path = art_dir / "independent_rebuild_latest.json"
    ind = _load_json(ind_path)
    forecast_run_id = str((forecast or {}).get("run_id") or "")
    forecast_generated = str((forecast or {}).get("generated_at") or "")
    if ind is not None:
        ind_run = str(ind.get("run_id") or "")
        ind_generated = str(ind.get("generated_at") or "")
        identity_match = bool(forecast_run_id) and ind_run == forecast_run_id
        # Rebuild must not predate the forecast it claims to verify.
        freshness_ok = True
        freshness_detail: dict[str, Any] = {}
        if forecast_generated and ind_generated:
            try:
                from datetime import datetime as _dt
                from datetime import timedelta as _td

                fg = _dt.fromisoformat(forecast_generated.replace("Z", "+00:00"))
                ig = _dt.fromisoformat(ind_generated.replace("Z", "+00:00"))
                # Allow small clock skew; reject rebuilds clearly before the forecast.
                freshness_ok = ig >= (fg - _td(minutes=5))
                freshness_detail = {
                    "forecast_generated_at": forecast_generated,
                    "rebuild_generated_at": ind_generated,
                    "rebuild_after_forecast": freshness_ok,
                }
            except ValueError:
                freshness_ok = False
                freshness_detail = {"error": "unparseable generated_at timestamps"}
        independent_ok = bool(ind.get("ok")) and identity_match and freshness_ok
        g10_detail["independent_rebuild"] = {
            "ok": independent_ok,
            "path": str(ind_path),
            "comparison": ind.get("comparison"),
            "domain_ok": ind.get("domain_ok"),
            "error": ind.get("error"),
            "run_id_match": identity_match,
            "forecast_run_id": forecast_run_id,
            "rebuild_run_id": ind_run,
            "freshness": freshness_detail,
            "claim": (
                "Independent rebuild verifies the sealed forecast run_id with a "
                "re-execution no older than the forecast artifact."
            ),
        }
        if not identity_match:
            g10_detail["independent_rebuild"]["block"] = (
                "rebuild run_id does not match current forecast — stale or wrong release"
            )
        if not freshness_ok:
            g10_detail["independent_rebuild"]["block"] = (
                "rebuild generated_at precedes forecast — cannot prove current release"
            )
    else:
        g10_detail["independent_rebuild"] = {
            "ok": False,
            "error": "missing independent_rebuild_latest.json — run verify-rebuild --independent",
        }
    try:
        from midterms.ops.shadow_publish import list_shadow_publications, verify_shadow
        from midterms.config import PUBLIC_LIVE_ENABLED as _LIVE

        rows = list_shadow_publications()
        forecast_mv = str((forecast or {}).get("model_version") or MODEL_VERSION)
        forecast_run = str((forecast or {}).get("run_id") or "")
        # Prefer seals for the current forecast/model; do not let ancient
        # rotated-key shadows alone veto a fresh seal.
        relevant = [
            r
            for r in rows
            if (forecast_run and forecast_run in str(r.get("shadow_id") or ""))
            or str(r.get("model_version") or "") == forecast_mv
            or str(r.get("mode") or "") in {"prospective_live", "milestone"}
        ]
        check_rows = (relevant or rows)[-5:]
        shadow_checks = []
        for row in check_rows:
            ver = verify_shadow(path=Path(row["path"]))
            shadow_checks.append(
                {
                    "shadow_id": row.get("shadow_id"),
                    "ok": ver.get("ok"),
                    "hashes_ok": ver.get("hashes_ok"),
                    "signatures_ok": ver.get("signatures_ok"),
                    "signature_status": ver.get("signature_status"),
                    "path": row.get("path"),
                    "model_version": row.get("model_version"),
                }
            )
        g10_detail["shadow_verify"] = shadow_checks
        g10_detail["shadow_rows_checked"] = len(shadow_checks)

        def _shadow_acceptable(c: dict[str, Any]) -> bool:
            if not c.get("hashes_ok"):
                return False
            status = str(c.get("signature_status") or "missing")
            # Integrity failure only when hashes break or seal is corrupt.
            if status == "failed":
                return False
            # Live publication requires cryptographic verification.
            if _LIVE and status != "verified":
                return False
            # Research / live-locked: hashes + non-failed sig status
            # (missing / unverifiable disclosed, not greenwashed as verified).
            return status in {"verified", "missing", "unverifiable"}

        shadow_all_ok = bool(shadow_checks) and all(_shadow_acceptable(c) for c in shadow_checks)
        g10_detail["shadow_signature_note"] = (
            "signature_status=verified → Ed25519 ok against historical key_id; "
            "missing → hashes-only; unverifiable → hash ok but key does not verify "
            "(e.g. rotated trust root); failed → integrity/corrupt seal. "
            "PUBLIC_LIVE requires verified; research accepts missing/unverifiable with hashes."
        )
    except Exception as exc:  # noqa: BLE001
        g10_detail["shadow_verify_error"] = str(exc)
    g10_detail["note"] = (
        "G10: lite hash seal (verify-rebuild) + independent re-execution within MCSE "
        "tolerances (verify-rebuild --independent) + write-once shadow seals."
    )
    if rebuild_ok and independent_ok and shadow_all_ok:
        g10_ok = True
        g10_status = "pass"
    elif rebuild_ok and (independent_ok or shadow_all_ok):
        g10_ok = False
        g10_status = "partial"
        g10_detail["promotion_block"] = (
            "incomplete reproducibility evidence — research partial only"
        )
    else:
        g10_ok = False
        g10_status = "fail"
        g10_detail["promotion_block"] = "reproducibility seals incomplete or hash mismatch"
    # Release-identity hash seal (truth artifacts) — fail closed for promotion.
    try:
        from midterms.ops.release_identity import verify_release_identity

        identity = verify_release_identity()
        g10_detail["release_identity"] = identity
        if not identity.get("ok"):
            g10_ok = False
            g10_status = "fail"
            g10_detail["promotion_block"] = "release_identity hash mismatch"
    except Exception as exc:  # noqa: BLE001
        g10_detail["release_identity_error"] = str(exc)
        g10_ok = False
        g10_status = "fail"
    gates.append(
        _gate(
            "G10",
            name="Reproducibility",
            ok=g10_ok,
            status=g10_status,
            detail=g10_detail,
            evidence=[str(ROOT / "data" / "manifests" / "shadow_publications.jsonl")],
        )
    )

    # --- G11 Transparency ---
    g11_ok = model_card.exists()
    g11_notes = []
    g11_detail: dict[str, Any] = {
        "model_card": model_card.exists(),
        "model_version": MODEL_VERSION,
    }
    card_text = model_card.read_text(encoding="utf-8") if model_card.exists() else ""
    has_card_limitations = "## Limitations" in card_text or "## Known limitations" in card_text
    g11_detail["model_card_has_limitations_section"] = has_card_limitations
    if not has_card_limitations:
        g11_ok = False
        g11_notes.append("model card missing ## Limitations section (fresh audit R-12)")
    if forecast:
        g11_detail["run_class"] = forecast.get("run_class") or (forecast.get("diagnostics") or {}).get(
            "run_class"
        )
        g11_detail["publishable"] = forecast.get("publishable")
        g11_detail["publication_surface"] = forecast.get("publication_surface")
        g11_detail["has_limitations"] = bool(forecast.get("limitations"))
        g11_detail["forecast_model_version"] = forecast.get("model_version")
        fv = str(forecast.get("model_version") or "")
        if fv and fv != MODEL_VERSION:
            g11_ok = False
            g11_notes.append(
                f"stale forecast model_version={fv} != {MODEL_VERSION}"
            )
        if forecast.get("publication_surface") == "live" and not PUBLIC_LIVE_ENABLED:
            g11_ok = False
            g11_notes.append(
                "forecast publication_surface=live while PUBLIC_LIVE_ENABLED=False"
            )
        if not forecast.get("limitations"):
            g11_ok = False
            g11_notes.append("forecast artifact missing limitations block")
        if forecast.get("publishable") and not forecast.get("run_class"):
            g11_ok = False
            g11_notes.append("publishable claim without run_class")
        if forecast.get("publication_surface") == "live" and not forecast.get("limitations"):
            g11_ok = False
    if val_report and val_report.get("limitations"):
        g11_detail["validation_limitations_n"] = len(val_report["limitations"])
    if poll_cov:
        g11_detail["poll_coverage_present"] = True
    g11_status = "pass" if g11_ok else "fail"
    gates.append(
        _gate(
            "G11",
            name="Transparency",
            ok=g11_ok,
            status=g11_status,
            detail=g11_detail,
            evidence=[str(model_card)],
            notes=g11_notes
            or ["Model card limitations, run_class, and missingness required."],
        )
    )

    hard = [g for g in gates if g["status"] == "fail"]
    partial = [g for g in gates if g["status"] == "partial"]
    # While live is locked, G10 seal failures are promotion-blocking only
    # (research diagnostic may continue with ok=true and promotion_ok=false).
    research_hard = [
        g
        for g in hard
        if g["id"] != "G10" or PUBLIC_LIVE_ENABLED
    ]
    report = {
        "audit_item": "Milestone-0",
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive_scope": "2014-2024",
        "archive_note": MILESTONE_ARCHIVE_NOTE,
        "gates": {g["id"]: g for g in gates},
        "gate_list": gates,
        "ok": len(research_hard) == 0,
        "promotion_ok": len(hard) == 0
        and len(partial) == 0
        and (not PUBLIC_LIVE_ENABLED)
        and all(bool(g.get("ok")) for g in gates),
        "n_pass": sum(1 for g in gates if g["status"] == "pass"),
        "n_partial": len(partial),
        "n_fail": len(hard),
        "failures": [g["id"] for g in hard],
        "research_failures": [g["id"] for g in research_hard],
        "partials": [g["id"] for g in partial],
        "milestone": {
            "name": "first_publishable",
            "public_live_probabilities": (
                bool(PUBLIC_LIVE_ENABLED)
                and str((forecast or {}).get("publication_surface") or "") == "live"
                and not bool(((forecast or {}).get("public_release") or {}).get("superseded"))
            ),
            "current_publication_surface": (forecast or {}).get("publication_surface")
            or "research_only",
            "prior_public_publication_id": (
                ((forecast or {}).get("public_release") or {}).get("publication_id")
                if ((forecast or {}).get("public_release") or {}).get("superseded")
                else None
            ),
            "public_publication_id": (
                None
                if ((forecast or {}).get("public_release") or {}).get("superseded")
                else ((forecast or {}).get("public_release") or {}).get("publication_id")
            ),
            "requirements": [
                "frozen shadow with identified spine",
                "nested holdouts",
                "official 2014-2024 chamber archive",
                "no fixture inputs for publication claims",
                "G1-G11 acceptance report",
            ],
        },
        "exit_condition": (
            "Unified G1–G11 acceptance artifact retained; milestone shadow uses production spine."
        ),
    }

    try:
        from midterms.ops.run_coherence import write_coherence_report

        coherence = write_coherence_report(artifacts_dir=art_dir)
    except Exception as exc:  # noqa: BLE001
        coherence = {"ok": False, "error": str(exc)}
    report["run_coherence"] = coherence
    if coherence.get("ok") is False:
        # Coherence failure blocks promotion; research ok already computed above.
        report["promotion_ok"] = False
        report.setdefault("failures", [])
        if "COHERENCE" not in report["failures"]:
            report["failures"] = list(report["failures"]) + ["COHERENCE"]

    if write:
        art_dir.mkdir(parents=True, exist_ok=True)
        out = art_dir / "acceptance_gates_latest.json"
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        md = art_dir / "acceptance_gates_latest.md"
        md.write_text(_to_markdown(report), encoding="utf-8")
        report["path"] = str(out)
        report["md_path"] = str(md)
    return report


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Acceptance gates — {report.get('model_version')}",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Overall: **{'PASS' if report.get('ok') else 'FAIL'}** "
        f"({report.get('n_pass')} pass / {report.get('n_partial')} partial / {report.get('n_fail')} fail)",
        "",
        report.get("archive_note") or "",
        "",
        "| Gate | Status | Name |",
        "| --- | --- | --- |",
    ]
    for g in report.get("gate_list") or []:
        lines.append(f"| {g['id']} | {g['status']} | {g['name']} |")
    if report.get("failures"):
        lines.extend(["", "## Failures", "", ", ".join(report["failures"])])
    if report.get("partials"):
        lines.extend(["", "## Partials", "", ", ".join(report["partials"])])
    lines.extend(["", "## Notes", ""])
    if (report.get("milestone") or {}).get("public_live_probabilities"):
        lines.append(
            f"- Public live probabilities enabled"
            f" (`{(report.get('milestone') or {}).get('public_publication_id')}`)."
        )
    else:
        lines.append(
            "- Public-facing live probabilities remain out of scope until `publish-live`."
        )
    return "\n".join(lines) + "\n"
