"""Audit P2.2: genuine OOF stack weights reproduce from the prediction matrix."""

from __future__ import annotations

from midterms.model.ensemble import align_weights_to_spine
from midterms.validation.stack_weights import (
    fit_stack_weights_from_oof,
    reproduce_weights,
    verify_reproducible,
)


def test_align_weights_no_longer_remaps_fast_to_pymc():
    w = align_weights_to_spine(
        {"fast_hierarchical_t": 0.5, "state_space": 0.5}, spine="pymc"
    )
    assert "fast_hierarchical_t" in w
    assert "pymc" not in w


def test_fit_and_reproduce_from_matrix():
    crps = {
        "2018": {
            "fast_hierarchical_t": 6.0,
            "state_space": 4.0,
            "ridge_fundamentals": 3.5,
            "poll_only_state_space": 10.0,
            "hier_no_similarity": 6.1,
        },
        "2020": {
            "fast_hierarchical_t": 5.0,
            "state_space": 3.5,
            "ridge_fundamentals": 3.2,
            "poll_only_state_space": 11.0,
            "hier_no_similarity": 5.2,
        },
    }
    g8 = {
        "poll_only_state_space": {"recommend": "disable"},
        "state_space": {"recommend": "keep"},
        "ridge_fundamentals": {"recommend": "keep"},
    }
    fitted = fit_stack_weights_from_oof(crps, g8_recommendations=g8, temperature=0.75)
    assert "poll_only_state_space" not in fitted["stack_weights"]
    assert "hier_no_similarity" not in fitted["stack_weights"]
    assert "ridge_fundamentals" in fitted["stack_weights"]
    assert fitted["no_weight_remapping"] is True
    ver = verify_reproducible(fitted)
    assert ver["ok"] is True
    assert abs(sum(fitted["stack_weights"].values()) - 1.0) < 1e-9
    again = reproduce_weights(fitted)
    for k in fitted["stack_weights"]:
        assert abs(fitted["stack_weights"][k] - again[k]) < 1e-12


def test_fit_from_nested_loo_artifact():
    from pathlib import Path

    from midterms.config import ARTIFACTS_DIR
    from midterms.validation.stack_weights import fit_stack_weights_from_nested_loo

    nested = ARTIFACTS_DIR / "nested_component_loo.json"
    if not nested.exists():
        return  # skip if P2.1 artifact absent in bare CI
    report = fit_stack_weights_from_nested_loo()
    assert report["audit_item"] == "P2.2"
    assert report["reproduction"]["ok"] is True
    assert Path(report["path"]).exists()
    assert "pymc" not in report["stack_weights"] or report.get("source_spine_label") == "pymc"
