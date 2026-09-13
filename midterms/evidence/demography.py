"""State-level demography features for similarity / covariance (Senate)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Compact ACS-like research features (standardized-ish). Redistributable snapshots —
# not a live Census API pull. Values are approximate public statistics for pooling.
STATE_DEMO: dict[str, dict[str, float]] = {
    # college_share, nonwhite_share, density_log, median_age_z, urban_share
    "AL": {"college": 0.26, "nonwhite": 0.35, "density": 2.4, "age": 0.2, "urban": 0.59},
    "AK": {"college": 0.30, "nonwhite": 0.40, "density": 0.1, "age": -0.3, "urban": 0.66},
    "AZ": {"college": 0.31, "nonwhite": 0.45, "density": 1.8, "age": 0.1, "urban": 0.90},
    "AR": {"college": 0.24, "nonwhite": 0.28, "density": 1.7, "age": 0.2, "urban": 0.56},
    "CA": {"college": 0.35, "nonwhite": 0.65, "density": 3.2, "age": -0.2, "urban": 0.95},
    "CO": {"college": 0.42, "nonwhite": 0.33, "density": 1.7, "age": -0.4, "urban": 0.86},
    "CT": {"college": 0.40, "nonwhite": 0.35, "density": 3.5, "age": 0.3, "urban": 0.88},
    "DE": {"college": 0.33, "nonwhite": 0.38, "density": 3.0, "age": 0.3, "urban": 0.83},
    "FL": {"college": 0.32, "nonwhite": 0.47, "density": 3.1, "age": 0.8, "urban": 0.91},
    "GA": {"college": 0.33, "nonwhite": 0.48, "density": 2.5, "age": -0.1, "urban": 0.75},
    "HI": {"college": 0.34, "nonwhite": 0.78, "density": 3.0, "age": 0.2, "urban": 0.92},
    "ID": {"college": 0.29, "nonwhite": 0.18, "density": 0.8, "age": -0.2, "urban": 0.71},
    "IL": {"college": 0.36, "nonwhite": 0.39, "density": 3.1, "age": 0.1, "urban": 0.88},
    "IN": {"college": 0.28, "nonwhite": 0.22, "density": 2.5, "age": 0.1, "urban": 0.72},
    "IA": {"college": 0.30, "nonwhite": 0.15, "density": 1.7, "age": 0.2, "urban": 0.64},
    "KS": {"college": 0.34, "nonwhite": 0.24, "density": 1.3, "age": 0.0, "urban": 0.74},
    "KY": {"college": 0.26, "nonwhite": 0.16, "density": 2.2, "age": 0.2, "urban": 0.58},
    "LA": {"college": 0.25, "nonwhite": 0.42, "density": 2.3, "age": 0.1, "urban": 0.73},
    "ME": {"college": 0.33, "nonwhite": 0.07, "density": 1.4, "age": 0.7, "urban": 0.39},
    "MD": {"college": 0.41, "nonwhite": 0.50, "density": 3.4, "age": 0.2, "urban": 0.87},
    "MA": {"college": 0.45, "nonwhite": 0.30, "density": 3.8, "age": 0.2, "urban": 0.92},
    "MI": {"college": 0.31, "nonwhite": 0.25, "density": 2.6, "age": 0.3, "urban": 0.75},
    "MN": {"college": 0.38, "nonwhite": 0.21, "density": 2.0, "age": 0.1, "urban": 0.73},
    "MS": {"college": 0.23, "nonwhite": 0.44, "density": 1.8, "age": 0.1, "urban": 0.49},
    "MO": {"college": 0.30, "nonwhite": 0.21, "density": 2.2, "age": 0.2, "urban": 0.70},
    "MT": {"college": 0.33, "nonwhite": 0.14, "density": 0.3, "age": 0.3, "urban": 0.56},
    "NE": {"college": 0.33, "nonwhite": 0.22, "density": 1.0, "age": 0.0, "urban": 0.73},
    "NV": {"college": 0.26, "nonwhite": 0.52, "density": 1.2, "age": -0.1, "urban": 0.94},
    "NH": {"college": 0.38, "nonwhite": 0.10, "density": 2.2, "age": 0.4, "urban": 0.60},
    "NJ": {"college": 0.41, "nonwhite": 0.45, "density": 4.0, "age": 0.3, "urban": 0.95},
    "NM": {"college": 0.28, "nonwhite": 0.63, "density": 0.8, "age": 0.2, "urban": 0.77},
    "NY": {"college": 0.38, "nonwhite": 0.45, "density": 3.9, "age": 0.2, "urban": 0.88},
    "NC": {"college": 0.33, "nonwhite": 0.38, "density": 2.6, "age": 0.0, "urban": 0.66},
    "ND": {"college": 0.31, "nonwhite": 0.15, "density": 0.4, "age": -0.2, "urban": 0.60},
    "OH": {"college": 0.30, "nonwhite": 0.22, "density": 2.9, "age": 0.3, "urban": 0.78},
    "OK": {"college": 0.27, "nonwhite": 0.34, "density": 1.7, "age": 0.1, "urban": 0.65},
    "OR": {"college": 0.35, "nonwhite": 0.25, "density": 1.5, "age": 0.2, "urban": 0.81},
    "PA": {"college": 0.33, "nonwhite": 0.24, "density": 3.0, "age": 0.4, "urban": 0.79},
    "RI": {"college": 0.35, "nonwhite": 0.30, "density": 3.9, "age": 0.3, "urban": 0.91},
    "SC": {"college": 0.30, "nonwhite": 0.36, "density": 2.3, "age": 0.2, "urban": 0.66},
    "SD": {"college": 0.30, "nonwhite": 0.18, "density": 0.4, "age": 0.1, "urban": 0.57},
    "TN": {"college": 0.29, "nonwhite": 0.27, "density": 2.4, "age": 0.2, "urban": 0.66},
    "TX": {"college": 0.32, "nonwhite": 0.59, "density": 2.5, "age": -0.3, "urban": 0.85},
    "UT": {"college": 0.36, "nonwhite": 0.23, "density": 1.4, "age": -1.0, "urban": 0.90},
    "VT": {"college": 0.40, "nonwhite": 0.07, "density": 1.0, "age": 0.6, "urban": 0.39},
    "VA": {"college": 0.40, "nonwhite": 0.38, "density": 2.6, "age": 0.1, "urban": 0.76},
    "WA": {"college": 0.38, "nonwhite": 0.32, "density": 2.5, "age": -0.1, "urban": 0.84},
    "WV": {"college": 0.22, "nonwhite": 0.08, "density": 1.9, "age": 0.5, "urban": 0.49},
    "WI": {"college": 0.32, "nonwhite": 0.19, "density": 2.4, "age": 0.2, "urban": 0.70},
    "WY": {"college": 0.28, "nonwhite": 0.15, "density": 0.2, "age": 0.0, "urban": 0.65},
}


def demo_feature_vector(state: str) -> np.ndarray:
    d = STATE_DEMO.get(state, {"college": 0.3, "nonwhite": 0.3, "density": 2.0, "age": 0.0, "urban": 0.7})
    return np.array(
        [d["college"], d["nonwhite"], d["density"] / 4.0, d["age"], d["urban"]],
        dtype=float,
    )


def attach_demo_features(races: pd.DataFrame) -> pd.DataFrame:
    out = races.copy()
    feats = [demo_feature_vector(str(s)) for s in out["state"]]
    mat = np.vstack(feats) if feats else np.zeros((0, 5))
    out["demo_college"] = mat[:, 0] if len(mat) else []
    out["demo_nonwhite"] = mat[:, 1] if len(mat) else []
    out["demo_density"] = mat[:, 2] if len(mat) else []
    out["demo_age"] = mat[:, 3] if len(mat) else []
    out["demo_urban"] = mat[:, 4] if len(mat) else []
    return out


def demo_matrix(races: pd.DataFrame) -> np.ndarray:
    return np.vstack([demo_feature_vector(str(s)) for s in races["state"]]) if len(races) else np.zeros((0, 5))
