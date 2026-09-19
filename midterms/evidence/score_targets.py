"""Margin / score-target semantics for non-binary contests (audit P1)."""

from __future__ import annotations

from typing import Any

import pandas as pd


def annotate_margin_semantics(contest: dict[str, Any]) -> dict[str, Any]:
    """Attach margin_definition / margin_value / score_eligible without inventing ballot parties.

    - Ordinary D-vs-R: dem_minus_rep, score_eligible=True
    - Same-party finalists: same_party_lead, score_eligible=False for D−R models
    - Independent ballot winners (King/Sanders): independent_winner, score_eligible=False
    - Independent challenger mislabeled as Dem (Osborn): petition_independent, score_eligible=False
    """
    out = dict(contest)
    multi = dict(out.get("multiway") or {})
    winner = str(out.get("winner_party") or "")
    dem_v = float(out.get("dem_votes") or 0)
    rep_v = float(out.get("rep_votes") or 0)
    oth_v = float(out.get("other_votes") or 0)
    dem_name = out.get("dem_nominee")

    # Known petition Independent labeled as Dem in FTE (NE 2024 Osborn).
    if (
        str(out.get("race_id") or "") == "senate-2024-NE"
        and dem_name
        and "osborn" in str(dem_name).lower()
    ):
        out["other_votes"] = int(dem_v + oth_v)
        out["dem_votes"] = 0
        out["independent_nominee"] = dem_name
        out["dem_nominee"] = None
        multi["petition_independent_challenger"] = True
        multi["no_dem_nominee"] = True
        dem_v, oth_v = 0.0, float(out["other_votes"])
        dem_name = None

    if bool(multi.get("same_party_general")):
        lead = dem_v if dem_v else rep_v
        runner = oth_v
        den = lead + runner
        margin_value = round(100.0 * (lead - runner) / den, 4) if den else 0.0
        out["margin_definition"] = "same_party_lead"
        out["margin_value"] = margin_value
        out["two_party_margin"] = margin_value  # same-party lead pp, not D−R
        out["score_eligible"] = False
        out["score_exclusion_reason"] = "same_party_final"
    elif winner == "I":
        tot_dr = dem_v + rep_v
        dr_margin = round(100.0 * (dem_v - rep_v) / tot_dr, 4) if tot_dr else None
        out["margin_definition"] = "independent_winner"
        out["margin_value"] = None
        out["dem_minus_rep_among_major_parties"] = dr_margin
        out["two_party_margin"] = None
        out["score_eligible"] = False
        out["score_exclusion_reason"] = "independent_ballot_winner"
        out["ballot_winner_party"] = "I"
        # Do not list the Independent winner as Democratic nominee.
        if out.get("dem_nominee") and out.get("winner_name"):
            wn = str(out["winner_name"]).lower()
            dn = str(out["dem_nominee"]).lower()
            if wn.split()[0] in dn or any(tok in dn for tok in wn.split() if len(tok) > 3):
                out["dem_nominee"] = None
    elif bool(multi.get("petition_independent_challenger")):
        out["margin_definition"] = "independent_minus_rep"
        if rep_v and oth_v:
            out["margin_value"] = round(100.0 * (oth_v - rep_v) / (oth_v + rep_v), 4)
        else:
            out["margin_value"] = None
        out["two_party_margin"] = None
        out["score_eligible"] = False
        out["score_exclusion_reason"] = "petition_independent_challenger"
    elif dem_v > 0 and rep_v > 0:
        tot = dem_v + rep_v
        margin_value = round(100.0 * (dem_v - rep_v) / tot, 4)
        out["margin_definition"] = "dem_minus_rep"
        out["margin_value"] = margin_value
        out["two_party_margin"] = margin_value
        out["score_eligible"] = True
        out["score_exclusion_reason"] = None
    else:
        out["margin_definition"] = "nonbinary_or_missing_major"
        out["margin_value"] = None
        out["two_party_margin"] = None
        out["score_eligible"] = False
        out["score_exclusion_reason"] = "missing_dem_or_rep_votes"

    if "ballot_winner_party" not in out:
        out["ballot_winner_party"] = winner if winner in {"D", "R", "I"} else winner

    out["multiway"] = multi
    return out


def filter_score_eligible_results(results: pd.DataFrame) -> pd.DataFrame:
    """Drop races that must not train/score D−R margin models."""
    if results is None or len(results) == 0:
        return results
    out = results.copy()
    if "score_eligible" not in out.columns:
        if "two_party_margin" in out.columns:
            return out[out["two_party_margin"].notna()].copy()
        return out
    return out[out["score_eligible"].fillna(False).astype(bool)].copy()


def truth_margin_map(results: pd.DataFrame) -> dict[str, float]:
    """race_id → score-eligible margin only."""
    elig = filter_score_eligible_results(results)
    if elig is None or elig.empty:
        return {}
    col = "margin_value" if "margin_value" in elig.columns else "two_party_margin"
    out: dict[str, float] = {}
    for _, row in elig.drop_duplicates("race_id").iterrows():
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            val = row.get("two_party_margin")
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        out[str(row["race_id"])] = float(val)
    return out
