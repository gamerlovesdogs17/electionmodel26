"""Regression: run coherence + joint-sim expansion must fail closed on mismatches."""

from __future__ import annotations

import json
import hashlib
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


def test_coherence_detects_stale_presidential_source(tmp_path: Path, monkeypatch):
    from midterms.config import MODEL_VERSION
    from midterms.evidence import presidential_results

    monkeypatch.setattr(presidential_results, "verified_source_set_sha256", lambda: "b" * 64)
    forecast = {
        "run_id": "synthetic-run", "model_version": MODEL_VERSION,
        "publishable": False, "run_class": "non_publication",
        "publication_surface": "research_only",
        "presidential_source_sha256": "a" * 64,
        "snapshot": {"snapshot_id": "synthetic-snapshot"},
    }
    (tmp_path / "forecast_latest.json").write_text(json.dumps(forecast), encoding="utf-8")
    report = check_run_coherence(artifacts_dir=tmp_path, require_matching_eligibility=False)
    assert not report["ok"]
    assert any("presidential source fingerprint" in item for item in report["mismatches"])


def test_coherence_rejects_stale_independent_caucus_policy(tmp_path: Path):
    from midterms.config import MODEL_VERSION

    forecast = {
        "run_id": "synthetic-run", "model_version": MODEL_VERSION,
        "election_id": "senate-2026", "publishable": False,
        "run_class": "non_publication", "publication_surface": "research_only",
        "snapshot": {"snapshot_id": "synthetic-snapshot"},
        "chamber": {"independent_caucus_policy": {
            "ballot_party": "I", "seat_accounting_caucus": "D", "basis": "old-policy",
        }},
    }
    (tmp_path / "forecast_latest.json").write_text(json.dumps(forecast), encoding="utf-8")
    report = check_run_coherence(artifacts_dir=tmp_path, require_matching_eligibility=False)
    assert not report["ok"]
    assert any("Independent caucus accounting policy" in item for item in report["mismatches"])


def test_coherence_binds_decomposition_to_forecast(tmp_path: Path):
    from midterms.config import MODEL_VERSION

    forecast = {
        "run_id": "synthetic-run", "model_version": MODEL_VERSION,
        "publishable": False, "run_class": "non_publication",
        "publication_surface": "research_only",
        "snapshot": {"snapshot_id": "synthetic-snapshot"},
        "decomposition_artifact": "race_decomposition_latest.json",
    }
    raw = json.dumps(forecast).encode()
    (tmp_path / "forecast_latest.json").write_bytes(raw)
    decomposition = {
        "run_id": "synthetic-run", "model_version": MODEL_VERSION,
        "snapshot_id": "synthetic-snapshot", "prior_store_sha256": None,
        "forecast_sha256": hashlib.sha256(raw).hexdigest(),
        "evidence_fingerprint": {"sha256": ""}, "rows": [],
    }
    path = tmp_path / "race_decomposition_latest.json"
    path.write_text(json.dumps(decomposition))
    assert check_run_coherence(artifacts_dir=tmp_path, require_matching_eligibility=False)["ok"]
    decomposition["run_id"] = "different-run"
    path.write_text(json.dumps(decomposition))
    report = check_run_coherence(artifacts_dir=tmp_path, require_matching_eligibility=False)
    assert not report["ok"]
    assert any("decomposition.run_id" in item for item in report["mismatches"])


def test_coherence_rejects_stale_market_store(tmp_path: Path, monkeypatch):
    from midterms.config import MODEL_VERSION
    from midterms.evidence import markets

    monkeypatch.setattr(markets, "verify_market_store_integrity",
                        lambda **kwargs: {"ok": False, "reason": "synthetic stale audit"})
    forecast = {
        "run_id": "synthetic-run", "model_version": MODEL_VERSION,
        "forecast_as_of": "2026-01-01", "publishable": False,
        "run_class": "non_publication", "publication_surface": "research_only",
        "market_store_sha256": "a" * 64, "market_audit_sha256": "b" * 64,
        "snapshot": {"snapshot_id": "synthetic-snapshot"},
    }
    (tmp_path / "forecast_latest.json").write_text(json.dumps(forecast), encoding="utf-8")
    report = check_run_coherence(artifacts_dir=tmp_path, require_matching_eligibility=False)
    assert not report["ok"]
    assert any("market store integrity" in item for item in report["mismatches"])


def test_coherence_rejects_stale_stack_artifact(tmp_path: Path, monkeypatch):
    from midterms.config import MODEL_VERSION
    from midterms.ops import run_coherence

    monkeypatch.setattr(run_coherence, "ARTIFACTS_DIR", tmp_path)
    stack_path = tmp_path / "stack_weights_oof.json"
    stack_path.write_text('{"synthetic":true}', encoding="utf-8")
    forecast = {
        "run_id": "synthetic-run", "model_version": MODEL_VERSION,
        "publishable": False, "run_class": "non_publication",
        "publication_surface": "research_only",
        "stack_artifact_sha256": "a" * 64,
        "snapshot": {"snapshot_id": "synthetic-snapshot"},
    }
    (tmp_path / "forecast_latest.json").write_text(json.dumps(forecast), encoding="utf-8")
    report = check_run_coherence(artifacts_dir=tmp_path, require_matching_eligibility=False)
    assert not report["ok"]
    assert any("stack artifact fingerprint" in item for item in report["mismatches"])
