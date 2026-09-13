"""Locked project configuration (mirrors docs/project-context.md Decisions)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = DATA_DIR / "normalized"
FIXTURES_DIR = DATA_DIR / "fixtures"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
MANIFESTS_DIR = DATA_DIR / "manifests"

OFFICE = "US_SENATE"
MODEL_VERSION = "senate-hierarchical-v0.9.16"

# Historical Senate cycles for holdout / replay
CYCLES = (2014, 2016, 2018, 2020, 2022, 2024)
PRIMARY_HOLDOUT = 2022
SECONDARY_HOLDOUT = 2018
LEAD_DAYS = (120, 90, 60, 45, 30, 14, 7, 1)

# Uncertainty hyperparameters (nested-validated defaults; see validation/lead_time_grid.py)
STUDENT_T_DF = 5.0
ERA_WEIGHT = 1.0

# Production method: pymc hierarchical-t (fast is CI/degraded fallback only)
PRODUCTION_METHOD = "pymc"

# Demo forecast target
DEMO_ELECTION_ID = "senate-2026"
DEMO_ELECTION_DAY = "2026-11-03"
DEMO_AS_OF = "2026-09-13"

# Inference defaults — raised for P2.3 MCSE/convergence (demo still local-friendly)
DEMO_DRAWS = 800
DEMO_TUNE = 800
DEMO_CHAINS = 2
DEMO_SEED = 20260901

# Publishable / research floors (see midterms.validation.numerical_quality)
PRODUCTION_DRAWS = 1000
PRODUCTION_TUNE = 1000
PRODUCTION_CHAINS = 4

REGIONS = {
    "AL": "South",
    "AK": "West",
    "AZ": "West",
    "AR": "South",
    "CA": "West",
    "CO": "West",
    "CT": "Northeast",
    "DE": "Northeast",
    "FL": "South",
    "GA": "South",
    "HI": "West",
    "ID": "West",
    "IL": "Midwest",
    "IN": "Midwest",
    "IA": "Midwest",
    "KS": "Midwest",
    "KY": "South",
    "LA": "South",
    "ME": "Northeast",
    "MD": "Northeast",
    "MA": "Northeast",
    "MI": "Midwest",
    "MN": "Midwest",
    "MS": "South",
    "MO": "Midwest",
    "MT": "West",
    "NE": "Midwest",
    "NV": "West",
    "NH": "Northeast",
    "NJ": "Northeast",
    "NM": "West",
    "NY": "Northeast",
    "NC": "South",
    "ND": "Midwest",
    "OH": "Midwest",
    "OK": "South",
    "OR": "West",
    "PA": "Northeast",
    "RI": "Northeast",
    "SC": "South",
    "SD": "Midwest",
    "TN": "South",
    "TX": "South",
    "UT": "West",
    "VT": "Northeast",
    "VA": "South",
    "WA": "West",
    "WV": "South",
    "WI": "Midwest",
    "WY": "West",
}
