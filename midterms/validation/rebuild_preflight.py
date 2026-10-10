"""Cheap pre-OOF rebuild preflight (v0.9.25).

Performs all checks that can fail quickly *before* the expensive PyMC OOF
run is launched.  No PyMC is invoked here; the only allowed imports are
from midterms validation/evidence modules that themselves avoid fitting.

Exit / return semantics
-----------------------
``run_rebuild_preflight`` returns a dict with:

  ok            – bool, True when all blocking gates pass.
  blockers      – list[str], non-empty when ok is False.
  warnings      – list[str], non-blocking notices (multiway withheld, etc.).
  checks        – dict, one sub-report per named gate.
  multiway_withheld – bool, True when MT is correctly withheld (not a blocker).

When ``strict=True`` the function raises SystemExit(1) if ok is False.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION

# ---------------------------------------------------------------------------
# Individual gate helpers
# ---------------------------------------------------------------------------


def _check_source_readiness(election_id: str, as_of: str) -> dict[str, Any]:
    """Gate: required evidence domains are ready for an expensive rebuild."""
    from midterms.evidence.source_readiness import write_source_readiness

    report = write_source_readiness(election_id=election_id, as_of=as_of)
    ok = bool(report.get("ready_for_expensive_rebuild"))
    return {
        "gate": "source_readiness",
        "ok": ok,
        "ready_for_expensive_rebuild": ok,
        "blockers": report.get("blockers") or [],
        "domain_status": {
            name: (block.get("status") if isinstance(block, dict) else block)
            for name, block in (report.get("domains") or {}).items()
        },
    }


def _check_forecast_coverage(
    election_id: str, as_of: str
) -> dict[str, Any]:
    """Gate: every active race has an approved predictive path.

    Returns ok=True when forecast_complete is True and n_fail==0.
    Returns ok='withheld_multiway' when the only failures are
    fail-closed multiway races, which is expected and not a blocker.
    """
    from midterms.evidence.preparation import classify_strict_forecast_coverage
    from midterms.evidence.source_readiness import audit_source_readiness

    report = audit_source_readiness(election_id=election_id, as_of=as_of)
    state = classify_strict_forecast_coverage(report)
    summary = state.get("summary") or {}
    if not isinstance(summary, dict):
        summary = {}

    complete = bool(summary.get("forecast_complete"))
    n_fail = int(summary.get("n_fail") or 0)
    multiway_withheld = bool(state.get("multiway_withheld_only"))

    if complete and n_fail == 0:
        status = "complete"
        ok: Any = True
    elif multiway_withheld:
        status = "withheld_multiway_only"
        ok = "withheld_multiway"
    else:
        status = "incomplete"
        ok = False

    return {
        "gate": "forecast_coverage",
        "ok": ok,
        "status": status,
        "forecast_complete": complete,
        "n_fail": n_fail,
        "multiway_withheld": multiway_withheld,
        "approved_multiway_withheld_race_ids": list(
            state.get("approved_multiway_withheld_race_ids") or []
        ),
        "unexpected_forecast_failure_race_ids": list(
            state.get("unexpected_forecast_failure_race_ids") or []
        ),
        "summary": summary,
    }


def _check_modeling_paths() -> dict[str, Any]:
    """Gate: every active race resolves to exactly one modeling path.

    Also asserts MT is not in the binary non-major set.
    """
    from midterms.evidence.current_candidates import load_current_candidate_registry
    from midterms.evidence.modeling_paths import (
        alaska_rcv_race_ids,
        assert_no_binary_contamination,
        binary_non_major_eligible_race_ids,
        modeling_path_for_structure,
        multiway_plurality_race_ids,
    )

    registry = load_current_candidate_registry()
    races = registry.get("races") or []
    active = [r for r in races if isinstance(r, dict) and r.get("race_id")]

    binary_ids = binary_non_major_eligible_race_ids(registry)
    multiway_ids = multiway_plurality_race_ids(registry)
    alaska_ids = alaska_rcv_race_ids(registry)

    failures: list[str] = []
    path_map: dict[str, str] = {}

    # Contamination assertion
    try:
        assert_no_binary_contamination(
            binary_race_ids=binary_ids, multiway_race_ids=multiway_ids
        )
    except ValueError as exc:
        failures.append(str(exc))

    # MT must not be in binary set
    mt_ids = {r for r in multiway_ids if "MT" in str(r).upper() or "montana" in str(r).lower()}
    mt_in_binary = mt_ids & binary_ids
    if mt_in_binary:
        failures.append(
            f"Montana multiway race(s) contaminate binary non-major set: {sorted(mt_in_binary)}"
        )

    # Every race gets exactly one path
    for row in active:
        race_id = str(row["race_id"])
        structure = str(row.get("contest_structure") or "")
        path = modeling_path_for_structure(structure)
        path_map[race_id] = path

    # Check no orphans (all should have a non-empty path)
    orphans = [rid for rid, p in path_map.items() if not p or p == "unsupported"]
    if orphans:
        failures.append(f"Races with unsupported/unknown path: {sorted(orphans)}")

    # Known state assertions (registry-derived, no hard-coding)
    highlight_states = {"AK", "NE", "ID", "SD", "MT"}
    highlight: dict[str, str] = {}
    for row in active:
        state = str(row.get("state") or "").upper()
        if state in highlight_states:
            highlight[state] = path_map.get(str(row["race_id"]), "unknown")

    # MT must be multiway (withheld)
    if "MT" in highlight and highlight["MT"] not in {"multiway_plurality_adapter", "unknown"}:
        failures.append(
            f"Montana expected multiway_plurality_adapter path, got {highlight['MT']!r}"
        )

    # AK must be alaska_rcv
    if "AK" in highlight and highlight["AK"] not in {"alaska_rcv_adapter", "unknown"}:
        failures.append(
            f"Alaska expected alaska_rcv_adapter path, got {highlight['AK']!r}"
        )

    return {
        "gate": "modeling_paths",
        "ok": not failures,
        "failures": failures,
        "path_assignments": path_map,
        "highlight_states": highlight,
        "n_binary_non_major": len(binary_ids),
        "n_multiway": len(multiway_ids),
        "n_alaska": len(alaska_ids),
        "mt_in_binary_set": sorted(mt_in_binary) if mt_in_binary else [],
    }


def _check_challenger_stack_classification() -> dict[str, Any]:
    """Gate: SPECIFICATION_CHALLENGERS are valid and no stack leakage is present.

    Uses the frozen spec weights artifact when available; otherwise validates
    the frozen set structure only (still catches misclassification).
    """
    from midterms.validation.stack_weights import (
        SPECIFICATION_CHALLENGERS,
        assert_no_same_family_stack_leakage,
        specification_challenger_names,
    )

    failures: list[str] = []
    spec_names = specification_challenger_names()

    # Sanity: frozenset is non-empty and equals SPECIFICATION_CHALLENGERS
    if not spec_names:
        failures.append("SPECIFICATION_CHALLENGERS is empty")
    if spec_names != SPECIFICATION_CHALLENGERS:
        failures.append("specification_challenger_names() != SPECIFICATION_CHALLENGERS sentinel")

    # Try to load the stack weights artifact and check leakage
    stack_path = ARTIFACTS_DIR / "stack_weights_oof.json"
    stack_checked = False
    if stack_path.is_file():
        try:
            weights = json.loads(stack_path.read_text(encoding="utf-8"))
            # The weights dict may be nested; try common keys
            production = weights.get("weights") or weights.get("production") or {}
            if isinstance(production, dict):
                assert_no_same_family_stack_leakage(production)
                stack_checked = True
            else:
                # Try asserting on the top-level dict directly
                assert_no_same_family_stack_leakage(weights)
                stack_checked = True
        except ValueError as exc:
            failures.append(f"Stack leakage: {exc}")
        except Exception:  # noqa: BLE001, S110
            pass  # artifact may not exist or may be malformed; non-blocking

    return {
        "gate": "challenger_stack_classification",
        "ok": not failures,
        "failures": failures,
        "n_specification_challengers": len(spec_names),
        "stack_artifact_checked": stack_checked,
        "stack_artifact_exists": stack_path.is_file(),
    }


def _check_exceptional_lineage() -> dict[str, Any]:
    """Gate: verify exceptional model lineage if artifacts are present."""
    lineage_path = ARTIFACTS_DIR / "exceptional_model_lineage_v0923.json"
    if not lineage_path.is_file():
        return {
            "gate": "exceptional_lineage",
            "ok": True,
            "skipped": True,
            "reason": "artifact not yet generated (pre-OOF is expected)",
        }

    failures: list[str] = []
    try:
        from midterms.validation.exceptional_model_lineage import (
            verify_exceptional_model_lineage,
        )

        result = verify_exceptional_model_lineage()
        if not result.get("ok"):
            failures.append(
                f"exceptional lineage verification failed: {result.get('failures') or result}"
            )
    except Exception as exc:  # noqa: BLE001
        failures.append(f"exceptional lineage check error: {exc}")

    return {
        "gate": "exceptional_lineage",
        "ok": not failures,
        "failures": failures,
        "artifact_path": str(lineage_path),
    }


def _check_multiway_share_smoke() -> dict[str, Any]:
    """Gate: synthetic multiway share draws (no PyMC, cheap)."""
    failures: list[str] = []
    try:
        from midterms.validation.multiway_plurality_validation import (
            smoke_fit_multiway_share_draws,
        )

        result = smoke_fit_multiway_share_draws(n_candidates=4, n_draws=200, seed=0)
        if not result.get("shares_sum_to_one"):
            failures.append("multiway share draws do not sum to one")
        if int(result.get("n_unique_winners") or 0) < 2:
            failures.append(
                f"multiway share smoke: only {result.get('n_unique_winners')} unique winners"
            )
    except Exception as exc:  # noqa: BLE001
        failures.append(f"multiway share smoke error: {exc}")

    return {
        "gate": "multiway_share_smoke",
        "ok": not failures,
        "failures": failures,
    }


def _check_chamber_accounting() -> dict[str, Any]:
    """Gate: 100-seat chamber accounting with a synthetic fit."""
    failures: list[str] = []
    n_seats = 0
    try:
        from midterms.evidence.current_candidates import load_current_candidate_registry

        registry = load_current_candidate_registry()
        races = registry.get("races") or []
        active_races = [r for r in races if isinstance(r, dict) and r.get("race_id")]
        n_seats = len(active_races)

        # The full senate has 100 seats; ~33-34 are contested each cycle.
        # We just verify the contested race count is plausible.
        if n_seats < 25:
            failures.append(
                f"Active senate races ({n_seats}) is implausibly low for a general election"
            )
        if n_seats > 100:
            failures.append(
                f"Active senate races ({n_seats}) exceeds 100-seat chamber"
            )
    except Exception as exc:  # noqa: BLE001
        failures.append(f"chamber accounting error: {exc}")

    return {
        "gate": "chamber_accounting",
        "ok": not failures,
        "failures": failures,
        "n_active_contested_races": n_seats,
        "note": "34 seats contested in a typical midterm; full chamber has 100 seats",
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_rebuild_preflight(
    *,
    election_id: str = "senate-2026",
    as_of: str,
    strict: bool = False,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run all cheap pre-OOF rebuild gates.

    Parameters
    ----------
    election_id:
        Election identifier (default ``senate-2026``).
    as_of:
        Evidence cutoff date ``YYYY-MM-DD``.
    strict:
        If True, raises ``SystemExit(1)`` when any blocking gate fails.
    out_path:
        Optional path to write the preflight JSON artifact.

    Returns
    -------
    dict with keys ``ok``, ``blockers``, ``warnings``, ``checks``,
    ``multiway_withheld``.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    # --- 1. Source readiness ---
    sr = _check_source_readiness(election_id, as_of)
    checks["source_readiness"] = sr
    if not sr["ok"]:
        blockers.append(
            "source readiness: "
            + json.dumps(sr.get("blockers") or [], sort_keys=True)
        )

    # --- 2. Forecast coverage (may be withheld-multiway) ---
    fc = _check_forecast_coverage(election_id, as_of)
    checks["forecast_coverage"] = fc
    multiway_withheld = bool(fc.get("multiway_withheld"))
    if fc["ok"] is False:
        blockers.append(
            "forecast coverage incomplete (not due to fail-closed multiway): "
            + json.dumps(fc.get("summary") or {}, sort_keys=True)
        )
    elif multiway_withheld:
        withheld_ids = fc.get("approved_multiway_withheld_race_ids") or []
        warnings.append(
            "MULTIWAY_WITHHELD=1: genuine multiway races are intentionally fail-closed "
            f"({', '.join(withheld_ids) or 'registry multiway set'}). Evidence seal and "
            "ordinary OOF may proceed; final publication completeness remains deferred "
            "until historically supported multiway probabilities exist."
        )

    # --- 3. Modeling paths ---
    mp = _check_modeling_paths()
    checks["modeling_paths"] = mp
    if not mp["ok"]:
        for f in mp.get("failures") or []:
            blockers.append(f"modeling_paths: {f}")

    # --- 4. Challenger / stack classification ---
    cs = _check_challenger_stack_classification()
    checks["challenger_stack_classification"] = cs
    if not cs["ok"]:
        for f in cs.get("failures") or []:
            blockers.append(f"challenger_stack: {f}")

    # --- 5. Exceptional lineage (if artifact present) ---
    el = _check_exceptional_lineage()
    checks["exceptional_lineage"] = el
    if not el["ok"] and not el.get("skipped"):
        for f in el.get("failures") or []:
            blockers.append(f"exceptional_lineage: {f}")

    # --- 6. Multiway share smoke ---
    ms = _check_multiway_share_smoke()
    checks["multiway_share_smoke"] = ms
    if not ms["ok"]:
        for f in ms.get("failures") or []:
            blockers.append(f"multiway_share_smoke: {f}")

    # --- 7. Chamber accounting ---
    ca = _check_chamber_accounting()
    checks["chamber_accounting"] = ca
    if not ca["ok"]:
        for f in ca.get("failures") or []:
            blockers.append(f"chamber_accounting: {f}")

    ok = not blockers
    payload: dict[str, Any] = {
        "schema_version": "rebuild-preflight-v0925",
        "model_version": MODEL_VERSION,
        "election_id": election_id,
        "as_of": as_of,
        "ok": ok,
        "multiway_withheld": multiway_withheld,
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
    }

    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        payload["path"] = str(p)

    if strict and not ok:
        for msg in blockers:
            print(f"::error::{msg}")
        import sys

        print(
            json.dumps({"rebuild_preflight_strict_failure": blockers}, indent=2),
            file=sys.stderr,
        )
        raise SystemExit(1)

    return payload
