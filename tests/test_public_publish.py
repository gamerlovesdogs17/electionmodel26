"""Public live publication gate (post Milestone-0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.ops.public_publish import assert_public_ready, stamp_public_release


def _base_artifact(**overrides):
    art = {
        "run_id": "test-run",
        "election_id": "senate-2026",
        "as_of": "2026-09-13",
        "model_version": "senate-hierarchical-v0.9.15",
        "method": "ensemble_stack+overlays",
        "run_class": "publication",
        "publishable": True,
        "evidence_eligibility": {"publishable": True, "run_class": "publication", "reasons": []},
        "numerical_quality": {"ok": True, "alerts": [], "checks": []},
        "diagnostics": {"core_method": "pymc", "spine_method": "pymc"},
        "chamber": {"p_dem_majority": 0.42, "expected_dem_seats": 48},
        "seed": 1,
    }
    art.update(overrides)
    return art


def test_assert_public_ready_passes():
    gates = {"ok": True, "failures": []}
    out = assert_public_ready(artifact=_base_artifact(), gates=gates)
    assert out["ok"] is True
    assert out["reasons"] == []


def test_assert_public_ready_fails_on_red_gates():
    out = assert_public_ready(
        artifact=_base_artifact(),
        gates={"ok": False, "failures": ["G9"]},
    )
    assert out["ok"] is False
    assert any("acceptance" in r for r in out["reasons"])


def test_assert_public_ready_fails_non_publication():
    out = assert_public_ready(
        artifact=_base_artifact(run_class="non_publication", publishable=False),
        gates={"ok": True, "failures": []},
    )
    assert out["ok"] is False


def test_stamp_public_release_sets_surface():
    stamped = stamp_public_release(_base_artifact(), publication_id="public_test")
    assert stamped["publication_surface"] == "live"
    assert stamped["public_release"]["enabled"] is True
    assert stamped["public_release"]["publication_id"] == "public_test"
