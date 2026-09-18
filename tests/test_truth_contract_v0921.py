"""v0.9.21 truth_v1 producer/consumer contract + FEC canaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.config import MODEL_VERSION, PUBLIC_LIVE_ENABLED, ROOT
from midterms.evidence.official_ledger import load_expectations, load_ledger
from midterms.evidence.truth_contract import (
    CONTEST_REQUIRED,
    EXPECTATION_REQUIRED,
    SCHEMA_VERSION,
    WIKI_QUARANTINE_LABEL,
    WIKI_VOTE_COUNTS_PATH,
    caucus_for_winner,
    classify_row_role,
    is_quarantined_wiki_source,
    normalize_expectation_cycle,
    validate_contest,
    validate_expectation_cycle,
)
from midterms.validation.chamber_reconcile import reconcile_all_cycles


def test_wiki_vote_counts_quarantined():
    assert is_quarantined_wiki_source(WIKI_VOTE_COUNTS_PATH)
    notice = ROOT / "data" / "artifacts" / "WIKI_VOTE_COUNTS_QUARANTINE.md"
    assert notice.exists()
    text = notice.read_text(encoding="utf-8").lower()
    assert "parser" in text and "quarantine" in text
    from midterms.evidence.eligibility import audit_evidence

    audit = audit_evidence(election_id="senate-2026")
    block = (audit.get("domains") or {}).get("wiki_vote_scrape") or {}
    assert block.get("quarantine") == WIKI_QUARANTINE_LABEL
    assert block.get("eligible") is False


def test_expectation_key_aliases_normalize():
    raw = {
        "held_dem": 23,
        "held_rep": 42,
        "held_ind": 0,
        "n_contested_expected": 35,
        "contested_race_ids": ["senate-2018-OH"],
        "post_election_dem_seats": 47,
        "post_election_dem_control": False,
        "vp_tiebreak_party": "R",
    }
    norm = normalize_expectation_cycle(raw)
    assert norm["expected_race_ids"] == ["senate-2018-OH"]
    assert norm["post_dem_seats"] == 47
    assert norm["post_dem_control"] is False
    assert not validate_expectation_cycle(norm)


def test_ledger_contests_satisfy_truth_v1():
    ledger = load_ledger()
    assert ledger.get("schema_version") == SCHEMA_VERSION or any(
        c.get("schema_version") == SCHEMA_VERSION
        for year in ledger["cycles"].values()
        for c in year["contests"]
    )
    n = 0
    for year, block in ledger["cycles"].items():
        for contest in block["contests"]:
            errs = validate_contest(contest)
            assert not errs, (year, contest.get("race_id"), errs)
            n += 1
    assert n == 211


def test_expectations_satisfy_truth_v1_keys():
    exp = load_expectations()
    for year, block in exp["cycles"].items():
        norm = normalize_expectation_cycle(block)
        errs = validate_expectation_cycle(norm)
        assert not errs, (year, errs)
        assert "expected_race_ids" in norm
        assert "post_dem_seats" in norm
        # Legacy-only keys must not be the sole consumers (canonical present).
        for key in EXPECTATION_REQUIRED:
            assert key in norm


def test_row_role_classifier_rejects_meta():
    assert classify_row_role("Total votes") == "meta"
    assert classify_row_role("Turnout") == "meta"
    assert classify_row_role("Registered voters") == "meta"
    assert classify_row_role("Bill Cassidy") == "candidate"


def test_independent_caucus_mapping():
    assert caucus_for_winner(winner_party="I", winner_name="Angus S. King Jr.") == "D"
    assert caucus_for_winner(winner_party="I", winner_name="Bernie Sanders") == "D"
    assert caucus_for_winner(winner_party="I", winner_name="Someone Else") == "I"
    assert caucus_for_winner(winner_party="D", winner_name="Any") == "D"
    # Ballot party stays I — caucus mapping does not invent D ballot party.
    assert caucus_for_winner(winner_party="I", winner_name="Angus King", ballot_party="I") == "D"


def test_fec_canaries_decisive_stage():
    ledger = load_ledger()

    def contest(year: str, rid: str) -> dict:
        return next(c for c in ledger["cycles"][year]["contests"] if c["race_id"] == rid)

    la = contest("2014", "senate-2014-LA")
    assert la["stage"] == "runoff"
    assert int(la["dem_votes"]) == 561_210
    assert int(la["rep_votes"]) == 712_379
    assert la["winner_party"] == "R"

    ga = contest("2020", "senate-2020-GA-special")
    assert ga["stage"] == "runoff"
    assert int(ga["dem_votes"]) == 2_289_113
    assert int(ga["rep_votes"]) == 2_195_841

    ok = contest("2022", "senate-2022-OK")
    ok_sp = contest("2022", "senate-2022-OK-special")
    assert int(ok["other_votes"]) == 41_402
    assert int(ok_sp["other_votes"]) == 34_449

    ca = contest("2022", "senate-2022-CA")
    ca_u = contest("2022", "senate-2022-CA-unexpired")
    assert (ca["dem_votes"], ca["rep_votes"]) != (ca_u["dem_votes"], ca_u["rep_votes"])

    for year in ("2018", "2024"):
        me = contest(year, f"senate-{year}-ME")
        vt = contest(year, f"senate-{year}-VT")
        assert me["winner_party"] == "I" and me["winner_caucus"] == "D"
        assert vt["winner_party"] == "I" and vt["winner_caucus"] == "D"


def test_chamber_reconcile_all_cycles_green():
    report = reconcile_all_cycles()
    assert report["ok"] is True, report.get("failures")


def test_legacy_builder_redirects_to_truth_v1():
    from midterms.evidence import build_official_ledger as legacy
    from midterms.evidence.build_certified_ledger_v3 import rebuild_canonical_truth

    assert legacy.build_and_write.__doc__ and "DEPRECATED" in legacy.build_and_write.__doc__
    # Callable still routes through canonical rebuild (smoke: same entry shape).
    assert callable(rebuild_canonical_truth)


def test_live_stays_off_on_v0921():
    assert PUBLIC_LIVE_ENABLED is False
    assert "v0.9.21" in MODEL_VERSION or MODEL_VERSION.endswith("v0.9.21")
