"""Economics timeout / empty-read fail-closed behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from midterms.evidence import economics as econ


def test_timeout_preserves_non_fixture_store(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(econ, "NORMALIZED_DIR", tmp_path)
    monkeypatch.setattr(econ, "MANIFESTS_DIR", tmp_path)
    monkeypatch.setattr(econ, "RAW_DIR", tmp_path)

    prod = pd.DataFrame(
        [
            {
                "series_id": "A229RX0_YOY",
                "observation_date": "2026-08-01",
                "available_at": "2026-08-15",
                "value": 1.2,
                "revision": 0,
                "election_year": 2026,
            }
        ]
    )
    econ.write_economic_store(prod)

    def _timeout(*_a, **_k):
        raise TimeoutError("economics read operation timed out")

    with (
        patch.object(econ, "fetch_alfred_observations", side_effect=_timeout),
        patch.object(econ, "fetch_fred_public_csv", side_effect=_timeout),
    ):
        meta = econ.try_refresh_alfred(as_of="2026-09-01")

    assert meta.get("timeout") is True
    assert meta.get("used_fixtures") is False
    assert meta.get("preserved_existing") is True
    assert meta.get("publication_eligible") is True
    stored = pd.read_parquet(tmp_path / "economics_vintages.parquet")
    assert stored["series_id"].astype(str).str.contains("FIXTURE").eq(False).all()


def test_worldbank_fallback_when_fred_times_out(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(econ, "NORMALIZED_DIR", tmp_path)
    monkeypatch.setattr(econ, "MANIFESTS_DIR", tmp_path)
    monkeypatch.setattr(econ, "RAW_DIR", tmp_path)

    def _timeout(*_a, **_k):
        raise TimeoutError("The read operation timed out")

    wb = pd.DataFrame(
        [
            {
                "series_id": econ.WB_GDPPC_SERIES,
                "observation_date": "2024-12-31",
                "available_at": "2024-12-31",
                "value": 1.8,
                "revision": 0,
                "election_year": 2024,
                "source_url": econ.WB_SOURCE_URL,
            }
        ]
    )
    with (
        patch.object(econ, "fetch_alfred_observations", return_value=pd.DataFrame()),
        patch.object(econ, "fetch_fred_public_csv", side_effect=_timeout),
        patch.object(econ, "fetch_worldbank_us_gdppc_yoy", return_value=wb),
    ):
        meta = econ.try_refresh_alfred(as_of="2026-09-01")

    assert meta.get("used_fixtures") is False
    assert meta.get("publication_eligible") is True
    assert meta.get("source") == "worldbank_gdppc_yoy"
    assert meta.get("timeout") is True
    stored = pd.read_parquet(tmp_path / "economics_vintages.parquet")
    assert stored["series_id"].astype(str).str.contains("WB_").any()


def test_fixture_only_when_fred_and_worldbank_fail(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(econ, "NORMALIZED_DIR", tmp_path)
    monkeypatch.setattr(econ, "MANIFESTS_DIR", tmp_path)
    monkeypatch.setattr(econ, "RAW_DIR", tmp_path)

    def _timeout(*_a, **_k):
        raise TimeoutError("The read operation timed out")

    with (
        patch.object(econ, "fetch_alfred_observations", return_value=pd.DataFrame()),
        patch.object(econ, "fetch_fred_public_csv", side_effect=_timeout),
        patch.object(econ, "fetch_worldbank_us_gdppc_yoy", side_effect=_timeout),
    ):
        meta = econ.try_refresh_alfred(as_of="2026-09-01")

    assert meta.get("used_fixtures") is True
    assert meta.get("publication_eligible") is False


def test_yoy_does_not_silently_use_fixtures(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(econ, "NORMALIZED_DIR", tmp_path)
    monkeypatch.setattr(econ, "MANIFESTS_DIR", tmp_path)
    monkeypatch.setattr(econ, "RAW_DIR", tmp_path)
    econ.write_economic_store(econ.build_fixture_vintages())
    assert econ.yoy_growth_as_of("2022-09-01", election_year=2022) is None
    assert (
        econ.yoy_growth_as_of("2022-09-01", election_year=2022, allow_fixture_canary=True)
        is not None
    )
