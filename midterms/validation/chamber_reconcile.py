"""Official 100-seat chamber + ballot reconciliation (fresh audit R-01/R-02/R-03).

Expectations come from ``independent_chamber_expectations.json``. Observed races
and results come from the warehouse / official ledger. The two paths are
independent: changing a certified winner must break reconcile rather than
adjusting held seats.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR
from midterms.evidence.official_ledger import load_expectations
from midterms.simulate.chamber import vp_tiebreak_for_election_year

GATE_YEARS: tuple[int, ...] = (2014, 2016, 2018, 2020, 2022, 2024)


def reconcile_cycle(
    year: int,
    *,
    races: pd.DataFrame | None = None,
    results: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Fail closed unless warehouse matches independent expectations + vote counts."""
    election_id = f"senate-{year}"
    try:
        exp = (load_expectations().get("cycles") or {}).get(str(year))
    except FileNotFoundError as exc:
        return {"ok": False, "year": year, "error": str(exc)}
    if not exp:
        return {"ok": False, "year": year, "error": "no independent expectations"}

    if races is None or results is None:
        from midterms.evidence.warehouse import Warehouse

        wh = Warehouse(ensure_fixtures=False)
        races = races if races is not None else wh.races
        results = results if results is not None else wh.results

    rr = races[races["election_id"] == election_id].copy()
    if rr.empty:
        return {"ok": False, "year": year, "error": "no races for election_id"}

    expected_ids = set(exp.get("expected_race_ids") or [])
    contested = rr[~rr["not_up"]] if "not_up" in rr.columns else rr
    held = rr[rr["not_up"]] if "not_up" in rr.columns else rr.iloc[0:0]
    got_ids = set(contested["race_id"].astype(str))

    missing = sorted(expected_ids - got_ids)
    extra = sorted(got_ids - expected_ids)

    held_ind = int((held["held_by"] == "I").sum()) if len(held) else 0
    held_dem = int((held["held_by"] == "D").sum()) + held_ind if len(held) else 0
    held_rep = int((held["held_by"] == "R").sum()) if len(held) else 0
    n_contested = int(len(contested))
    total = held_dem + held_rep + n_contested

    res = results[results["election_id"] == election_id] if len(results) else results
    by_id = (
        res.drop_duplicates("race_id").set_index("race_id")
        if len(res) and "race_id" in res.columns
        else None
    )
    dem_wins = 0
    missing_results: list[str] = []
    zero_vote: list[str] = []
    for rid in sorted(expected_ids):
        if by_id is None or rid not in by_id.index:
            missing_results.append(rid)
            continue
        row = by_id.loc[rid]
        dem_v = float(row.get("dem_votes") or 0)
        rep_v = float(row.get("rep_votes") or 0)
        if dem_v <= 0 and rep_v <= 0:
            zero_vote.append(rid)
        margin = float(row["two_party_margin"])
        if margin >= 0:
            dem_wins += 1

    realized_dem = held_dem + dem_wins
    vp = exp.get("vp_tiebreak_party") or vp_tiebreak_for_election_year(year)
    if vp == "D":
        dem_control = realized_dem >= 50
    else:
        dem_control = realized_dem >= 51

    reasons: list[str] = []
    ok = True
    if missing:
        ok = False
        reasons.append(f"missing contested race_ids: {missing[:8]}")
    if extra:
        ok = False
        reasons.append(f"extra contested race_ids (not on official ballot): {extra[:8]}")
    if total != 100:
        ok = False
        reasons.append(
            f"seat accounting {total} != 100 (held_dem={held_dem}, held_rep={held_rep}, contested={n_contested})"
        )
    if held_dem != int(exp["held_dem"]) or held_rep != int(exp["held_rep"]):
        ok = False
        reasons.append(
            f"held caucus mismatch got D={held_dem}/R={held_rep} "
            f"expected D={exp['held_dem']}/R={exp['held_rep']}"
        )
    if n_contested != int(exp["n_contested_expected"]):
        ok = False
        reasons.append(
            f"contested count {n_contested} != expected {exp['n_contested_expected']}"
        )
    if missing_results:
        ok = False
        reasons.append(f"missing certified results for: {missing_results[:8]}")
    if zero_vote:
        ok = False
        reasons.append(f"zero vote counts for: {zero_vote[:8]}")
    if int(exp["post_dem_seats"]) != realized_dem:
        ok = False
        reasons.append(
            f"post-election Dem seats {realized_dem} != certified {exp['post_dem_seats']}"
        )
    if bool(exp["post_dem_control"]) != bool(dem_control):
        ok = False
        reasons.append(
            f"control mismatch got dem_control={dem_control} expected {exp['post_dem_control']}"
        )

    # Official canaries (audit R-03)
    canary_failures: list[str] = []
    for can in exp.get("canaries") or []:
        rid = str(can["race_id"])
        if by_id is None or rid not in by_id.index:
            canary_failures.append(f"{rid}: missing")
            continue
        row = by_id.loc[rid]
        dem_v = float(row.get("dem_votes") or 0)
        rep_v = float(row.get("rep_votes") or 0)
        margin = float(row["two_party_margin"])
        winner = str(row.get("winner_party") or ("D" if margin > 0 else "R"))
        if can.get("winner_party") and winner != can["winner_party"]:
            canary_failures.append(f"{rid}: winner {winner} != {can['winner_party']}")
        if can.get("dem_votes") is not None and int(dem_v) != int(can["dem_votes"]):
            canary_failures.append(f"{rid}: dem_votes {int(dem_v)} != {can['dem_votes']}")
        if can.get("rep_votes") is not None and int(rep_v) != int(can["rep_votes"]):
            canary_failures.append(f"{rid}: rep_votes {int(rep_v)} != {can['rep_votes']}")
        if can.get("min_margin_pp") is not None and margin < float(can["min_margin_pp"]):
            canary_failures.append(f"{rid}: margin {margin:.2f} < {can['min_margin_pp']}")
    if canary_failures:
        ok = False
        reasons.append("canary failures: " + "; ".join(canary_failures[:6]))

    # Required specials present
    for sid in exp.get("expected_special_ids") or []:
        if sid not in got_ids:
            ok = False
            reasons.append(f"missing required special/unexpired: {sid}")

    return {
        "ok": ok,
        "year": year,
        "election_id": election_id,
        "n_contested_expected": int(exp["n_contested_expected"]),
        "n_contested_got": n_contested,
        "missing_race_ids": missing,
        "extra_race_ids": extra,
        "held_dem": held_dem,
        "held_rep": held_rep,
        "held_ind": held_ind,
        "dem_wins_certified": dem_wins,
        "realized_dem_seats": realized_dem,
        "expected_dem_seats": exp["post_dem_seats"],
        "dem_control": dem_control,
        "expected_dem_control": exp["post_dem_control"],
        "vp_tiebreak_party": vp,
        "canary_failures": canary_failures,
        "reasons": reasons,
        "expectations_source": "independent_chamber_expectations.json",
        "independence_note": (
            "Expected IDs/held/post from independent expectations; observed from warehouse. "
            "Held seats are not solved from winners."
        ),
    }


def reconcile_all_cycles(years: tuple[int, ...] | None = None) -> dict[str, Any]:
    years = years or GATE_YEARS
    by = {str(y): reconcile_cycle(y) for y in years}
    ok = all(v.get("ok") for v in by.values())
    summary = {
        "ok": ok,
        "gate_years": list(GATE_YEARS),
        "cycles": list(by.keys()),
        "by_cycle": by,
        "failures": [y for y, v in by.items() if not v.get("ok")],
        "note": (
            "Gate years 2014–2024 against independent expectations + vote-count ledger "
            "(fresh audit R-01/R-02/R-03)."
        ),
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "chamber_reconcile_latest.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    summary["path"] = str(path)
    return summary
