"""Evidence eligibility tiers (audit P0.4 / fresh audit R-04)."""

from __future__ import annotations

import pytest

from midterms.evidence.eligibility import (
    assert_publishable,
    audit_evidence,
    classify_poll_row,
    _classify_manifest_domain,
)


def test_synthetic_poll_classified_blocked():
    assert classify_poll_row({"source_url": "synthetic://fixtures/polls-2026"}) == "synthetic"
    assert classify_poll_row({"parser_version": "fte-senate-polls-v2", "source_url": "https://x"}) == "aggregator"


def test_fixture_hash_finance_blocked():
    blocked = _classify_manifest_domain(
        name="finance",
        manifest={"source_mix": {"fixture_hash": 35}, "n_shares": 35},
    )
    assert blocked["eligible"] is False
    assert blocked["tier"] == "synthetic"


def test_2026_live_evidence_domains_enumerated():
    rep = audit_evidence(election_id="senate-2026", as_of="2026-09-13")
    assert "finance" in rep["domains"]
    assert "economics" in rep["domains"]
    assert "approval" in rep["domains"]
    # VoteHub + FRED/curated path may be publication-eligible
    if rep["publishable"]:
        assert rep["run_class"] == "publication"
        assert (rep["domains"]["polls"].get("blocked_n") or 0) == 0
    else:
        assert rep["run_class"] == "non_publication"
        assert rep["reasons"]


def test_2022_fte_polls_are_publication_eligible():
    rep = audit_evidence(election_id="senate-2022", as_of="2022-11-01")
    assert rep["publishable"] is True, rep.get("reasons")
    assert rep["run_class"] == "publication"
    assert (rep["domains"]["polls"].get("blocked_n") or 0) == 0


def test_require_publishable_respects_eligibility():
    rep = audit_evidence(election_id="senate-2026", as_of="2026-09-13")
    if rep["publishable"]:
        out = assert_publishable("senate-2026", as_of="2026-09-13", allow_non_publication=False)
        assert out["publishable"] is True
    else:
        with pytest.raises(ValueError, match="publication-eligible|ineligible|synthetic|blocked"):
            assert_publishable("senate-2026", as_of="2026-09-13", allow_non_publication=False)
