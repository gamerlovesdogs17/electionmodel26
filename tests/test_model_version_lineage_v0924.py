"""v0.9.24 lineage: sealed v0.9.23 artifacts cannot promote the new code identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.config import (
    ARTIFACTS_DIR,
    FORMAL_VALIDATION_LEADS,
    MODEL_VERSION,
    PREVIOUS_SEALED_MODEL_VERSION,
    PUBLIC_LIVE_ENABLED,
)
from midterms.validation.validated_model_spec import load_validated_model_spec


def test_current_model_version_is_v0924():
    assert MODEL_VERSION == "senate-hierarchical-v0.9.25"
    assert PREVIOUS_SEALED_MODEL_VERSION == "senate-hierarchical-v0.9.23"
    assert PUBLIC_LIVE_ENABLED is False
    assert FORMAL_VALIDATION_LEADS == (90, 60, 30, 14, 7)


def test_sealed_v0923_validated_spec_cannot_bind_current_code():
    path = ARTIFACTS_DIR / "validated_model_spec_latest.json"
    if not path.is_file():
        pytest.skip("validated_model_spec_latest.json missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("model_version") == PREVIOUS_SEALED_MODEL_VERSION
    with pytest.raises(ValueError, match="model version is stale"):
        load_validated_model_spec(path=path)


def test_v0923_forecast_artifact_is_historical_record():
    sealed = ARTIFACTS_DIR / "forecast_sealed_v0923_publication.json"
    latest = ARTIFACTS_DIR / "forecast_latest.json"
    if sealed.is_file():
        forecast = json.loads(sealed.read_text(encoding="utf-8"))
        assert forecast.get("model_version") == PREVIOUS_SEALED_MODEL_VERSION
    if latest.is_file():
        stub = json.loads(latest.read_text(encoding="utf-8"))
        assert stub.get("model_version") == MODEL_VERSION
        assert stub.get("status") == "forecast_not_rebuilt_after_spec_change"
        assert stub.get("publishable") is False
