"""Audit P3: write-once shadow publication + verify."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.ops import shadow_publish as sp


def test_write_once_refuses_overwrite(tmp_path: Path):
    p = tmp_path / "a.json"
    sp._write_once(p, '{"x": 1}')
    with pytest.raises(FileExistsError):
        sp._write_once(p, '{"x": 2}')


def test_verify_live_shadow_seal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sp, "SHADOW_ROOT", tmp_path / "shadow")
    monkeypatch.setattr(sp, "SHADOW_INDEX", tmp_path / "shadow_publications.jsonl")
    monkeypatch.setattr(sp, "ARTIFACTS_DIR", tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    forecast = {
        "election_id": "senate-2026",
        "as_of": "2026-09-13",
        "run_class": "research",
        "publishable": False,
        "numerical_quality": {"ok": True, "audit_item": "P2.3"},
    }
    (tmp_path / "artifacts" / "forecast_latest.json").write_text(
        json.dumps(forecast), encoding="utf-8"
    )
    (tmp_path / "artifacts" / "stack_weights_oof.json").write_text("{}", encoding="utf-8")

    man = sp.publish_live_shadow(
        forecast_path=tmp_path / "artifacts" / "forecast_latest.json",
        shadow_id="shadow_test_live",
        shadow_root=tmp_path / "shadow",
    )
    assert man["mode"] == "prospective_live"
    dest = Path(man["path"])
    assert (dest / "forecast.json").exists()
    assert (dest / "SHADOW_MANIFEST.json").exists()
    assert (dest / "evaluation.json").exists() is False

    ver = sp.verify_shadow(path=dest)
    assert ver["ok"] is True
    assert ver["has_evaluation"] is False

    # Tamper → verify fails
    (dest / "forecast.json").write_text('{"tampered": true}', encoding="utf-8")
    ver2 = sp.verify_shadow(path=dest)
    assert ver2["ok"] is False
    assert ver2["mismatches"]

    # Write-once on sealed forecast
    with pytest.raises(FileExistsError):
        sp._write_once(dest / "forecast.json", "{}")


def test_list_and_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sp, "SHADOW_INDEX", tmp_path / "idx.jsonl")
    monkeypatch.setattr(sp, "MANIFESTS_DIR", tmp_path)
    sp.append_shadow_index({"shadow_id": "a", "mode": "historical_cycle"})
    rows = sp.list_shadow_publications()
    assert len(rows) == 1
    assert rows[0]["shadow_id"] == "a"
