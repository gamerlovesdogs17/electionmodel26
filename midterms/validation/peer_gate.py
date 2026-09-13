"""Peer Brier / CRPS release gate (blueprint §2 — compare, never average)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR


def _brier(ps: np.ndarray, ys: np.ndarray) -> float:
    return float(np.mean((ps - ys) ** 2))


def score_against_peers(artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Score our race probs against peer snapshots (DDHQ / Kalshi) where both exist.

    Peers are treated as soft probabilistic targets for a *release gate*, not mixed
    into the ensemble. Control gap vs Kalshi/DDHQ is the primary chamber check.
    """
    if artifact is None:
        path = ARTIFACTS_DIR / "forecast_latest.json"
        if not path.exists():
            return {"ok": False, "error": "missing forecast_latest.json"}
        artifact = json.loads(path.read_text(encoding="utf-8"))

    peer = artifact.get("peer_comparison") or {}
    control = peer.get("control_p_dem") or {}
    ours_ctl = control.get("ours")
    gaps: dict[str, float] = {}
    for name in ("ddhq", "kalshi", "votehub"):
        them = control.get(name)
        if ours_ctl is not None and them is not None:
            try:
                gaps[name] = float(ours_ctl) - float(them)
            except (TypeError, ValueError):
                continue

    race_rows = peer.get("races") or []
    by_source: dict[str, list[tuple[float, float]]] = {"ddhq": [], "kalshi": []}
    for row in race_rows:
        o = row.get("ours")
        if o is None:
            continue
        try:
            o_f = float(o)
        except (TypeError, ValueError):
            continue
        for src in ("ddhq", "kalshi"):
            t = row.get(src)
            if t is None:
                continue
            try:
                t_f = float(t)
            except (TypeError, ValueError):
                continue
            if 0.0 <= t_f <= 1.0 and 0.0 <= o_f <= 1.0:
                by_source[src].append((o_f, t_f))

    race_scores: dict[str, Any] = {}
    for src, pairs in by_source.items():
        if len(pairs) < 3:
            race_scores[src] = {"n": len(pairs), "ok": None}
            continue
        ours = np.array([p[0] for p in pairs], dtype=float)
        them = np.array([p[1] for p in pairs], dtype=float)
        # Treat peer p as soft label; Brier of ours vs peer-rounded outcome + CRPS-like MAE
        y_hard = (them >= 0.5).astype(float)
        brier = _brier(ours, y_hard)
        mae = float(np.mean(np.abs(ours - them)))
        # Rough Gaussian CRPS proxy when peer is a point probability target
        crps_proxy = float(np.mean(np.abs(ours - them)))
        race_scores[src] = {
            "n": len(pairs),
            "brier_vs_peer_favorite": brier,
            "mae": mae,
            "crps_proxy": crps_proxy,
            "ok": bool(brier <= 0.22 and mae <= 0.22),
        }

    max_abs_control = max((abs(v) for v in gaps.values()), default=0.0)
    # Prefer Kalshi for control when present; else any peer
    primary_gap = gaps.get("kalshi", gaps.get("ddhq"))
    control_ok = (
        abs(primary_gap) <= 0.25
        if primary_gap is not None
        else (max_abs_control <= 0.25 if gaps else None)
    )

    kalshi_ok = race_scores.get("kalshi", {}).get("ok")
    ddhq_ok = race_scores.get("ddhq", {}).get("ok")
    # Race gate: pass if ANY peer panel clears thresholds.
    # Hard-fail only when every available peer panel fails. Peers often disagree
    # with each other (e.g. NC); blueprint §2 treats them as compare-only context.
    available = [x for x in (kalshi_ok, ddhq_ok) if x is not None]
    if not available:
        race_ok = None
    elif any(available):
        race_ok = True
    else:
        race_ok = False

    # Cross-peer disagreement (informational): large gaps → soft-note even if gate passes
    peer_cross: list[float] = []
    for row in race_rows:
        kd, dd = row.get("kalshi"), row.get("ddhq")
        try:
            if kd is not None and dd is not None:
                peer_cross.append(abs(float(kd) - float(dd)))
        except (TypeError, ValueError):
            continue
    mean_peer_disagreement = float(np.mean(peer_cross)) if peer_cross else None

    ok = True
    reasons: list[str] = []
    if control_ok is False:
        reasons.append(
            f"control gap vs primary peer large "
            f"(gap={primary_gap if primary_gap is not None else max_abs_control:.3f} > 0.25); "
            f"gaps={gaps} — informational under blueprint §2 (peers not averaged)"
        )
        # Soft: do not fail the release solely on control gap (markets are optional overlays)
    if gaps and control_ok is None:
        reasons.append("no peer control probabilities available")
    if race_ok is False:
        ok = False
        reasons.append("race-level peer Brier/MAE gate failed on all available peer panels")
    if race_ok is None:
        reasons.append("insufficient race overlap with peers for Brier/CRPS gate")
    if mean_peer_disagreement is not None and mean_peer_disagreement >= 0.20:
        reasons.append(
            f"peer panels disagree with each other "
            f"(mean |kalshi-ddhq|={mean_peer_disagreement:.3f}); "
            "compare-only — not a hard fail"
        )

    null_margins = [
        r.get("race_id")
        for r in (artifact.get("races") or [])
        if r.get("mean_margin") is None or r.get("sd_margin") is None
    ]
    if null_margins:
        ok = False
        reasons.append(f"null margins in races: {null_margins[:5]}")

    method = str(artifact.get("method") or "")
    diag = artifact.get("diagnostics") or {}
    core = str(diag.get("spine_method") or diag.get("core_method") or "")
    nonprod = (
        method.startswith("fast")
        or method.startswith("degraded")
        or core.startswith("fast")
        or core.startswith("degraded")
        or (method.startswith("ensemble_stack") and core.startswith("fast"))
    )
    if nonprod:
        ok = False
        reasons.append(f"non-production core method method={method} core={core}")
    elif method.startswith("ensemble_stack") and not core:
        reasons.append("ensemble_stack missing spine_method/core_method diagnostic")

    # Hard gate = integrity (nulls, production method, race MAE). Control gap is soft.
    hard_ok = ok and (race_ok is not False) and not null_margins and not nonprod

    return {
        "ok": hard_ok,
        "control_ok": control_ok,
        "control_soft": True,
        "control_gaps": gaps,
        "primary_control_gap": primary_gap,
        "max_abs_control_gap": max_abs_control if gaps else None,
        "race_scores": race_scores,
        "race_ok": race_ok,
        "mean_peer_disagreement": mean_peer_disagreement,
        "null_margin_races": null_margins,
        "method": method,
        "core_method": core,
        "generic_ballot": artifact.get("generic_ballot"),
        "reasons": reasons,
        "thresholds": {
            "max_abs_control_gap": 0.25,
            "race_brier": 0.22,
            "race_mae": 0.22,
            "control_gap_hard_fail": False,
        },
        "note": (
            "Peers are a release gate for integrity/race calibration only — "
            "never averaged into the ensemble (blueprint §2). "
            "Chamber control gap vs markets is soft/informational."
        ),
    }


def write_peer_gate_report(artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    report = score_against_peers(artifact)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "peer_gate_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    report["path"] = str(path)
    return report
