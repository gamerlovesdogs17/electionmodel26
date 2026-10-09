"""Same-family national-environment ablations for later OOS evaluation.

Each challenger differs from the reference PRIOR_COEF treatment in exactly one
declared national-environment term. Production coefficients remain unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from midterms.model.fundamentals import PRIOR_COEF

NATIONAL_ENV_KEYS = (
    "generic_ballot",
    "pres_approval",
    "midterm_outparty",
    "real_income_yoy",
)

NATIONAL_ENVIRONMENT_ABLATIONS: dict[str, dict[str, Any]] = {
    "no_generic_ballot": {
        "zero_keys": ("generic_ballot",),
        "description": "Remove generic-ballot national environment term",
    },
    "no_approval": {
        "zero_keys": ("pres_approval",),
        "description": "Remove presidential approval term",
    },
    "no_midterm_outparty": {
        "zero_keys": ("midterm_outparty",),
        "description": "Remove midterm out-party term",
    },
    "no_income_economic": {
        "zero_keys": ("real_income_yoy",),
        "description": "Remove real-income / economic term",
    },
}


def ablation_coefficients(name: str) -> dict[str, float]:
    if name not in NATIONAL_ENVIRONMENT_ABLATIONS:
        raise KeyError(name)
    coefs = dict(PRIOR_COEF)
    for key in NATIONAL_ENVIRONMENT_ABLATIONS[name]["zero_keys"]:
        coefs[key] = 0.0
    return coefs


def ablation_lineage(name: str) -> dict[str, Any]:
    ref = dict(PRIOR_COEF)
    chall = ablation_coefficients(name)
    diffs = [k for k in ref if ref[k] != chall[k]]
    declared = list(NATIONAL_ENVIRONMENT_ABLATIONS[name]["zero_keys"])
    return {
        "challenger": name,
        "reference_coefs": ref,
        "challenger_coefs": chall,
        "differing_keys": diffs,
        "declared_zero_keys": declared,
        "lineage_ok": diffs == declared,
        "production_unchanged": True,
    }


def register_national_environment_ablations() -> dict[str, Any]:
    return {
        "schema_version": "national-environment-ablations-v1",
        "reference": deepcopy(PRIOR_COEF),
        "challengers": {
            name: ablation_lineage(name) for name in NATIONAL_ENVIRONMENT_ABLATIONS
        },
        "note": "Registered for later OOS only; production PRIOR_COEF unchanged.",
    }
