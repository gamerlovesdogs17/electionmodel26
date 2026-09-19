"""Acceptance gates + calibration diagnostics (Milestone-0)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from midterms.config import MODEL_VERSION
from midterms.validation.acceptance_gates import evaluate_acceptance_gates
from midterms.validation.metrics import (
    calibration_slope_intercept,
    pit_summary,
    reliability_overconfidence,
    score_margins_extended,
)


def test_calibration_slope_and_pit():
    rng = np.random.default_rng(0)
    means = rng.normal(0, 5, size=40)
    sds = np.full(40, 4.0)
    y = means + rng.normal(0, 4.0, size=40)
    probs = 1.0 / (1.0 + np.exp(-means / 5.0))
    outcomes = (y > 0).astype(float)
    cal = calibration_slope_intercept(probs, outcomes)
    assert cal["ok"] is True
    assert np.isfinite(cal["slope"])
    pit = pit_summary(means, sds, y)
    assert pit["ok"] is True
    assert pit["n"] == 40
    ext = score_margins_extended(means, sds, y, probs=probs)
    assert "log_score" in ext
    assert "calibration_slope_intercept" in ext
    assert "pit" in ext
    assert "reliability_gate" in ext


def test_reliability_overconfidence_blocks_claim():
    bins = [
        {"bin_lo": 0.8, "bin_hi": 1.0, "n": 20, "mean_p": 0.9, "mean_y": 0.5},
    ]
    gate = reliability_overconfidence(bins)
    assert gate["calibration_claim_allowed"] is False
    assert gate["n_overconfident"] == 1


def test_acceptance_gates_from_stubs(tmp_path: Path):
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "chamber_reconcile_latest.json").write_text(
        json.dumps({"ok": True, "gate_years": [2018, 2020, 2022, 2024], "by_cycle": {}, "failures": []}),
        encoding="utf-8",
    )
    (art / "poll_coverage_latest.json").write_text(
        json.dumps({"ok": True, "years": [2018], "failures": []}),
        encoding="utf-8",
    )
    (art / "evidence_eligibility_latest.json").write_text(
        json.dumps({"ok": False, "publishable": False, "run_class": "non_publication", "reasons": ["demo"]}),
        encoding="utf-8",
    )
    (art / "nested_component_loo.json").write_text(
        json.dumps(
            {
                "freeze_before_truth": True,
                "no_weight_remapping": True,
                "spine_label": "pymc",
                "hierarchical_method": "pymc",
                "years": [2020, 2022],
                "lead_days": [60, 30],
                "mean_crps": 4.0,
                "mean_crps_by_component": {"pymc": 4.0},
                "crps_by_fold": {
                    "2020": {"pymc": 4.1},
                    "2022": {"pymc": 4.0},
                },
                "reliability": {
                    "spine": "pymc",
                    "n": 100,
                    "reliability": [
                        {"bin_lo": 0.0, "bin_hi": 0.5, "n": 50, "mean_p": 0.4, "mean_y": 0.42},
                        {"bin_lo": 0.5, "bin_hi": 1.0, "n": 50, "mean_p": 0.6, "mean_y": 0.58},
                    ],
                    "reliability_gate": {
                        "sample_sizes_disclosed": True,
                        "n_overconfident": 0,
                        "thin_sample": False,
                        "calibration_claim_allowed": True,
                        "total_n": 100,
                        "n_adequate_bins": 2,
                    },
                    "ok": True,
                },
                "g8_recommendations": {
                    "state_space": {"recommend": "keep"},
                    "poll_only_state_space": {"recommend": "disable"},
                },
            }
        ),
        encoding="utf-8",
    )
    (art / "stack_weights_oof.json").write_text(
        json.dumps(
            {
                "no_weight_remapping": True,
                "source_spine_label": "pymc",
                "source_hierarchical_method": "pymc",
                "stack_weights": {"pymc": 0.3, "state_space": 0.7},
                "reproduction": {"ok": True},
                "matrix_sha256": "abc",
            }
        ),
        encoding="utf-8",
    )
    (art / "forecast_latest.json").write_text(
        json.dumps(
            {
                "method": "pymc",
                "model_version": MODEL_VERSION,
                "run_class": "non_publication",
                "publishable": False,
                "publication_surface": "research_only",
                "limitations": ["test stub"],
                "numerical_quality": {"ok": True, "chamber_mcse": {"mcse_p_dem_control": 0.005}},
            }
        ),
        encoding="utf-8",
    )
    (art / "validation_report_latest.json").write_text(
        json.dumps(
            {
                "calibration": {
                    "n": 10,
                    "brier": 0.2,
                    "reliability": [
                        {"bin_lo": 0.4, "bin_hi": 0.6, "n": 10, "mean_p": 0.5, "mean_y": 0.5}
                    ],
                    "margin_scores": {
                        "reliability": [
                            {"bin_lo": 0.4, "bin_hi": 0.6, "n": 10, "mean_p": 0.5, "mean_y": 0.5}
                        ],
                        "reliability_gate": {
                            "sample_sizes_disclosed": True,
                            "n_overconfident": 0,
                            "calibration_claim_allowed": True,
                        },
                    },
                },
                "limitations": ["test"],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_acceptance_gates(artifacts_dir=art, write=True)
    assert (art / "acceptance_gates_latest.json").exists()
    assert (art / "acceptance_gates_latest.md").exists()
    assert report["gates"]["G1"]["status"] == "pass"
    assert report["gates"]["G2"]["status"] == "pass"
    assert report["gates"]["G3"]["status"] == "pass"
    assert report["gates"]["G5"]["ok"] is True
    assert report["gates"]["G6"]["ok"] is True
    assert report["gates"]["G8"]["status"] == "pass"
    assert report["gates"]["G9"]["status"] == "pass"
    assert report["gates"]["G11"]["ok"] is True
    assert "G1" not in report["failures"]


def test_acceptance_g9_fails_when_numerical_missing(tmp_path: Path):
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "chamber_reconcile_latest.json").write_text(
        json.dumps({"ok": True, "gate_years": [2022], "by_cycle": {}, "failures": []}),
        encoding="utf-8",
    )
    (art / "poll_coverage_latest.json").write_text(
        json.dumps({"ok": True, "years": [2022], "failures": []}),
        encoding="utf-8",
    )
    (art / "evidence_eligibility_latest.json").write_text(
        json.dumps({"ok": True, "publishable": True, "run_class": "publication", "reasons": []}),
        encoding="utf-8",
    )
    (art / "nested_component_loo.json").write_text(
        json.dumps(
            {
                "freeze_before_truth": True,
                "no_weight_remapping": True,
                "spine_label": "pymc",
                "hierarchical_method": "pymc",
                "crps_by_fold": {"2022": {"pymc": 4.0}},
                "g8_recommendations": {"state_space": {"recommend": "keep"}},
            }
        ),
        encoding="utf-8",
    )
    (art / "stack_weights_oof.json").write_text(
        json.dumps(
            {
                "no_weight_remapping": True,
                "source_spine_label": "pymc",
                "source_hierarchical_method": "pymc",
                "stack_weights": {"pymc": 1.0},
                "reproduction": {"ok": True},
            }
        ),
        encoding="utf-8",
    )
    (art / "forecast_latest.json").write_text(
        json.dumps({"method": "pymc", "run_class": "publication", "publishable": True}),
        encoding="utf-8",
    )
    report = evaluate_acceptance_gates(artifacts_dir=art, write=False)
    assert report["gates"]["G9"]["status"] == "fail"
    assert report["gates"]["G5"]["status"] == "pass"
