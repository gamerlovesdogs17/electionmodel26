"""Publication sampling must be explicit and fail closed."""

from __future__ import annotations

import pytest

from midterms.pipeline.inference_settings import resolve_inference_settings


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
    assert _resolve(require_publishable=True) == (2000, 2000, 4)


@pytest.mark.parametrize("overrides", [
    {"draws": 1999}, {"tune": 1999}, {"chains": 2},
])
def test_underpowered_publication_request_rejected(overrides):
    with pytest.raises(ValueError, match="production floor"):
        _resolve(require_publishable=True, **overrides)


def test_compliant_explicit_publication_settings():
    assert _resolve(require_publishable=True, draws=2000, tune=2200, chains=4) == (2000, 2200, 4)


def test_non_pymc_publication_request_rejected():
    with pytest.raises(ValueError, match="PyMC method"):
        _resolve(require_publishable=True, method="fast")
