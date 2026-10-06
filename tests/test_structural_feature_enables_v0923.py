"""Selection G8 drives which hierarchical structural terms stay enabled."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from midterms.config import MODEL_VERSION
from midterms.validation.blueprint_extension_gates import evaluate_blueprint_extension_gates
from midterms.validation.validated_model_spec import (
    coerce_structural_feature_enables,
    structural_feature_enables_from_g8,
)


def test_structural_feature_enables_follow_keep_votes_only():
    enables = structural_feature_enables_from_g8({
        "similarity_terminal": {"recommend": "keep"},
        "terminal_race": {"recommend": "drop_or_shrink"},
    })
    assert enables == {
        "similarity_terminal": True,
        "terminal_race": False,
    }
    assert coerce_structural_feature_enables(None)["terminal_race"] is True


def test_same_family_gate_allows_disabled_drop_or_shrink(tmp_path: Path):
    compliance = tmp_path / "audit.json"
    compliance.write_text(json.dumps({
        "empirical_validation_gates": {"same_family_ablation_oos": "requires_rebuild"},
    }))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    nested = {
        "model_version": MODEL_VERSION,
        "frozen_index_semantic_sha256": "a" * 64,
        "g8_recommendations": {
            "similarity_terminal": {"recommend": "keep"},
            "terminal_race": {"recommend": "drop_or_shrink"},
        },
    }
    nested_path = artifacts / "nested_component_loo_selection.json"
    nested_path.write_text(json.dumps(nested))
    nested["frozen_index_semantic_sha256"] = hashlib.sha256(
        # placeholder; overwritten below after frozen file exists
        b""
    ).hexdigest()
    entries = [
        {
            "component": "hier_no_similarity",
            "status": "ok",
            "structural_ablation_lineage": {
                "eligible": True,
                "changed_features": ["similarity"],
            },
        },
        {
            "component": "hier_no_terminal_race",
            "status": "ok",
            "structural_ablation_lineage": {
                "eligible": True,
                "changed_features": ["terminal_race"],
            },
        },
    ]
    frozen_path = artifacts / "nested_component_loo_selection_frozen.json"
    frozen_path.write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "n": len(entries),
        "entries": entries,
    }))
    from midterms.validation.artifact_lineage import frozen_index_semantic_sha256

    nested["frozen_index_semantic_sha256"] = frozen_index_semantic_sha256(frozen_path)
    nested_path.write_text(json.dumps(nested))
    (artifacts / "validated_model_spec_candidate.json").write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "structural_feature_enables": {
            "similarity_terminal": True,
            "terminal_race": False,
        },
    }))
    report = evaluate_blueprint_extension_gates(
        compliance_path=compliance, artifacts_dir=artifacts,
    )
    gate = report["gates"]["same_family_ablation_oos"]
    assert gate["status"] == "pass"
    assert gate["structural_feature_enables"]["terminal_race"] is False
