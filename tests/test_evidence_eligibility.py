"""Evidence eligibility tiers (audit P0.4)."""

from __future__ import annotations

import pytest

from midterms.evidence.eligibility import (
    assert_publishable,
    audit_evidence,
    classify_poll_row,
)


def test_synthetic_poll_classified_blocked():
    assert classify_poll_row({"source_url": "synthetic://fixtures/polls-2026"}) == "synthetic"
    assert classify_poll_row({"parser_version": "fte-senate-polls-v2", "source_url": "https://x"}) == "aggregator"


def test_2026_fixture_polls_are_non_publication():
    rep = audit_evidence(election_id="senate-2026", as_of="2026-09-13")
    assert rep["publishable"] is False
    assert rep["run_class"] == "non_publication"
    assert any("blocked" in r or "synthetic" in r.lower() or "polls" in r for r in rep["reasons"])


def test_2022_fte_polls_are_publication_eligible():
    rep = audit_evidence(election_id="senate-2022", as_of="2022-11-01")
    assert rep["publishable"] is True, rep.get("reasons")
    assert rep["run_class"] == "publication"
    assert (rep["domains"]["polls"].get("blocked_n") or 0) == 0


def test_require_publishable_raises_on_synthetic_2026():
    with pytest.raises(ValueError, match="publication-eligible|ineligible|synthetic|blocked"):
        assert_publishable("senate-2026", as_of="2026-09-13", allow_non_publication=False)
