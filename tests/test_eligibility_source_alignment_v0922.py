"""Canonical v0.9.22 source selection shared by readiness and eligibility."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from midterms.evidence.approval import aggregate_votehub_approval_vintages
from midterms.evidence.demographic_vintages import ingest_demographic_vintages
from midterms.evidence.eligibility import (
    _audit_configured_domains,
    apply_domain_contract,
    audit_canonical_demographics,
    audit_canonical_finance,
    audit_canonical_approval,
    candidate_timeline_freshness,
    domain_freshness_from_provenance,
)
from midterms.evidence.source_readiness import audit_source_readiness
from midterms.evidence.source_registry import canonical_domain_contract


def _finance_store(tmp_path, *, available_at: str = "2026-09-27"):
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    normalized.mkdir()
    manifests.mkdir()
    frame = pd.DataFrame([{
        "election_id": "senate-2026",
        "state": "AA",
        "race_id": "senate-2026-AA",
        "fundraising_share": 0.5,
        "available_at": available_at,
        "feature_as_of": "2026-09-27",
        "availability_basis": "fec_receipt_date",
        "source": "fec_form3_report_summaries",
    }])
    path = normalized / "fundraising_shares.parquet"
    frame.to_parquet(path, index=False)
    manifest = {
        "schema_version": "fec-report-finance-v1",
        "parser_version": "fec-v2-report-receipt-asof",
        "production_eligible": True,
        "tier": "first_party",
        "source_url": "https://www.fec.gov/data/reports/",
        "normalized": {
            "fundraising_shares": {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            },
        },
        "sources": [{
            "cycle": 2026,
            "form3": {"retrieved_at": "2026-09-29T12:00:00Z"},
        }],
    }
    (manifests / "fundraising_shares.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    return normalized, manifests


def _demographic_store(tmp_path):
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    normalized.mkdir(exist_ok=True)
    manifests.mkdir(exist_ok=True)
    source = tmp_path / "demographics.csv"
    pd.DataFrame([{
        "dataset_id": "acs5",
        "dataset_version": "2020-2024",
        "geographic_level": "state",
        "source_vintage": "2020-2024",
        "official_release_date": "2026-01-29",
        "retrieved_at": "2026-09-28T12:00:00Z",
        "source_url": "https://www.census.gov/",
        "raw_object_sha256": "a" * 64,
        "feature_definition_version": "test-v1",
        "state": "AA",
        "college": 0.4,
        "nonwhite": 0.3,
        "density": 4.0,
        "age": 0.2,
        "urban": 0.8,
    }]).to_csv(source, index=False)
    ingest_demographic_vintages(
        source,
        normalized_path=normalized / "demographic_vintages.parquet",
        manifest_path=manifests / "demographic_vintages.json",
    )
    return normalized, manifests


def _approval_store(tmp_path, *, available_at="2026-09-15", poll_end="2026-09-14"):
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    normalized.mkdir(exist_ok=True)
    manifests.mkdir(exist_ok=True)
    frame = pd.DataFrame([{
        "year": 2026,
        "available_at": available_at,
        "max_poll_end": poll_end,
        "retrieved_at": "2026-09-29T12:00:00Z",
        "white_house_party": "R",
        "net_approval": -5.0,
        "source": "votehub_approval_aggregate",
        "production_eligible": True,
    }])
    path = normalized / "pres_approval.parquet"
    frame.to_parquet(path, index=False)
    (manifests / "pres_approval.json").write_text(json.dumps({
        "tier": "aggregator",
        "source_url": "https://api.votehub.com/polls",
        "generated_at": "2026-09-29T12:00:00Z",
        "normalized_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return normalized, manifests


def test_strict_demographics_uses_canonical_vintage_store(tmp_path):
    normalized, manifests = _demographic_store(tmp_path)
    result = audit_canonical_demographics(
        as_of="2026-09-27",
        normalized_dir=normalized,
        manifests_dir=manifests,
    )
    assert result["eligible"] is True
    assert result["tier"] == "official"
    assert result["selected_vintages"] == ["2020-2024"]
    assert result["freshness_provenance"]["observed_at"].startswith("2026-01-29")
    assert result["canonical_source_contract"]["normalized_name"] == (
        "demographic_vintages.parquet"
    )


def test_strict_finance_uses_receipt_safe_store_and_date_only_is_not_future(tmp_path):
    normalized, manifests = _finance_store(tmp_path)
    result = audit_canonical_finance(
        election_id="senate-2026",
        as_of="2026-09-27",
        normalized_dir=normalized,
        manifests_dir=manifests,
    )
    assert result["eligible"] is True
    assert result["receipt_date_safe"] is True
    assert result["source_mix"] == {"fec_form3_report_summaries": 1}
    freshness = domain_freshness_from_provenance(
        "finance",
        checked_at="2026-09-29T12:30:00Z",
        **result["freshness_provenance"],
    )
    assert freshness["status"] == "fresh"


def test_strict_finance_still_rejects_genuinely_future_receipt(tmp_path):
    normalized, manifests = _finance_store(tmp_path, available_at="2026-09-30")
    result = audit_canonical_finance(
        election_id="senate-2026",
        as_of="2026-09-27",
        normalized_dir=normalized,
        manifests_dir=manifests,
    )
    assert result["eligible"] is False
    assert "no rows available" in result["blocked_reason"]


def test_current_approval_uses_created_at_cutoff_and_exact_store_hash(tmp_path):
    normalized, manifests = _approval_store(tmp_path)
    result = audit_canonical_approval(
        election_id="senate-2026",
        as_of="2026-09-27",
        normalized_dir=normalized,
        manifests_dir=manifests,
    )
    assert result["eligible"] is True
    assert result["freshness_provenance"]["observed_at"].startswith("2026-09-14")


def test_future_or_mutated_approval_fails_closed(tmp_path):
    normalized, manifests = _approval_store(tmp_path, available_at="2026-09-30")
    future = audit_canonical_approval(
        election_id="senate-2026", as_of="2026-09-27",
        normalized_dir=normalized, manifests_dir=manifests,
    )
    assert future["eligible"] is False
    assert "no source-backed current-cycle row" in future["blocked_reason"]
    frame = pd.read_parquet(normalized / "pres_approval.parquet")
    frame.loc[0, "net_approval"] = -4.0
    frame.to_parquet(normalized / "pres_approval.parquet", index=False)
    mutated = audit_canonical_approval(
        election_id="senate-2026", as_of="2026-10-01",
        normalized_dir=normalized, manifests_dir=manifests,
    )
    assert mutated["eligible"] is False
    assert "hash mismatch" in mutated["blocked_reason"]


def test_live_approval_does_not_use_poll_before_record_created_at():
    rows = aggregate_votehub_approval_vintages(polls=[{
        "id": "synthetic-approval-late",
        "end_date": "2026-09-18",
        "created_at": "2026-09-20",
        "sample_size": 800,
        "answers": [
            {"choice": "Approve", "pct": 45},
            {"choice": "Disapprove", "pct": 50},
        ],
    }])
    assert rows["available_at"].min() >= "2026-09-20"
    assert rows["availability_basis"].eq("votehub_poll_created_at").all()


def test_conditional_candidate_freshness_is_not_required_for_side_only_races():
    audit = {
        "eligible": True,
        "conditional_identity_contract": True,
        "n_identity_required_and_resolved": 0,
        "n_identity_required_and_missing": 0,
    }
    freshness = candidate_timeline_freshness(
        audit, {}, checked_at="2026-09-29T12:00:00Z",
    )
    assert freshness["status"] == "not_applicable"
    annotated, failures = apply_domain_contract(
        {"candidate_timeline": {**audit, "freshness": freshness}},
        {"roles": {"candidate_timeline": "required_core"}},
    )
    assert failures == []
    assert annotated["candidate_timeline"]["effective_eligible"] is True


def test_identity_sensitive_candidate_freshness_fails_missing_and_future_provenance():
    audit = {
        "eligible": True,
        "conditional_identity_contract": True,
        "n_identity_required_and_resolved": 1,
        "n_identity_required_and_missing": 0,
    }
    missing = candidate_timeline_freshness(
        audit, {}, checked_at="2026-09-29T12:00:00Z",
    )
    assert missing["status"] == "stale_retrieval"
    future = candidate_timeline_freshness(
        audit,
        {
            "latest_retrieved_at": "2026-09-30T12:00:00Z",
            "latest_effective_at": "2026-09-30",
        },
        checked_at="2026-09-29T12:00:00Z",
    )
    assert future["status"] == "future_timestamp"


def test_registry_resolved_identity_uses_review_boundary_for_freshness():
    audit = {
        "eligible": True,
        "conditional_identity_contract": True,
        "n_identity_required_and_resolved": 1,
        "n_identity_required_and_missing": 0,
        "classification_records": [
            {
                "race_id": "senate-2026-AL",
                "candidate_identity_required": True,
                "candidate_identity_resolved": True,
                "identity_source": "reviewed_current_candidate_registry",
                "identity_reviewed_as_of": "2026-10-03",
            }
        ],
    }
    freshness = candidate_timeline_freshness(
        audit, {}, checked_at="2026-10-05T12:00:00Z",
    )
    assert freshness["status"] == "fresh"
    assert freshness["identity_freshness_basis"] == "reviewed_current_candidate_registry"
    annotated, failures = apply_domain_contract(
        {"candidate_timeline": {**audit, "freshness": freshness}},
        {"roles": {"candidate_timeline": "required_core"}},
    )
    assert failures == []
    assert annotated["candidate_timeline"]["effective_eligible"] is True


def test_readiness_and_eligibility_select_same_canonical_products():
    readiness = audit_source_readiness(
        election_id="senate-2026", as_of="2026-09-27",
    )
    eligibility = _audit_configured_domains(
        election_id="senate-2026", as_of="2026-09-27",
    )
    for domain in ("finance", "demographics"):
        assert readiness["domains"][domain]["status"] == "ready"
        assert eligibility[domain]["eligible"] is True
        assert (
            readiness["domains"][domain]["canonical_source_contract"]
            == eligibility[domain]["canonical_source_contract"]
        )


def test_required_domain_clock_contracts_are_explicit():
    expected = {
        "polls": "polls.parquet",
        "races": "races_official.parquet",
        "presidential_prior": "presidential_vote_counts.parquet",
        "candidate_timeline": "candidate_timeline.parquet",
        "finance": "fundraising_shares.parquet",
        "economics": "economics_vintages.parquet",
        "approval": "pres_approval.parquet",
        "demographics": "demographic_vintages.parquet",
    }
    for domain, normalized_name in expected.items():
        contract = canonical_domain_contract(domain)
        assert contract["normalized_name"] == normalized_name
        assert contract["manifest_name"]
        assert contract["availability_clock"]
        assert contract["operational_freshness_clock"]


def test_rebuild_tests_cannot_mutate_the_sealed_evidence_checkout():
    workflow = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
    tests_job = workflow.index("  tests:")
    rebuild_job = workflow.index("  rebuild:")
    assert tests_job < rebuild_job
    assert "needs: [prepare_evidence, tests]" in workflow[rebuild_job:]
    tests_block = workflow[tests_job:rebuild_job]
    assert "Verify sealed publication eligibility artifact" in tests_block
    assert "Run evidence-compatible regression suite" in tests_block
    assert "Smoke forecast at sealed evidence cutoff" in tests_block
    assert "Checkout exact evidence commit for tests" in tests_block
    # Sealed eligibility check must precede mutating forecast tests.
    assert tests_block.index("Verify sealed publication eligibility artifact") < (
        tests_block.index("Run evidence-compatible regression suite")
    )
    assert tests_block.index("tests/test_demo_as_of_registry_boundary.py") < (
        tests_block.index("tests/test_model.py")
    )
    assert "pytest -q" not in workflow[rebuild_job:]
