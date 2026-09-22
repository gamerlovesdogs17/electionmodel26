"""State-level demography features for similarity / covariance (Senate).

Primary path: aggregate Harvard MEDSL county election-context ACS fields
(already vendored under ``data/raw/external/medsl_election_context_2018.csv``)
up to state means. Fallback: compact research snapshot table.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

# Compact ACS-like research features (fallback only).
STATE_DEMO: dict[str, dict[str, float]] = {
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

STATE_NAME_TO_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}

MEDSL_PATH = RAW_DIR / "external" / "medsl_election_context_2018.csv"


def _medsl_state_demo(path: Path | None = None) -> pd.DataFrame:
    path = path or MEDSL_PATH
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "state" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["state_abbr"] = df["state"].map(lambda s: STATE_NAME_TO_ABBR.get(str(s), None))
    df = df[df["state_abbr"].notna()]
    # Population-weighted state means when totals exist.
    wcol = "total_population" if "total_population" in df.columns else None
    rows = []
    for st, g in df.groupby("state_abbr"):
        if wcol:
            w = g[wcol].fillna(0).astype(float)
            if float(w.sum()) <= 0:
                w = pd.Series(np.ones(len(g)))
        else:
            w = pd.Series(np.ones(len(g)))

        def _wavg(col: str, default: float) -> float:
            if col not in g.columns:
                return default
            v = pd.to_numeric(g[col], errors="coerce")
            m = v.notna() & w.notna()
            if not m.any():
                return default
            return float(np.average(v[m], weights=w[m]))

        less_college = _wavg("lesscollege_pct", 70.0)
        nonwhite = _wavg("nonwhite_pct", 30.0)
        rural = _wavg("rural_pct", 30.0)
        age65 = _wavg("age65andolder_pct", 15.0)
        # Map into the feature scheme used by similarity.
        rows.append(
            {
                "state": st,
                "college": float(np.clip(1.0 - less_college / 100.0, 0.05, 0.95)),
                "nonwhite": float(np.clip(nonwhite / 100.0, 0.01, 0.99)),
                "density": float(np.clip(np.log1p(100.0 - rural), 0.1, 5.0)),
                "age": float(np.clip((age65 - 16.0) / 10.0, -1.5, 1.5)),
                "urban": float(np.clip(1.0 - rural / 100.0, 0.05, 0.99)),
                "source": "medsl_election_context_2018",
            }
        )
    return pd.DataFrame(rows)


def write_demography_store(*, prefer_medsl: bool = True) -> dict[str, Any]:
    """Materialize sealed state demography parquet + manifest."""
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    medsl = _medsl_state_demo() if prefer_medsl else pd.DataFrame()
    if len(medsl):
        df = medsl
        tier = "aggregator"
        source_url = "https://dataverse.harvard.edu/dataverse/medsl"
        note = "State means from MEDSL 2018 election-context ACS county fields (vendored CSV)."
        source_year = 2018
        raw_sha256 = hashlib.sha256(MEDSL_PATH.read_bytes()).hexdigest()
    else:
        df = pd.DataFrame(
            [{"state": st, **vals, "source": "embedded_research_snapshot"} for st, vals in STATE_DEMO.items()]
        )
        tier = "curated"
        source_url = None
        note = "Embedded research snapshot — replace via MEDSL/Census store."
        source_year = None
        raw_sha256 = None
    df = df.copy()
    df["source_year"] = source_year
    df["available_at"] = None
    df["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    df["source_sha256"] = raw_sha256
    df["feature_version"] = "state-demo-v1"
    df["production_eligible"] = False
    out = NORMALIZED_DIR / "demography.parquet"
    df.to_parquet(out, index=False)
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": int(len(df)),
        "tier": tier,
        "eligible": tier in {"official", "first_party", "aggregator"},
        "source_url": source_url,
        "path": str(out),
        "note": note,
        "source_year": source_year,
        "available_at": None,
        "source_sha256": raw_sha256,
        "feature_version": "state-demo-v1",
        "production_eligible": False,
        "eligibility_reason": "source publication availability is not sealed; cycle reuse is an explicit approximation",
    }
    (MANIFESTS_DIR / "demography.json").write_text(json.dumps(man, indent=2))
    return man


def _demo_lookup() -> dict[str, dict[str, float]]:
    path = NORMALIZED_DIR / "demography.parquet"
    if path.exists():
        try:
            df = pd.read_parquet(path)
            out = {}
            for _, r in df.iterrows():
                out[str(r["state"])] = {
                    "college": float(r["college"]),
                    "nonwhite": float(r["nonwhite"]),
                    "density": float(r["density"]),
                    "age": float(r["age"]),
                    "urban": float(r["urban"]),
                }
            if out:
                return out
        except Exception:  # noqa: BLE001
            pass
    return STATE_DEMO


def demographic_snapshot_metadata(
    as_of: str | date,
    *,
    source_year: int = 2018,
    available_at: str | date | None = None,
) -> dict[str, Any]:
    """Describe snapshot timing without manufacturing a release date."""
    cutoff = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    available = date.fromisoformat(available_at) if isinstance(available_at, str) else available_at
    future_source = source_year > cutoff.year
    availability_verified = available is not None
    known = availability_verified and available <= cutoff and not future_source
    return {
        "schema_version": "demographic-snapshot-v1",
        "as_of": cutoff.isoformat(),
        "source_year": int(source_year),
        "available_at": available.isoformat() if available else None,
        "availability_verified": availability_verified,
        "future_source": future_source,
        "reused_outside_natural_vintage": cutoff.year != source_year,
        "modeling_approximation": cutoff.year != source_year,
        "production_eligible": known,
        "sensitivity_hook": "disable_similarity_or_compare_cycle_specific_snapshot",
    }


def demographic_snapshot_as_of(
    as_of: str | date,
    *,
    require_point_in_time: bool = False,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Load features plus their explicit vintage eligibility metadata."""
    manifest_path = MANIFESTS_DIR / "demography.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    meta = demographic_snapshot_metadata(
        as_of,
        source_year=int(manifest.get("source_year") or 2018),
        available_at=manifest.get("available_at"),
    )
    meta.update({
        "source_sha256": manifest.get("source_sha256"),
        "feature_version": manifest.get("feature_version") or "state-demo-v1",
        "source": manifest.get("source_url") or "embedded_research_snapshot",
    })
    if require_point_in_time and not meta["production_eligible"]:
        raise ValueError("demographic snapshot lacks verified point-in-time availability")
    return _demo_lookup(), meta


def demo_feature_vector(state: str) -> np.ndarray:
    lookup = _demo_lookup()
    d = lookup.get(state, {"college": 0.3, "nonwhite": 0.3, "density": 2.0, "age": 0.0, "urban": 0.7})
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
