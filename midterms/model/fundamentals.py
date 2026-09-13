"""Restrained congressional fundamentals prior (Election-Day anchor)."""

from __future__ import annotations

import numpy as np
import pandas as pd


# Predeclared, regularized coefficients (pp). Selected to be small and stable;
# nested cycle replay can ablate them. Not fit on the target cycle.
COEF = {
    "prior_lean": 1.0,
    "generic_ballot": 0.35,
    "incumbency": 2.0,
    "fundraising_logit": 1.4,  # applied to logit(share) - logit(0.5)
    "pres_approval": 0.08,  # net approval → Dem margin contribution
    "midterm_outparty": 1.2,  # midterm shift toward out-party (signed by White House party)
    "real_income_yoy": 0.35,  # pp of Dem margin per pp of YoY real disposable income growth
}


def _logit(p: np.ndarray | float) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 0.02, 0.98)
    return np.log(p / (1.0 - p))


def fundamentals_mean(
    races: pd.DataFrame,
    generic_ballot: float = 0.0,
    *,
    pres_approval: float | None = None,
    white_house_party: str | None = None,
    real_income_yoy: float | None = None,
) -> pd.Series:
    """
    Parsimonious structural mean margin (dem - rep pp).

    Wired as the Election-Day prior mean inside the hierarchical model — not a
    second additive correction on top of the same evidence.

    Optional race columns (filled by fixtures / future ingest):
      - fundraising_share: Dem share of candidate receipts in [0, 1]
      - is_midterm: bool
      - white_house_party: 'D' | 'R' (cycle-level; may also be passed in)
      - pres_approval: presidential net approval (positive = popular), Dem-signed later
    """
    contested = races[~races["not_up"]].copy()
    if contested.empty:
        return pd.Series(dtype=float, name="fundamentals_mean")

    lean = contested["prior_lean"].astype(float).to_numpy()
    mu = COEF["prior_lean"] * lean + COEF["generic_ballot"] * float(generic_ballot)

    # Incumbency / open-seat
    inc = contested["incumbent_party"]
    open_mask = contested["is_open"].astype(bool).to_numpy()
    # Ind incumbents who caucus with Democrats get the Dem-side incumbency bump.
    bump = np.where(
        open_mask,
        0.0,
        np.where(
            inc.isin(["D", "I"]),
            COEF["incumbency"],
            np.where(inc == "R", -COEF["incumbency"], 0.0),
        ),
    )
    mu = mu + bump

    # Fundraising share → stable log-odds vs 50/50 (not raw dollars)
    if "fundraising_share" in contested.columns:
        share = contested["fundraising_share"].astype(float).fillna(0.5).to_numpy()
        mu = mu + COEF["fundraising_logit"] * (_logit(share) - _logit(0.5))

    # Presidential approval (cycle-level). Positive approval helps the White House party.
    if pres_approval is None and "pres_approval" in contested.columns:
        # Use race-constant value when present
        vals = contested["pres_approval"].astype(float).dropna()
        if len(vals):
            pres_approval = float(vals.iloc[0])
    if white_house_party is None and "white_house_party" in contested.columns:
        wh = contested["white_house_party"].dropna()
        if len(wh):
            white_house_party = str(wh.iloc[0])

    if pres_approval is not None and white_house_party in {"D", "R"}:
        signed = float(pres_approval) if white_house_party == "D" else -float(pres_approval)
        mu = mu + COEF["pres_approval"] * signed

    # Midterm out-party bonus (historical regularity; small and ablatable)
    is_midterm = False
    if "is_midterm" in contested.columns:
        is_midterm = bool(contested["is_midterm"].astype(bool).iloc[0])
    else:
        # Infer from election_id / election_day year
        year = None
        if "election_day" in contested.columns and len(contested):
            try:
                year = int(str(contested["election_day"].iloc[0])[:4])
            except (TypeError, ValueError):
                year = None
        if year is not None:
            is_midterm = year % 4 == 2

    if is_midterm and white_house_party in {"D", "R"}:
        # Out-party gains: if WH is R, Dems get +bonus
        mid = COEF["midterm_outparty"] if white_house_party == "R" else -COEF["midterm_outparty"]
        mu = mu + mid

    # Vintage real disposable income growth (ALFRED / fixture). Helps WH party.
    if real_income_yoy is None and "real_income_yoy" in contested.columns:
        vals = contested["real_income_yoy"].astype(float).dropna()
        if len(vals):
            real_income_yoy = float(vals.iloc[0])
    if real_income_yoy is not None and white_house_party in {"D", "R"}:
        signed = float(real_income_yoy) if white_house_party == "D" else -float(real_income_yoy)
        mu = mu + COEF["real_income_yoy"] * signed

    return pd.Series(mu, index=contested["race_id"], name="fundamentals_mean")
