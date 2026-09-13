"""Baseline forecasters for Senate two-party margins."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from midterms.evidence.warehouse import EvidenceSnapshot


@dataclass
class RaceForecast:
    race_id: str
    state: str
    mean_margin: float
    sd: float
    p_dem: float

    def as_dict(self) -> dict:
        return {
            "race_id": self.race_id,
            "state": self.state,
            "mean_margin": self.mean_margin,
            "sd": self.sd,
            "p_dem": self.p_dem,
        }


def _contested(snapshot: EvidenceSnapshot) -> pd.DataFrame:
    return snapshot.races[~snapshot.races["not_up"]].copy()


def _p_dem_from_norm(mean: float, sd: float) -> float:
    from scipy.stats import norm

    return float(norm.sf(0, loc=mean, scale=max(sd, 0.5)))


def baseline_last_election_swing(snapshot: EvidenceSnapshot) -> list[RaceForecast]:
    """Last-election lean + national swing from recent polls' average residual."""
    races = _contested(snapshot)
    polls = snapshot.polls
    if len(polls):
        merged = polls.merge(races[["race_id", "prior_lean"]], on="race_id", how="left")
        swing = float((merged["two_party_margin"] - merged["prior_lean"]).mean())
    else:
        swing = 0.0
    out = []
    for _, r in races.iterrows():
        mu = float(r["prior_lean"]) + swing
        sd = 7.0
        out.append(
            RaceForecast(r["race_id"], r["state"], mu, sd, _p_dem_from_norm(mu, sd))
        )
    return out


def baseline_equal_weight_polls(snapshot: EvidenceSnapshot, window_days: int = 21) -> list[RaceForecast]:
    """Equal-weight average of recent polls; falls back to prior lean."""
    races = _contested(snapshot)
    polls = snapshot.polls.copy()
    if len(polls):
        polls["field_end"] = pd.to_datetime(polls["field_end"])
        cutoff = pd.Timestamp(snapshot.as_of) - pd.Timedelta(days=window_days)
        recent = polls[polls["field_end"] >= cutoff]
        if recent.empty:
            recent = polls
    else:
        recent = polls
    out = []
    for _, r in races.iterrows():
        rp = recent[recent["race_id"] == r["race_id"]] if len(recent) else recent
        if len(rp):
            mu = float(rp["two_party_margin"].mean())
            sd = float(max(rp["two_party_margin"].std(ddof=1) if len(rp) > 1 else 6.0, 4.0))
        else:
            mu = float(r["prior_lean"])
            sd = 8.0
        out.append(
            RaceForecast(r["race_id"], r["state"], mu, sd, _p_dem_from_norm(mu, sd))
        )
    return out


def baseline_shrinkage_polls(snapshot: EvidenceSnapshot, prior_strength: float = 40.0) -> list[RaceForecast]:
    """Shrinkage polling average toward prior lean with ENOP-aware influence weights."""
    from midterms.model.poll_weights import attach_poll_weights

    races = _contested(snapshot)
    polls = attach_poll_weights(snapshot.polls.copy(), as_of=snapshot.as_of) if len(snapshot.polls) else snapshot.polls
    out = []
    for _, r in races.iterrows():
        prior = float(r["prior_lean"])
        rp = polls[polls["race_id"] == r["race_id"]] if len(polls) else polls
        if len(rp):
            iw = (
                rp["influence_weight"].astype(float)
                if "influence_weight" in rp.columns
                else pd.Series(np.ones(len(rp)), index=rp.index)
            )
            w = rp["sample_size"].astype(float).clip(lower=100) * iw.clip(0.05, 3.0)
            y = rp["two_party_margin"].astype(float)
            mu = (prior_strength * prior + float((w * y).sum())) / (prior_strength + float(w.sum()))
            n_eff = float(w.sum()) / 500.0
            sd = float(np.sqrt(36.0 / max(n_eff, 0.5) + 9.0))
        else:
            mu = prior
            sd = 8.0
        out.append(
            RaceForecast(r["race_id"], r["state"], mu, sd, _p_dem_from_norm(mu, sd))
        )
    return out


BASELINES = {
    "last_election_swing": baseline_last_election_swing,
    "equal_weight_polls": baseline_equal_weight_polls,
    "shrinkage_polls": baseline_shrinkage_polls,
}
