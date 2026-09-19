"""Restrained congressional fundamentals prior (Election-Day anchor).

Audit P1.2: ``PRIOR_COEF`` are documented prior means. Production ``COEF`` may be
replaced by nested leave-one-cycle ridge estimates (shrinkage toward PRIOR).
``prior_lean`` remains fixed at 1.0 (identity); see FIXED_COEF_RATIONALE.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

import numpy as np
import pandas as pd


# Documented prior means (pp). Nested LOO ridge shrinks toward these.
PRIOR_COEF: dict[str, float] = {
    "prior_lean": 1.0,
    "generic_ballot": 0.28,
    "incumbency": 2.0,
    "fundraising_logit": 1.4,  # applied to logit(share) - logit(0.5)
    "pres_approval": 0.08,  # net approval → Dem margin contribution
    "midterm_outparty": 1.2,  # midterm shift toward out-party (signed by White House party)
    "real_income_yoy": 0.35,  # pp of Dem margin per pp of YoY real disposable income growth
}

# Working coefficients (mutable for ablation / nested estimation).
COEF: dict[str, float] = dict(PRIOR_COEF)

# Coefficients estimated by nested ridge (prior_lean excluded — identity map).
SHRINKABLE_KEYS: tuple[str, ...] = (
    "generic_ballot",
    "incumbency",
    "fundraising_logit",
    "pres_approval",
    "midterm_outparty",
    "real_income_yoy",
)

FIXED_COEF_RATIONALE: dict[str, str] = {
    "prior_lean": (
        "Identity scale of vintaged lean into margin units; lean construction is "
        "the calibrated object, not a free slope in production."
    ),
}


def _logit(p: np.ndarray | float) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 0.02, 0.98)
    return np.log(p / (1.0 - p))


def set_coefs(coefs: dict[str, float] | None = None) -> dict[str, float]:
    """Replace working COEF (defaults restore PRIOR). Returns previous values."""
    old = deepcopy(COEF)
    COEF.clear()
    COEF.update(PRIOR_COEF if coefs is None else {**PRIOR_COEF, **coefs})
    # Always keep prior_lean fixed at identity
    COEF["prior_lean"] = 1.0
    return old


def feature_row(
    row: pd.Series,
    *,
    generic_ballot: float = 0.0,
    real_income_yoy: float | None = None,
) -> dict[str, float]:
    """Design-matrix row matching ``fundamentals_mean`` channels (Dem margin units)."""
    lean = float(row.get("prior_lean") or 0.0)
    open_mask = bool(row.get("is_open"))
    inc = str(row.get("incumbent_party") or "")
    if open_mask:
        incumbency = 0.0
    elif inc in {"D", "I"}:
        incumbency = 1.0
    elif inc == "R":
        incumbency = -1.0
    else:
        incumbency = 0.0

    share = float(row["fundraising_share"]) if pd.notna(row.get("fundraising_share")) else 0.5
    fund_logit = float(_logit(share) - _logit(0.5))

    wh = str(row.get("white_house_party") or "")
    if pd.isna(row.get("pres_approval")):
        pa = 0.0
    else:
        pa = float(row.get("pres_approval") or 0.0)
    if wh == "D":
        pres_signed = pa
    elif wh == "R":
        pres_signed = -pa
    else:
        pres_signed = 0.0

    is_midterm = bool(row.get("is_midterm")) if "is_midterm" in row.index and pd.notna(row.get("is_midterm")) else False
    if not is_midterm and "election_day" in row.index:
        try:
            year = int(str(row["election_day"])[:4])
            is_midterm = year % 4 == 2
        except (TypeError, ValueError):
            pass
    if is_midterm and wh == "R":
        mid = 1.0
    elif is_midterm and wh == "D":
        mid = -1.0
    else:
        mid = 0.0

    if real_income_yoy is None and "real_income_yoy" in row.index and pd.notna(row.get("real_income_yoy")):
        real_income_yoy = float(row["real_income_yoy"])
    if real_income_yoy is not None and wh in {"D", "R"}:
        ri = float(real_income_yoy) if wh == "D" else -float(real_income_yoy)
    else:
        ri = 0.0

    return {
        "prior_lean": lean,
        "generic_ballot": float(generic_ballot),
        "incumbency": incumbency,
        "fundraising_logit": fund_logit,
        "pres_approval": pres_signed,
        "midterm_outparty": mid,
        "real_income_yoy": ri,
    }


def fundamentals_mean(
    races: pd.DataFrame,
    generic_ballot: float = 0.0,
    *,
    pres_approval: float | None = None,
    white_house_party: str | None = None,
    real_income_yoy: float | None = None,
    coefs: dict[str, float] | None = None,
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
    c = coefs if coefs is not None else COEF
    contested = races[~races["not_up"]].copy()
    if contested.empty:
        return pd.Series(dtype=float, name="fundamentals_mean")

    if pres_approval is not None:
        contested["pres_approval"] = float(pres_approval)
    if white_house_party is not None:
        contested["white_house_party"] = white_house_party

    rows = []
    for _, row in contested.iterrows():
        feats = feature_row(row, generic_ballot=generic_ballot, real_income_yoy=real_income_yoy)
        mu = sum(float(c[k]) * float(feats[k]) for k in PRIOR_COEF)
        rows.append(mu)

    return pd.Series(rows, index=contested["race_id"], name="fundamentals_mean")


def _cycle_generic_ballot(polls: pd.DataFrame, races: pd.DataFrame) -> float:
    if polls is None or len(polls) == 0 or races is None or len(races) == 0:
        return 0.0
    merged = polls.merge(races[["race_id", "prior_lean"]], on="race_id", how="left")
    if merged.empty or "two_party_margin" not in merged.columns:
        return 0.0
    delta = merged["two_party_margin"].astype(float) - merged["prior_lean"].astype(float)
    return float(delta.mean()) if len(delta) else 0.0


def build_design(
    races: pd.DataFrame,
    results: pd.DataFrame,
    *,
    polls: pd.DataFrame | None = None,
    generic_ballot: float | None = None,
    real_income_yoy: float | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return X, y, feature_names for contested races with certified margins."""
    keys = list(PRIOR_COEF.keys())
    if races.empty or results.empty:
        return np.zeros((0, len(keys))), np.zeros(0), keys
    res_all = results.set_index("race_id")
    from midterms.evidence.score_targets import filter_score_eligible_results

    elig = filter_score_eligible_results(results)
    ycol = "margin_value" if "margin_value" in elig.columns else "two_party_margin"
    res = elig.set_index("race_id")[ycol].astype(float) if len(elig) else res_all.get(
        "two_party_margin", pd.Series(dtype=float)
    )
    gb = (
        float(generic_ballot)
        if generic_ballot is not None
        else _cycle_generic_ballot(polls if polls is not None else pd.DataFrame(), races)
    )
    xs: list[list[float]] = []
    ys: list[float] = []
    contested = races[~races["not_up"]].copy() if "not_up" in races.columns else races.copy()
    for _, row in contested.iterrows():
        rid = row["race_id"]
        if rid not in res.index:
            continue
        y = float(res.loc[rid])
        if not np.isfinite(y):
            continue
        feats = feature_row(row, generic_ballot=gb, real_income_yoy=real_income_yoy)
        xs.append([float(feats[k]) for k in keys])
        ys.append(y)
    if not xs:
        return np.zeros((0, len(keys))), np.zeros(0), keys
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float), keys


