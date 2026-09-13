"""Fundamentals nested ablation smoke test."""

from __future__ import annotations

from midterms.validation.fundamentals_ablation import run_primary_holdout_ablation


def test_fundamentals_ablation_primary():
    report = run_primary_holdout_ablation()
    assert report["n_cycles"] >= 1
    assert "recommendations" in report
    assert "fundraising_logit" in report["recommendations"]
    assert report.get("path")
