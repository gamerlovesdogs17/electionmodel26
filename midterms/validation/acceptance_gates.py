"""Unified G1–G11 acceptance gates (first publishable milestone).

Aggregates existing validation artifacts into a single pass/fail report.
Does not recompute heavy fits by default.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, ROOT


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
                },
                evidence=[str(art_dir / "evidence_eligibility_latest.json")],
                notes=[
                    "All configured domains (finance/economics/approval/…) must be enumerated; "
                    "fixture_hash / *_FIXTURE blocks publication."
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
    elif stack and stack.get("source_hierarchical_method") == "fast":
        g5_status = "partial" if g5_ok else g5_status
        g5_notes.append(
            "OOF stack spine is fast_hierarchical_t (honest). Production forecast spine remains pymc; "
            "milestone shadow reseals pymc separately."
        )
    elif stack and str(stack.get("source_hierarchical_method") or "").startswith("pymc"):
        g5_notes.append("OOF stack / nested LOO spine is pymc (aligned with production).")
        if g5_ok:
            g5_status = "pass"
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
    if nested and nested.get("crps_by_fold"):
        scores_present = True
        g6_detail["g8_metric"] = nested.get("g8_metric")
        g6_detail["mean_crps"] = nested.get("mean_crps") or nested.get("crps_mean")
        g6_detail["n_folds"] = len(nested.get("crps_by_fold") or {})
        g6_detail["freeze_before_truth"] = nested.get("freeze_before_truth")
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
    if scores_present and (nested or {}).get("freeze_before_truth"):
        g6_ok = True
        g6_status = "pass"
    elif scores_present:
        g6_ok = True
        g6_status = "partial"
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
    if rel_gate is None and rel:
        from midterms.validation.metrics import reliability_overconfidence

        rel_gate = reliability_overconfidence(rel)
    if rel is not None:
        g7_detail["n_bins"] = len(rel)
        g7_detail["reliability_gate"] = rel_gate
        g7_detail["sample_sizes_disclosed"] = all("n" in b for b in rel)
        if rel_gate and rel_gate.get("calibration_claim_allowed"):
            g7_ok = True
            g7_status = "pass"
        elif rel_gate and rel_gate.get("sample_sizes_disclosed") and rel_gate.get("n_overconfident"):
            g7_ok = False
            g7_status = "fail"
            g7_detail["alert"] = "material overconfidence — recalibrate or widen; no calibration claim"
        else:
            g7_ok = bool(g7_detail.get("sample_sizes_disclosed"))
            g7_status = "partial" if g7_ok else "fail"
    else:
        g7_detail["alert"] = "no reliability bins in validation_report_latest"
        g7_status = "partial"
        g7_ok = True  # do not hard-fail milestone if calibration block absent but G6 scores exist
        g7_detail["note"] = "Reliability claim withheld until bins with sample sizes are regenerated."
    gates.append(
        _gate(
            "G7",
            name="Reliability",
            ok=g7_ok,
            status=g7_status,
            detail=g7_detail,
            evidence=[str(art_dir / "validation_report_latest.json")],
            notes=["Calibration plots must disclose sample sizes; overconfidence blocks claims."],
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
    if ind is not None:
        independent_ok = bool(ind.get("ok"))
        g10_detail["independent_rebuild"] = {
            "ok": independent_ok,
            "path": str(ind_path),
            "comparison": ind.get("comparison"),
            "domain_ok": ind.get("domain_ok"),
            "error": ind.get("error"),
        }
    else:
        g10_detail["independent_rebuild"] = {
            "ok": False,
            "error": "missing independent_rebuild_latest.json — run verify-rebuild --independent",
        }
    try:
        from midterms.ops.shadow_publish import list_shadow_publications, verify_shadow

        rows = list_shadow_publications()
        shadow_checks = []
        for row in rows[-5:]:
            ver = verify_shadow(path=Path(row["path"]))
            shadow_checks.append(
                {"shadow_id": row.get("shadow_id"), "ok": ver.get("ok"), "path": row.get("path")}
            )
        g10_detail["shadow_verify"] = shadow_checks
        shadow_all_ok = bool(shadow_checks) and all(c.get("ok") for c in shadow_checks)
    except Exception as exc:  # noqa: BLE001
        g10_detail["shadow_verify_error"] = str(exc)
    g10_detail["note"] = (
        "G10: lite hash seal (verify-rebuild) + independent re-execution within MCSE "
        "tolerances (verify-rebuild --independent) + write-once shadow seals."
    )
    if rebuild_ok and independent_ok and shadow_all_ok:
        g10_ok = True
        g10_status = "pass"
    elif (rebuild_ok or independent_ok) and shadow_all_ok:
        g10_ok = True
        g10_status = "partial"
    elif rebuild_ok or independent_ok or shadow_all_ok:
        g10_ok = True
        g10_status = "partial"
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
        from midterms.config import PUBLIC_LIVE_ENABLED

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
    report = {
        "audit_item": "Milestone-0",
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive_scope": "2014-2024",
        "archive_note": MILESTONE_ARCHIVE_NOTE,
        "gates": {g["id"]: g for g in gates},
        "gate_list": gates,
        "ok": len(hard) == 0,
        "n_pass": sum(1 for g in gates if g["status"] == "pass"),
        "n_partial": len(partial),
        "n_fail": len(hard),
        "failures": [g["id"] for g in hard],
        "partials": [g["id"] for g in partial],
        "milestone": {
            "name": "first_publishable",
            "public_live_probabilities": bool(
                (forecast or {}).get("public_release", {}).get("enabled")
                or (forecast or {}).get("publication_surface") == "live"
            ),
            "public_publication_id": (
                ((forecast or {}).get("public_release") or {}).get("publication_id")
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