def ridge_toward_prior(
    X: np.ndarray,
    y: np.ndarray,
    *,
    prior: dict[str, float] | None = None,
    feature_names: list[str] | None = None,
    ridge_lambda: float = 25.0,
    fixed_keys: Iterable[str] = ("prior_lean",),
) -> dict[str, float]:
    """
    Ridge regression shrunk toward ``prior`` means.

    Fixed keys are pinned (not free parameters). Empty design returns prior.
    """
    prior = prior or PRIOR_COEF
    names = feature_names or list(PRIOR_COEF.keys())
    beta0 = np.array([float(prior[k]) for k in names], dtype=float)
    fixed = set(fixed_keys)
    if X.size == 0 or len(y) == 0:
        return {k: float(prior[k]) for k in names}

    # Solve only free columns; pin fixed
    free_idx = [i for i, k in enumerate(names) if k not in fixed]
    if not free_idx:
        return {k: float(prior[k]) for k in names}

    Xf = X[:, free_idx]
    b0f = beta0[free_idx]
    # Residualize fixed contribution from y
    y_adj = y.copy()
    for i, k in enumerate(names):
        if k in fixed:
            y_adj = y_adj - beta0[i] * X[:, i]

    lam = float(max(ridge_lambda, 1e-6))
    xtx = Xf.T @ Xf + lam * np.eye(len(free_idx))
    xty = Xf.T @ y_adj + lam * b0f
    try:
        bf = np.linalg.solve(xtx, xty)
    except np.linalg.LinAlgError:
        bf = b0f

    out = {k: float(prior[k]) for k in names}
    for j, i in enumerate(free_idx):
        out[names[i]] = float(bf[j])
    out["prior_lean"] = 1.0
    return out


