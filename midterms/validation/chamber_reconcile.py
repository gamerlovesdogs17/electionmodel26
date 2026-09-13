"""Official 100-seat chamber + ballot reconciliation (audit P0.2)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import ARTIFACTS_DIR
from midterms.evidence.official_ballot import CYCLE_META, contested_contests
from midterms.simulate.chamber import vp_tiebreak_for_election_year

# Production gate years (full certified margins + official ballots).
GATE_YEARS: tuple[int, ...] = (2014, 2016, 2018, 2020, 2022, 2024)


def reconcile_cycle(
    year: int,
    *,
    races: pd.DataFrame | None = None,
    results: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Fail closed if the packaged race universe / results cannot reproduce the
    official ballot and certified chamber outcome.
    """
    election_id = f"senate-{year}"
    meta = CYCLE_META.get(year)
    if meta is None:
        return {"ok": False, "year": year, "error": "no CYCLE_META"}

    if races is None or results is None:
        from midterms.evidence.warehouse import Warehouse

        wh = Warehouse(ensure_fixtures=False)
        races = races if races is not None else wh.races
        results = results if results is not None else wh.results

    rr = races[races["election_id"] == election_id].copy()
    if rr.empty:
        return {"ok": False, "year": year, "error": "no races for election_id"}

    contests = contested_contests(year)
    expected_ids = {c["race_id"] for c in contests}
    expected_states_regular = {
        c["state"] for c in contests if c.get("kind") == "regular"
    }
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
    for rid in sorted(expected_ids):
        if by_id is None or rid not in by_id.index:
            # Fall back to state match for single-seat states
            st = rid.rsplit("-", 1)[-1]
            if st == "special" or "special" in rid:
                missing_results.append(rid)
                continue
            hit = res[res["state"].astype(str) == st] if len(res) else res
            if len(hit) == 0:
                missing_results.append(rid)
                continue
            margin = float(hit.iloc[0]["two_party_margin"])
        else:
            margin = float(by_id.loc[rid, "two_party_margin"])
        if margin >= 0:
            dem_wins += 1

    realized_dem = held_dem + dem_wins
    vp = meta.get("vp_tiebreak_party") or vp_tiebreak_for_election_year(year)
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
    if held_dem != int(meta["held_dem"]) or held_rep != int(meta["held_rep"]):
        ok = False
        reasons.append(
            f"held caucus mismatch got D={held_dem}/R={held_rep} "
            f"expected D={meta['held_dem']}/R={meta['held_rep']}"
        )
    if missing_results:
        ok = False
        reasons.append(f"missing certified results for: {missing_results[:8]}")
    if int(meta["post_dem_seats"]) != realized_dem:
        ok = False
        reasons.append(
            f"post-election Dem seats {realized_dem} != certified {meta['post_dem_seats']}"
        )
    if bool(meta["post_dem_control"]) != bool(dem_control):
        ok = False
        reasons.append(
            f"control mismatch got dem_control={dem_control} expected {meta['post_dem_control']}"
        )

    # Wrong-class canary (2022 must not look like Class II)
    if year == 2022 and "PA" not in expected_states_regular:
        ok = False
        reasons.append("internal: 2022 expected_states missing PA")
    if year == 2022 and "DE" in expected_states_regular and "PA" not in set(contested["state"]):
        ok = False
        reasons.append("2022 contested set still looks Class-II-like (DE without PA)")

    report = {
        "ok": ok,
        "year": year,
        "election_id": election_id,
        "n_contested_expected": len(expected_ids),
        "n_contested_got": n_contested,
        "missing_race_ids": missing,
        "extra_race_ids": extra,
        "held_dem": held_dem,
        "held_rep": held_rep,
        "held_ind": held_ind,
        "dem_wins_certified": dem_wins,
        "realized_dem_seats": realized_dem,
        "expected_dem_seats": meta["post_dem_seats"],
        "dem_control": dem_control,
        "expected_dem_control": meta["post_dem_control"],
        "vp_tiebreak_party": vp,
        "reasons": reasons,
        "source": meta.get("source"),
        "note": meta.get("note"),
    }
    return report


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
            "Default gate is 2018–2024 (official ballots + certified margins). "
            "Pass years=(2014,2016,...) for provisional older cycles."
        ),
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "chamber_reconcile_latest.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    summary["path"] = str(path)
    return summary
