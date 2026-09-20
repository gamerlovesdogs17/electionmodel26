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
MODEL_VERSION = "senate-hierarchical-v0.9.21"

# Blueprint audit 14 Sep 2026 (v0.9.19 review): HOLD live until truth ledger,
# vintages, fold purity, and version-coherent release clearance are repaired.
PUBLIC_LIVE_ENABLED = False
PUBLICATION_SURFACE_DEFAULT = "research_only"

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
PRODUCTION_DRAWS = 2000
PRODUCTION_TUNE = 2000
PRODUCTION_CHAINS = 4

# Joint chamber simulations (correlated margin draws → seats). Separate from
# PyMC retained posterior samples. CI/dev can use the floor; production target
# is 50k unless memory/runtime forces a documented reduction.
CI_JOINT_SIMS = 2_500
ROUTINE_JOINT_SIMS = 10_000
PRODUCTION_JOINT_SIMS = 50_000
DEMO_JOINT_SIMS = 10_000

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