def estimate_coefs_nested(
    *,
    holdout_year: int | None = None,
    train_years: Iterable[int] | None = None,
    ridge_lambda: float = 25.0,
) -> dict[str, Any]:
    """
    Leave-one-cycle ridge estimate of fundamentals coefficients.

    When ``holdout_year`` is set, that cycle is excluded from the design.
    When ``train_years`` is set, only those cycles are used.
    """
    from midterms.config import CYCLES
    from midterms.evidence.warehouse import Warehouse

    wh = Warehouse()
    years = list(train_years) if train_years is not None else list(CYCLES)
    if holdout_year is not None:
        years = [y for y in years if int(y) != int(holdout_year)]

    Xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    used: list[int] = []
    for year in years:
        eid = f"senate-{year}"
        races = wh.races[wh.races["election_id"] == eid]
        results = wh.results[wh.results["election_id"] == eid]
        if races.empty or results.empty:
            continue
        polls = wh.polls[wh.polls["race_id"].isin(set(races["race_id"]))] if len(wh.polls) else pd.DataFrame()
        real_income = None
        try:
            from datetime import date, timedelta

            from midterms.evidence.economics import yoy_growth_as_of

            ed = date.fromisoformat(str(races["election_day"].iloc[0])[:10])
            real_income = yoy_growth_as_of(ed - timedelta(days=30), election_year=year)
        except Exception:  # noqa: BLE001
            real_income = None
        X, y, names = build_design(races, results, polls=polls, real_income_yoy=real_income)
        if len(y):
            Xs.append(X)
            ys.append(y)
            used.append(int(year))

    if not Xs:
        coefs = dict(PRIOR_COEF)
        return {
            "coefs": coefs,
            "prior": dict(PRIOR_COEF),
            "train_years": used,
            "holdout_year": holdout_year,
            "n_rows": 0,
            "ridge_lambda": ridge_lambda,
            "shrinkable_keys": list(SHRINKABLE_KEYS),
            "fixed_keys": dict(FIXED_COEF_RATIONALE),
            "note": "no training rows; returned PRIOR_COEF",
        }

    X = np.vstack(Xs)
    y = np.concatenate(ys)
    coefs = ridge_toward_prior(X, y, ridge_lambda=ridge_lambda, feature_names=names)
    return {
        "coefs": coefs,
        "prior": dict(PRIOR_COEF),
        "train_years": used,
        "holdout_year": holdout_year,
        "n_rows": int(len(y)),
        "ridge_lambda": ridge_lambda,
        "shrinkable_keys": list(SHRINKABLE_KEYS),
        "fixed_keys": dict(FIXED_COEF_RATIONALE),
        "delta_vs_prior": {k: float(coefs[k] - PRIOR_COEF[k]) for k in PRIOR_COEF},
    }
