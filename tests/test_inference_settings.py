"""Publication sampling must be explicit and fail closed."""

from __future__ import annotations

import pytest

from midterms.pipeline.inference_settings import (
    resolve_inference_settings,
    resolve_target_accept,
)


def _resolve(**overrides):
    args = {
        "draws": None,
        "tune": None,
        "chains": None,
        "require_publishable": False,
        "method": "pymc",
    }
    args.update(overrides)
    return resolve_inference_settings(**args)


def test_development_defaults():
    assert _resolve() == (800, 800, 2)


def test_publication_defaults():
    assert _resolve(require_publishable=True) == (2000, 4000, 4)


@pytest.mark.parametrize("overrides", [
    {"draws": 1999}, {"tune": 3999}, {"chains": 2},
])
def test_underpowered_publication_request_rejected(overrides):
    with pytest.raises(ValueError, match="production floor"):
        _resolve(require_publishable=True, **overrides)


def test_compliant_explicit_publication_settings():
    assert _resolve(require_publishable=True, draws=2000, tune=4200, chains=4) == (2000, 4200, 4)


def test_target_accept_defaults_and_publication_floor():
    assert resolve_target_accept(target_accept=None, require_publishable=False) == 0.90
    assert resolve_target_accept(target_accept=None, require_publishable=True) == 0.99
    assert resolve_target_accept(target_accept=0.995, require_publishable=True) == 0.995
    with pytest.raises(ValueError, match="production floor"):
        resolve_target_accept(target_accept=0.98, require_publishable=True)


@pytest.mark.parametrize("value", [0.0, 1.0, -0.1, 1.1])
def test_invalid_target_accept_rejected(value):
    with pytest.raises(ValueError, match="between 0 and 1"):
        resolve_target_accept(target_accept=value, require_publishable=False)


def test_non_pymc_publication_request_rejected():
    with pytest.raises(ValueError, match="PyMC method"):
        _resolve(require_publishable=True, method="fast")
