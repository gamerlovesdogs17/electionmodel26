"""Leave-pollster-out diagnostics (blueprint §4.3 / Gate 3)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

from midterms.baselines.score import score_forecasts
from midterms.config import ARTIFACTS_DIR, PRIMARY_HOLDOUT
from midterms.evidence.warehouse import Warehouse
from midterms.model.pymc_model import fit_fast_approximation
from midterms.validation.cycle_replay import _forecasts_from_fit


def leave_pollster_out(
    *,
    year: int = PRIMARY_HOLDOUT,
    lead_days: int = 60,
    draws: int = 400,
    top_n: int = 5,
) -> dict[str, Any]:
    """
    Drop each of the top-N prolific pollsters in turn; score CRPS/Brier vs full fit.

    Uses the fast hierarchical approximation for speed; production PyMC should be
    checked separately when compute allows. Results land in artifacts for the
    validation report.
    """
    wh = Warehouse(ensure_fixtures=False)
    election_id = f"senate-{year}"
    races = wh.races[wh.races["election_id"] == election_id]
    if races.empty:
        return {"ok": False, "error": "no races", "year": year}
    ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
    as_of = ed - timedelta(days=lead_days)
    snap = wh.build_as_of(as_of, election_id)
    results = wh.results[wh.results["election_id"] == election_id]
    polls = snap.polls
    if polls.empty or "pollster_id" not in polls.columns:
        return {"ok": False, "error": "no polls", "year": year}

    counts = polls["pollster_id"].value_counts()
    targets = [str(p) for p in counts.head(top_n).index.tolist()]

    base = fit_fast_approximation(snap, n_draws=draws, seed=year + lead_days)
    base_scores = score_forecasts(_forecasts_from_fit(base), results)

    drops: list[dict[str, Any]] = []
    for pid in targets:
        reduced = snap.polls[snap.polls["pollster_id"].astype(str) != pid].copy()
        # Shallow clone snapshot with reduced polls
        from dataclasses import replace

        try:
            slim = replace(snap, polls=reduced)
        except TypeError:
            slim = snap
            slim.polls = reduced  # type: ignore[misc]
        try:
            fit = fit_fast_approximation(slim, n_draws=draws, seed=year + hash(pid) % 10_000)
            scores = score_forecasts(_forecasts_from_fit(fit), results)
        except Exception as exc:  # noqa: BLE001
            drops.append({"pollster_id": pid, "n_polls": int(counts[pid]), "error": str(exc)})
            continue
        drops.append(
            {
                "pollster_id": pid,
                "n_polls": int(counts[pid]),
                "crps": scores.get("crps"),
                "brier": scores.get("brier"),
                "delta_crps": float(scores.get("crps", np.nan) - base_scores.get("crps", np.nan))
                if scores.get("crps") is not None and base_scores.get("crps") is not None
                else None,
                "delta_brier": float(scores.get("brier", np.nan) - base_scores.get("brier", np.nan))
                if scores.get("brier") is not None and base_scores.get("brier") is not None
                else None,
            }
        )

    deltas = [d["delta_crps"] for d in drops if d.get("delta_crps") is not None]
    report = {
        "ok": True,
        "year": year,
        "lead_days": lead_days,
        "method": "fast_hierarchical_t",
        "baseline": base_scores,
        "drops": drops,
        "mean_abs_delta_crps": float(np.mean(np.abs(deltas))) if deltas else None,
        "note": (
            "Leave-pollster-out: large positive delta_crps means that pollster was "
            "important; stable scores imply robustness to single-house dominance."
        ),
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "leave_pollster_out_latest.json"
    import json

    path.write_text(json.dumps(report, indent=2, default=str))
    report["path"] = str(path)
    return report
