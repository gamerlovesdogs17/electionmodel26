"""Continuous race-similarity covariance for joint margin shocks."""

from __future__ import annotations

import numpy as np
import pandas as pd


def race_feature_matrix(races: pd.DataFrame) -> np.ndarray:
    """
    Compact features for similarity: region one-hots, lean, open, fundraising.
    Rows aligned to `races` order.
    """
    n = len(races)
    regions = sorted(races["region"].astype(str).unique())
    region_idx = {r: i for i, r in enumerate(regions)}
    X = np.zeros((n, len(regions) + 3), dtype=float)
    for i, (_, row) in enumerate(races.iterrows()):
        X[i, region_idx[str(row["region"])]] = 1.0
        X[i, len(regions)] = float(row.get("prior_lean") or 0.0) / 20.0
        X[i, len(regions) + 1] = 1.0 if bool(row.get("is_open")) else 0.0
        share = row.get("fundraising_share")
        try:
            X[i, len(regions) + 2] = (float(share) - 0.5) * 2.0 if share == share else 0.0
        except (TypeError, ValueError):
            X[i, len(regions) + 2] = 0.0
    # Column-standardize non-one-hot cols lightly
    for j in range(len(regions), X.shape[1]):
        col = X[:, j]
        sd = float(col.std())
        if sd > 1e-6:
            X[:, j] = (col - col.mean()) / sd
    return X


def similarity_matrix(X: np.ndarray, *, length_scale: float = 1.75) -> np.ndarray:
    """RBF similarity on rows; diagonal 1; PSD via jitter."""
    n = X.shape[0]
    if n == 0:
        return np.zeros((0, 0))
    # squared distances
    G = X @ X.T
    sq = np.clip(np.diag(G)[:, None] + np.diag(G)[None, :] - 2.0 * G, 0.0, None)
    ell2 = max(float(length_scale) ** 2, 1e-3)
    R = np.exp(-0.5 * sq / ell2)
    np.fill_diagonal(R, 1.0)
    R = 0.5 * (R + R.T)
    R = R + np.eye(n) * 1e-6
    return R


def correlated_shocks(
    races: pd.DataFrame,
    n_draws: int,
    rng: np.random.Generator,
    *,
    scale: np.ndarray | float = 1.0,
    length_scale: float = 1.75,
    nu: float = 5.0,
) -> np.ndarray:
    """
    Heavy-tailed correlated shocks with continuous similarity covariance.

    Returns array (n_draws, n_races).
    """
    n = len(races)
    if n == 0:
        return np.zeros((n_draws, 0))
    X = race_feature_matrix(races)
    R = similarity_matrix(X, length_scale=length_scale)
    try:
        L = np.linalg.cholesky(R)
    except np.linalg.LinAlgError:
        L = np.linalg.cholesky(R + np.eye(n) * 1e-4)
    # Student-t via normal / chi2
    z = rng.standard_normal(size=(n, n_draws))
    chi = rng.chisquare(nu, size=(1, n_draws))
    t = z / np.sqrt(chi / nu)
    shocks = (L @ t).T  # (n_draws, n)
    if np.isscalar(scale):
        return shocks * float(scale)
    scale_arr = np.asarray(scale, dtype=float).reshape(1, -1)
    return shocks * scale_arr
