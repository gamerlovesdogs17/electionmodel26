"""Regression: run coherence + joint-sim expansion must fail closed on mismatches."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from midterms.ops.run_coherence import check_run_coherence
from midterms.simulate.chamber import expand_joint_draws


def test_expand_joint_draws_preserves_correlation_shape():
    rng = np.random.default_rng(0)
    base = rng.normal(size=(100, 5))
    # Shared national shock → correlated columns
    nat = rng.normal(size=(100, 1))
    margins = base + nat
    out = expand_joint_draws(margins, n_sims=500, seed=3)
    assert out.shape == (500, 5)
    # Resampled rows must be exact copies of some posterior row
    matched = 0
    for row in out[:20]:
        if any(np.allclose(row, margins[i]) for i in range(len(margins))):
            matched += 1
    assert matched == 20


def test_coherence_detects_eligibility_publishable_mismatch(tmp_path: Path):
    from midterms.config import MODEL_VERSION

    art = tmp_path
    forecast = {
        "run_id": "run-a",
        "model_version": MODEL_VERSION,
        "publishable": True,
        "run_class": "publication",
        "publication_surface": "research_only",
        "evidence_fingerprint": {"sha256": "abc"},
        "evidence_eligibility": {"publishable": True, "run_class": "publication"},
        "snapshot": {"snapshot_id": "snap1"},
    }
    eligibility = {
        "publishable": False,
        "run_class": "non_publication",
        "reasons": ["stale"],
        "forecast_run_id": "run-b",
        "evidence_fingerprint": {"sha256": "abc"},
    }
    (art / "forecast_latest.json").write_text(json.dumps(forecast), encoding="utf-8")
    (art / "evidence_eligibility_latest.json").write_text(
        json.dumps(eligibility), encoding="utf-8"
    )
    rep = check_run_coherence(artifacts_dir=art)
    assert rep["ok"] is False
    assert any("publishable" in m for m in rep["mismatches"])
    assert any("run_id" in m for m in rep["mismatches"])


def test_evidence_eligible_development_run_is_not_mislabeled(tmp_path: Path):
    from midterms.config import MODEL_VERSION

    forecast = {
        "run_id": "dev-a", "model_version": MODEL_VERSION,
        "publishable": False, "run_class": "non_publication",
        "publication_surface": "research_only",
        "evidence_fingerprint": {"sha256": "abc"},
        "evidence_eligibility": {"publishable": True, "run_class": "publication"},
        "snapshot": {"snapshot_id": "snap-a"},
    }
    eligibility = {
        "publishable": True, "run_class": "publication",
        "forecast_run_id": "dev-a", "evidence_fingerprint": {"sha256": "abc"},
    }
    (tmp_path / "forecast_latest.json").write_text(json.dumps(forecast), encoding="utf-8")
    (tmp_path / "evidence_eligibility_latest.json").write_text(json.dumps(eligibility), encoding="utf-8")
    report = check_run_coherence(artifacts_dir=tmp_path)
    assert report["ok"] is True
    assert any("inference is non-publication" in note for note in report["notes"])
