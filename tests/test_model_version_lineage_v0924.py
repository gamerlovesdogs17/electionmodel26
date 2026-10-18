"""Model-version lineage across the sealed v0.9.23 and current artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.config import (
    ARTIFACTS_DIR,
    FORMAL_VALIDATION_LEADS,
    MODEL_VERSION,
    PREVIOUS_CODE_MODEL_VERSION,
    PREVIOUS_SEALED_MODEL_VERSION,
    PUBLIC_LIVE_ENABLED,
)
from midterms.validation.validated_model_spec import (
    canonical_sha256,
    load_validated_model_spec,
)


def test_current_model_version_is_v0926():
    assert MODEL_VERSION == "senate-hierarchical-v0.9.26"
    assert PREVIOUS_SEALED_MODEL_VERSION == "senate-hierarchical-v0.9.23"
    assert PREVIOUS_CODE_MODEL_VERSION == "senate-hierarchical-v0.9.25"
    assert PUBLIC_LIVE_ENABLED is False
    assert FORMAL_VALIDATION_LEADS == (90, 60, 30, 14, 7)


def test_sealed_v0923_validated_spec_cannot_bind_current_code(tmp_path: Path):
    path = ARTIFACTS_DIR / "validated_model_spec_latest.json"
    if not path.is_file():
        pytest.skip("validated_model_spec_latest.json missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("model_version") in {MODEL_VERSION, PREVIOUS_CODE_MODEL_VERSION}

    if payload.get("model_version") == PREVIOUS_CODE_MODEL_VERSION:
        with pytest.raises(ValueError, match="model version is stale"):
            load_validated_model_spec(path=path)

    # Preserve the old-version rejection check without requiring the live
    # `latest` artifact to remain stale after a successful current rebuild.
    payload["model_version"] = PREVIOUS_SEALED_MODEL_VERSION
    payload.pop("spec_sha256", None)
    payload["spec_sha256"] = canonical_sha256(payload)
    stale_path = tmp_path / "validated_model_spec_v0923.json"
    stale_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="model version is stale"):
        load_validated_model_spec(path=stale_path)


def test_v0923_forecast_artifact_is_historical_record():
    sealed = ARTIFACTS_DIR / "forecast_sealed_v0923_publication.json"
    latest = ARTIFACTS_DIR / "forecast_latest.json"
    if sealed.is_file():
        forecast = json.loads(sealed.read_text(encoding="utf-8"))
        assert forecast.get("model_version") == PREVIOUS_SEALED_MODEL_VERSION
    if latest.is_file():
        forecast = json.loads(latest.read_text(encoding="utf-8"))
        assert forecast.get("model_version") in {MODEL_VERSION, PREVIOUS_CODE_MODEL_VERSION}
        if forecast.get("status") == "forecast_not_rebuilt_after_spec_change":
            assert forecast.get("publishable") is False
        else:
            assert forecast.get("run_class") == "publication"
            assert forecast.get("publishable") is True
            assert forecast.get("publication_surface") == "research_only"
