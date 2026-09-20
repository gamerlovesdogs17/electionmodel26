"""Resolve sampling settings before a forecast touches evidence or fits a model."""

from __future__ import annotations

from midterms.config import (
    DEMO_CHAINS,
    DEMO_DRAWS,
    DEMO_TUNE,
    PRODUCTION_CHAINS,
    PRODUCTION_DRAWS,
    PRODUCTION_TUNE,
)


def resolve_inference_settings(
    *,
    draws: int | None,
    tune: int | None,
    chains: int | None,
    require_publishable: bool,
    method: str,
    allow_fast_fallback: bool = False,
) -> tuple[int, int, int]:
    """Apply demo or publication defaults, and reject underpowered publication fits."""
    if require_publishable and method not in {"pymc", "pymc_dynamic"}:
        raise ValueError("publishable runs require a PyMC method with convergence diagnostics")
    if require_publishable and allow_fast_fallback:
        raise ValueError("publishable runs cannot allow a fast fallback")
    floors = (
        (PRODUCTION_DRAWS, PRODUCTION_TUNE, PRODUCTION_CHAINS)
        if require_publishable
        else (DEMO_DRAWS, DEMO_TUNE, DEMO_CHAINS)
    )
    values = tuple(floor if supplied is None else int(supplied) for floor, supplied in zip(floors, (draws, tune, chains)))
    for name, value, floor in zip(("draws", "tune", "chains"), values, floors):
        if value < 1:
            raise ValueError(f"{name} must be positive")
        if require_publishable and value < floor:
            raise ValueError(f"publishable {name}={value} is below the production floor {floor}")
    return values
