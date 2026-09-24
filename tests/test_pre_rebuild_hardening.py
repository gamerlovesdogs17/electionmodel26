"""Cheap synthetic coverage for the v0.9.22 pre-rebuild contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from midterms.config import MODEL_VERSION
from midterms.evidence.candidate_timeline import (
    apply_candidate_timeline,
    audit_candidate_timeline,
    audit_candidate_timeline_history,
)
from midterms.evidence.eligibility import (
    ROLE_COMPARE,
    ROLE_CONDITIONAL,
    ROLE_HARD,
    apply_domain_contract,
    effective_production_domain_contract,
)
from midterms.evidence.freshness import classify_freshness
from midterms.evidence.warehouse import (
    _selected_material_source_hashes,
    evidence_snapshot_fingerprint,
)
from midterms.model.poll_structure import PollStructureConfig, encode_poll_structure
from midterms.validation.artifact_lineage import require_current_model_version
from midterms.validation.blueprint_extension_gates import (
    evaluate_blueprint_extension_gates,
)
from midterms.validation.nested_component_loo import STRUCTURAL_VARIANTS
from midterms.validation.stack_weights import EXCLUDE_FROM_STACK
from midterms.validation.structural_ablations import (
    ablation_by_id,
    same_family_fit_spec,
)


def test_model_version_boundary_rejects_old_stack(tmp_path: Path):
    assert MODEL_VERSION == "senate-hierarchical-v0.9.22"
    payload = {"source_model_version": "senate-hierarchical-v0.9.21"}
    with pytest.raises(ValueError, match="stale stack artifact model_version"):
        require_current_model_version(
            payload, field="source_model_version", label="stack artifact",
        )


@pytest.mark.parametrize(
    ("ablation_id", "changed"),
    [
        ("hier_no_study_effect", "study_effect"),
        ("hier_no_sponsor_effect", "sponsor_effect"),
        ("hier_no_questionnaire_effect", "questionnaire_effect"),
    ],
)
def test_poll_ablation_changes_exactly_one_enabled_feature(ablation_id: str, changed: str):
    reference = PollStructureConfig(
        sponsor_effect=True, questionnaire_effect=True, study_effect=True,
    )
    spec = same_family_fit_spec(
        base_method="pymc", base_seed=5, ablation=ablation_by_id(ablation_id),
        reference_poll_structure=reference,
        reference_fit_config={"draws": 800, "tune": 800, "chains": 2},
    )
    assert spec["eligible"] is True
    assert spec["changed_features"] == [changed]
    for field in ("sponsor_effect", "questionnaire_effect", "study_effect"):
        expected = False if field == changed else True
        assert spec["challenger_config"]["poll_structure"][field] is expected
    for setting in ("draws", "tune", "chains"):
        assert spec["challenger_config"][setting] == spec["reference_config"][setting]


def test_disabled_poll_ablation_is_noop_and_ineligible():
    spec = same_family_fit_spec(
        base_method="pymc", base_seed=5,
        ablation=ablation_by_id("hier_no_study_effect"),
        reference_poll_structure=PollStructureConfig(study_effect=False),
    )
    assert spec["is_noop"] is True
    assert spec["eligible"] is False
    assert spec["changed_features"] == []


def test_every_structural_ablation_is_diagnostic_only():
    expected = {
        "hier_no_similarity", "hier_no_terminal_race", "hier_no_study_effect",
        "hier_no_sponsor_effect", "hier_no_questionnaire_effect",
    }
    assert expected <= set(STRUCTURAL_VARIANTS)
    assert expected <= set(EXCLUDE_FROM_STACK)


def test_missing_poll_metadata_never_shares_reserved_latent_group():
    polls = pd.DataFrame({
        "poll_id": ["p1", "p2", "p3"],
        "sponsor_id": [None, None, "missing-row:p1#0"],
        "questionnaire_hash": [None, None, "known-q"],
        "study_id": [None, None, "known-study"],
    })
    encoded = encode_poll_structure(polls, PollStructureConfig(
        sponsor_effect=True, questionnaire_effect=True, study_effect=True,
    ))
    assert encoded["sponsor_idx"][0] != encoded["sponsor_idx"][1]
    assert encoded["questionnaire_idx"][0] != encoded["questionnaire_idx"][1]
    assert encoded["study_idx"][0] != encoded["study_idx"][1]
    assert len(set(encoded["sponsor_ids"])) == 3
    assert encoded["missing_metadata_policy"] == "unique_row_reserved_namespace_v1"


def _snapshot(**changes):
    polls = pd.DataFrame([
        {"poll_id": "p1", "race_id": "r1", "two_party_margin": 1.0, "available_at": "2026-01-01"},
        {"poll_id": "p2", "race_id": "r2", "two_party_margin": -2.0, "available_at": "2026-01-02"},
    ])
    races = pd.DataFrame([
        {"race_id": "r1", "state": "AA", "ballot_status": "qualified", "prior_lean": 1.2},
        {"race_id": "r2", "state": "BB", "ballot_status": "qualified", "prior_lean": -0.4},
    ])
    timeline = {"status": "point_in_time", "snapshot_sha256": "a" * 64}
    prior = "b" * 64
    if "poll_margin" in changes:
        polls.loc[0, "two_party_margin"] = changes["poll_margin"]
    if "race_status" in changes:
        races.loc[0, "ballot_status"] = changes["race_status"]
    if "timeline" in changes:
        timeline["snapshot_sha256"] = changes["timeline"]
    if "prior" in changes:
        prior = changes["prior"]
    material = {"finance": changes.get("finance", "f" * 64)}
    if changes.get("reorder"):
        polls = polls.iloc[::-1][polls.columns[::-1]]
        races = races.iloc[::-1][races.columns[::-1]]
    return evidence_snapshot_fingerprint(
        election_id="synthetic-election", as_of="2026-02-01",
        polls=polls, races=races, candidate_timeline=timeline,
        pollster_ratings={"snapshot": "ratings-1"},
        prior_snapshot_sha256=prior,
        presidential_source_sha256="c" * 64,
        presidential_source_years=(2020, 2024),
        material_source_hashes=material,
    )


def test_semantic_snapshot_hash_is_order_stable_and_mutation_sensitive():
    base, parts = _snapshot()
    assert _snapshot(reorder=True)[0] == base
    assert _snapshot(poll_margin=1.1)[0] != base
    assert _snapshot(timeline="d" * 64)[0] != base
    assert _snapshot(race_status="withdrawn")[0] != base
    assert _snapshot(prior="e" * 64)[0] != base
    assert _snapshot(finance="9" * 64)[0] != base
    assert set(parts) >= {"polls_semantic_sha256", "races_semantic_sha256"}


def test_future_unavailable_secondary_evidence_does_not_change_identity(tmp_path: Path):
    path = tmp_path / "fundraising_shares.parquet"
    frame = pd.DataFrame([
        {"election_id": "synthetic-election", "race_id": "r1", "available_at": "2026-01-01", "value": 1.0},
        {"election_id": "synthetic-election", "race_id": "r1", "available_at": "2026-03-01", "value": 2.0},
    ])
    frame.to_parquet(path, index=False)
    first = _selected_material_source_hashes(
        normalized_dir=tmp_path, as_of=pd.Timestamp("2026-02-01").date(),
        election_id="synthetic-election",
    )
    frame.loc[1, "value"] = 99.0
    frame.to_parquet(path, index=False)
    second = _selected_material_source_hashes(
        normalized_dir=tmp_path, as_of=pd.Timestamp("2026-02-01").date(),
        election_id="synthetic-election",
    )
    assert first == second


def test_candidate_timeline_future_event_cannot_enter_and_held_rows_are_irrelevant():
    races = pd.DataFrame([
        {"race_id": "r1", "not_up": False},
        {"race_id": "held", "not_up": True},
    ])
    timeline = pd.DataFrame([
        {
            "event_id": "known", "race_id": "r1", "candidate_id": "c1",
            "modeled_side": "modeled", "event_type": "qualification",
            "effective_at": "2020-01-01", "available_at": "2020-01-02",
            "retrieved_at": "2020-01-02", "source_url": "https://example.test/source",
            "source_hash": "a" * 64, "parser_version": "synthetic-v1",
            "ballot_party": "A",
        },
        {
            "event_id": "known-opponent", "race_id": "r1", "candidate_id": "c-opponent",
            "modeled_side": "opposing", "event_type": "qualification",
            "effective_at": "2020-01-01", "available_at": "2020-01-02",
            "retrieved_at": "2020-01-02", "source_url": "https://example.test/source",
            "source_hash": "a" * 64, "parser_version": "synthetic-v1",
            "ballot_party": "B",
        },
        {
            "event_id": "future", "race_id": "r1", "candidate_id": "c2",
            "modeled_side": "modeled", "event_type": "replacement",
            "effective_at": "2020-02-01", "available_at": "2020-02-02",
            "retrieved_at": "2020-02-02", "source_url": "https://example.test/source",
            "source_hash": "b" * 64, "parser_version": "synthetic-v1",
        },
    ])
    early, meta = apply_candidate_timeline(races, timeline, as_of="2020-01-15")
    assert early.loc[early["race_id"] == "r1", "modeled_candidate_id"].iloc[0] == "c1"
    audit = audit_candidate_timeline(early, meta)
    assert audit["publication_eligible"] is True
    assert audit["n_required_races"] == 1


def test_candidate_timeline_requires_both_contested_identities():
    races = pd.DataFrame([{"race_id": "r1", "not_up": False}])
    timeline = pd.DataFrame([{
        "event_id": "one-side", "race_id": "r1", "candidate_id": "c1",
        "modeled_side": "modeled", "event_type": "qualification",
        "effective_at": "2020-01-01", "available_at": "2020-01-02",
        "retrieved_at": "2020-01-02", "source_url": "https://example.test/source",
        "source_hash": "a" * 64, "parser_version": "synthetic-v1",
        "ballot_party": "A",
    }])
    snap, meta = apply_candidate_timeline(races, timeline, as_of="2020-01-15")
    audit = audit_candidate_timeline(snap, meta)
    assert snap.loc[0, "candidate_timeline_status"] == "degraded_incomplete_identity"
    assert audit["publication_eligible"] is False
    assert audit["n_incomplete_identity"] == 1


def test_candidate_timeline_history_audit_is_point_in_time_and_fail_closed():
    races = pd.DataFrame([{
        "election_id": "synthetic-2020", "race_id": "r1", "not_up": False,
    }])
    common = {
        "race_id": "r1", "event_type": "qualification",
        "effective_at": "2020-01-01", "available_at": "2020-01-02",
        "retrieved_at": "2020-01-02", "source_url": "https://example.test/source",
        "source_hash": "a" * 64, "parser_version": "synthetic-v1",
    }
    timeline = pd.DataFrame([
        {**common, "event_id": "modeled", "candidate_id": "c1",
         "modeled_side": "modeled", "ballot_party": "A"},
        {**common, "event_id": "opposing", "candidate_id": "c2",
         "modeled_side": "opposing", "ballot_party": "B"},
        {**common, "event_id": "future", "candidate_id": "future-candidate",
         "modeled_side": "modeled", "ballot_party": "A",
         "effective_at": "2020-03-01", "available_at": "2020-03-02"},
    ])
    audit = audit_candidate_timeline_history(
        races, timeline, cutoffs={"synthetic-2020": "2020-02-01"},
    )
    assert audit["publication_eligible"] is True
    assert audit["cutoffs"][0]["n_point_in_time"] == 1
    incomplete = audit_candidate_timeline_history(
        races, timeline[timeline["modeled_side"].eq("modeled")],
        cutoffs={"synthetic-2020": "2020-02-01"},
    )
    assert incomplete["publication_eligible"] is False


def test_freshness_distinguishes_retrieval_observation_failures_and_future():
    common = {"domain": "polls", "checked_at": "2026-02-01"}
    assert classify_freshness(**common, retrieved_at="2026-01-31", observed_at="2026-01-30")["status"] == "fresh"
    assert classify_freshness(**common, retrieved_at="2026-01-31", observed_at="2025-01-01")["status"] == "stale_observation"
    assert classify_freshness(**common, retrieved_at="2025-01-01", observed_at="2026-01-30")["status"] == "stale_retrieval"
    assert classify_freshness(**common, parser_status="bad")["status"] == "parser_failed"
    assert classify_freshness(**common, source_available=False)["status"] == "unavailable"
    assert classify_freshness(**common, retrieved_at="2026-02-02", observed_at="2026-01-30")["status"] == "future_timestamp"


def test_domain_contract_only_blocks_layers_used_by_effective_configuration():
    domains = {
        "polls": {"eligible": True, "tier": "first_party"},
        "markets": {"eligible": False, "tier": "untraceable", "reason": "bad"},
        "comparison": {"eligible": False, "tier": "curated"},
    }
    contract = {"roles": {"polls": ROLE_HARD, "markets": "disabled", "comparison": ROLE_COMPARE}}
    annotated, failures = apply_domain_contract(domains, contract)
    assert failures == []
    assert annotated["markets"]["hard_dependency"] is False
    contract["roles"]["markets"] = ROLE_CONDITIONAL
    _, failures = apply_domain_contract(domains, contract)
    assert failures and "markets" in failures[0]
    domains["polls"]["eligible"] = False
    _, failures = apply_domain_contract(domains, effective_production_domain_contract())
    assert any("polls" in failure for failure in failures)


def test_blueprint_gate_rejects_old_model_artifact_and_defers_disabled_feature(tmp_path: Path):
    compliance = tmp_path / "audit.json"
    compliance.write_text(json.dumps({"empirical_validation_gates": {
        "sampler_health_current_reference": "requires_rebuild",
        "overlay_incremental_value": "requires_rebuild",
    }}))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "forecast_latest.json").write_text(json.dumps({
        "model_version": "senate-hierarchical-v0.9.21", "ok": True,
    }))
    report = evaluate_blueprint_extension_gates(
        compliance_path=compliance, artifacts_dir=artifacts,
    )
    assert report["required_ok"] is False
    assert report["gates"]["sampler_health_current_reference"]["status"] == "blocked"
    assert report["gates"]["overlay_incremental_value"]["status"] == "intentionally_deferred"


def test_blueprint_gate_promotes_enabled_optional_domain_to_required(tmp_path: Path):
    compliance = tmp_path / "audit.json"
    compliance.write_text(json.dumps({"empirical_validation_gates": {
        "market_contract_semantics_refresh": "requires_refresh",
    }}))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "forecast_latest.json").write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "evidence_eligibility": {
            "effective_domain_contract": {
                "roles": {"markets": "conditionally_required"},
            },
        },
    }))
    report = evaluate_blueprint_extension_gates(
        compliance_path=compliance, artifacts_dir=artifacts,
    )
    gate = report["gates"]["market_contract_semantics_refresh"]
    assert gate["required_for_full_validation"] is True
    assert gate["status"] == "blocked"


def test_same_family_gate_rejects_mismatched_frozen_index_lineage(tmp_path: Path):
    compliance = tmp_path / "audit.json"
    compliance.write_text(json.dumps({"empirical_validation_gates": {
        "same_family_ablation_oos": "requires_rebuild",
    }}))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "nested_component_loo.json").write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "frozen_index_semantic_sha256": "0" * 64,
    }))
    entries = [
        {
            "component": component, "status": "ok",
            "structural_ablation_lineage": {
                "eligible": True, "changed_features": [feature],
            },
        }
        for component, feature in (
            ("hier_no_similarity", "similarity"),
            ("hier_no_terminal_race", "terminal_race"),
        )
    ]
    (artifacts / "nested_component_loo_frozen.json").write_text(json.dumps({
        "model_version": MODEL_VERSION, "n": len(entries), "entries": entries,
    }))
    report = evaluate_blueprint_extension_gates(
        compliance_path=compliance, artifacts_dir=artifacts,
    )
    gate = report["gates"]["same_family_ablation_oos"]
    assert gate["status"] == "blocked"
    assert gate["required_frozen_index_semantic_sha256"] != gate[
        "actual_frozen_index_semantic_sha256"
    ]


def test_diagnostic_gate_requires_exact_forecast_hash(tmp_path: Path):
    compliance = tmp_path / "audit.json"
    compliance.write_text(json.dumps({"empirical_validation_gates": {
        "prior_predictive_current_model": "requires_rebuild",
    }}))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    forecast_path = artifacts / "forecast_latest.json"
    forecast_path.write_text(json.dumps({"model_version": MODEL_VERSION}))
    diagnostic_path = artifacts / "prior_predictive_latest.json"
    diagnostic = {
        "model_version": MODEL_VERSION, "ok": True, "forecast_sha256": "0" * 64,
    }
    diagnostic_path.write_text(json.dumps(diagnostic))
    report = evaluate_blueprint_extension_gates(
        compliance_path=compliance, artifacts_dir=artifacts,
    )
    assert report["gates"]["prior_predictive_current_model"]["status"] == "blocked"
    diagnostic["forecast_sha256"] = hashlib.sha256(forecast_path.read_bytes()).hexdigest()
    diagnostic_path.write_text(json.dumps(diagnostic))
    report = evaluate_blueprint_extension_gates(
        compliance_path=compliance, artifacts_dir=artifacts,
    )
    assert report["gates"]["prior_predictive_current_model"]["status"] == "pass"


def test_enabled_poll_structure_requires_matching_nested_ablation(tmp_path: Path):
    compliance = tmp_path / "audit.json"
    compliance.write_text(json.dumps({"empirical_validation_gates": {
        "poll_structure_nested_oos": "requires_rebuild",
    }}))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "forecast_latest.json").write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "diagnostics": {
            "optional_poll_structure": {"study_effect": True},
        },
    }))
    (artifacts / "nested_component_loo.json").write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "frozen_index_semantic_sha256": "0" * 64,
    }))
    report = evaluate_blueprint_extension_gates(
        compliance_path=compliance, artifacts_dir=artifacts,
    )
    gate = report["gates"]["poll_structure_nested_oos"]
    assert gate["required_for_full_validation"] is True
    assert gate["status"] == "blocked"
    assert gate["required_ablation_ids"] == ["hier_no_study_effect"]


def test_rebuild_workflow_orders_regeneration_before_forecast():
    text = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
    assert text.index("blueprint-extension-gates --source-only --strict") < text.index("nested-component-loo")
    assert "--publication-config" in text
    assert text.index("nested-component-loo") < text.index("fit-stack-weights")
    assert text.index("fit-stack-weights") < text.index("crossfit-stack-reliability")
    assert text.index("joint-oof-scores --strict") < text.index("--require-publishable")
    assert text.index("--require-publishable") < text.index("verify-rebuild --independent")
    assert "acceptance-gates --strict" in text
