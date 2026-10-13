"""Cheap tests for immutable seal-time eligibility during checkpoint resume."""

from __future__ import annotations

from copy import deepcopy

from midterms.config import MODEL_VERSION
from midterms.evidence.eligibility import (
    effective_production_domain_contract,
    verify_sealed_eligibility_report,
)
from midterms.evidence.evidence_bundle import build_evidence_bundle

AS_OF = "2026-10-10"
MANIFESTS = {
    "races": "official_senate_ballots.json",
    "structural_prior": "presidential_vote_sources.json",
    "candidate_timeline": "candidate_timeline.json",
    "finance": "fundraising_shares.json",
    "economics": "economics_vintages.json",
    "approval": "pres_approval.json",
    "demographics": "demographic_vintages.json",
}
BUNDLE_NAMES = {
    "races": "races",
    "polls": "polls",
    "structural_prior": "presidential_prior",
    "candidate_timeline": "candidate_timeline",
    "finance": "finance",
    "economics": "economics",
    "approval": "approval",
    "demographics": "demographics",
}


def _sealed_pair():
    contract = effective_production_domain_contract()
    domains = {}
    manifest_hashes = {}
    report_domains = {}
    for index, (name, role) in enumerate(contract["roles"].items(), start=1):
        if role != "required_core":
            continue
        digest = f"{index:064x}"
        bundle_name = BUNDLE_NAMES[name]
        domains[bundle_name] = {"status": "ready", "semantic_sha256": digest}
        manifest_name = MANIFESTS.get(name)
        if manifest_name:
            domains[bundle_name]["manifest_sha256"] = digest
            manifest_hashes[manifest_name] = digest
        report_domains[name] = {
            "eligible": True,
            "hard_dependency": True,
            "effective_eligible": True,
            "freshness": {
                "status": "fresh",
                "required_for_effective_input": True,
            },
        }
    bundle = build_evidence_bundle(
        as_of=AS_OF,
        current_snapshot_id="snapshot-current",
        historical_snapshot_ids={"senate-2024-lead-30": "snapshot-history"},
        domains=domains,
    )
    report = {
        "model_version": MODEL_VERSION,
        "ok": True,
        "publishable": True,
        "run_class": "publication",
        "election_id": "senate-2026",
        "as_of": AS_OF,
        # Deliberately old: verification preserves this decision rather than
        # reclassifying it against whatever day a checkpoint is resumed.
        "checked_at": "2026-10-11T00:00:00+00:00",
        "effective_domain_contract": contract,
        "domains": report_domains,
        "evidence_fingerprint": {"manifests": manifest_hashes},
    }
    return report, bundle, contract


def test_sealed_eligibility_preserves_original_freshness_clock():
    report, bundle, contract = _sealed_pair()
    result = verify_sealed_eligibility_report(
        report,
        bundle,
        election_id="senate-2026",
        as_of=AS_OF,
        domain_contract=contract,
        expected_evidence_bundle_id=bundle["evidence_bundle_id"],
    )
    assert result["ok"] is True
    assert result["freshness_clock"] == "frozen_at_evidence_seal"
    assert result["eligibility_checked_at"] == report["checked_at"]


def test_sealed_eligibility_fails_closed_on_receipt_or_domain_change():
    report, bundle, contract = _sealed_pair()
    changed_receipt = deepcopy(report)
    changed_receipt["evidence_fingerprint"]["manifests"][
        "fundraising_shares.json"
    ] = "f" * 64
    receipt_result = verify_sealed_eligibility_report(
        changed_receipt,
        bundle,
        election_id="senate-2026",
        as_of=AS_OF,
        domain_contract=contract,
    )
    assert receipt_result["ok"] is False
    assert any("source receipt differs" in reason for reason in receipt_result["reasons"])

    stale_domain = deepcopy(report)
    stale_domain["domains"]["finance"]["freshness"]["status"] = "stale_retrieval"
    stale_result = verify_sealed_eligibility_report(
        stale_domain,
        bundle,
        election_id="senate-2026",
        as_of=AS_OF,
        domain_contract=contract,
    )
    assert stale_result["ok"] is False
    assert any("was not fresh: finance" in reason for reason in stale_result["reasons"])


def test_sealed_eligibility_rejects_wrong_cutoff_or_bundle_identity():
    report, bundle, contract = _sealed_pair()
    result = verify_sealed_eligibility_report(
        report,
        bundle,
        election_id="senate-2026",
        as_of="2026-10-11",
        domain_contract=contract,
        expected_evidence_bundle_id="eb-wrong",
    )
    assert result["ok"] is False
    assert "sealed evidence bundle identity is invalid" in result["reasons"]
    assert any("as_of differs" in reason for reason in result["reasons"])
