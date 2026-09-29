"""Cheap candidate-state leakage and binary-score contract tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from midterms.evidence.candidate_timeline import (
    apply_candidate_state_contract,
    filter_candidate_state_score_exclusions,
)
from midterms.evidence.source_readiness import audit_source_readiness
from midterms.evidence.warehouse import evidence_snapshot_fingerprint

HASH = "a" * 64


def _race(
    race_id: str = "senate-2026-AA", *, state: str = "AA", election_id: str = "senate-2026",
) -> pd.DataFrame:
    return pd.DataFrame([{
        "election_id": election_id,
        "race_id": race_id,
        "state": state,
        "not_up": False,
        "election_phase": "general",
        "ballot_status": "nominated",
    }])


def _event(
    *, race_id: str, election_id: str, side: str, candidate: str,
    party: str, available_at: str, event_type: str = "nomination",
) -> dict:
    slug = candidate.lower().replace(" ", "-")
    return {
        "election_id": election_id,
        "event_id": f"{race_id}:{side}:{event_type}:{available_at}:{slug}",
        "race_id": race_id,
        "candidate_id": f"{race_id}:{slug}",
        "candidate_name": candidate,
        "modeled_side": side,
        "event_type": event_type,
        "effective_at": available_at,
        "available_at": available_at,
        "retrieved_at": "2026-09-29T00:00:00Z",
        "ballot_party": party,
        "ballot_status": "qualified",
        "source_url": "https://example.test/official-candidates",
        "source_tier": "official",
        "source_object_sha256": HASH,
        "source_hash": HASH,
        "parser_version": "synthetic-candidate-v1",
        "valid_from": available_at,
    }


def _pair(
    race_id: str, election_id: str, available_at: str,
    *, modeled: str = "Modeled Candidate", opposing: str = "Opposing Candidate",
    modeled_party: str = "DEM", opposing_party: str = "REP",
) -> pd.DataFrame:
    return pd.DataFrame([
        _event(
            race_id=race_id, election_id=election_id, side="modeled",
            candidate=modeled, party=modeled_party, available_at=available_at,
        ),
        _event(
            race_id=race_id, election_id=election_id, side="opposing",
            candidate=opposing, party=opposing_party, available_at=available_at,
        ),
    ])


def _poll(
    poll_id: str, race_id: str, available_at: str,
    dem: str, rep: str, *, hypothetical: bool = False,
) -> dict:
    return {
        "poll_id": poll_id,
        "race_id": race_id,
        "available_at": available_at,
        "exclusion_status": "include",
        "dem_candidate_id": f"d:{dem}",
        "dem_candidate_name": dem,
        "rep_candidate_id": f"r:{rep}",
        "rep_candidate_name": rep,
        "matchup_id": f"{dem}|{rep}",
        "candidate_set_version": f"set:{dem}|{rep}",
        "hypothetical": hypothetical,
    }


def test_ordinary_side_only_race_passes_without_exact_candidate_ids():
    applied, polls, meta = apply_candidate_state_contract(
        _race(), pd.DataFrame(), pd.DataFrame(), as_of="2026-09-27",
    )
    assert applied.loc[0, "candidate_state"] == "side_only_stable"
    assert pd.isna(applied.loc[0, "modeled_candidate_id"])
    assert meta["publication_eligible"] is True
    assert meta["counts"]["identity_required_and_missing"] == 0
    assert polls.empty


def test_future_nominee_and_candidate_poll_do_not_leak_into_pre_primary_snapshot():
    race_id = "senate-2022-NH"
    races = _race(race_id, state="NH", election_id="senate-2022")
    timeline = _pair(race_id, "senate-2022", "2022-09-13")
    polls = pd.DataFrame([
        _poll("known-before-primary", race_id, "2022-09-01", "Future D", "Future R"),
        _poll("published-after-cutoff", race_id, "2022-09-10", "Future D", "Future R"),
    ])
    gap = [{
        "race_id": race_id,
        "gap_type": "nomination_not_yet_determined",
        "event_date": "2022-09-13",
        "reason": "primary follows cutoff",
    }]
    applied, safe, meta = apply_candidate_state_contract(
        races, timeline, polls, as_of="2022-09-09", structural_gaps=gap,
    )
    assert applied.loc[0, "candidate_state"] == "structurally_unresolved"
    assert pd.isna(applied.loc[0, "modeled_candidate_id"])
    assert safe.empty
    assert [row["poll_id"] for row in meta["poll_exclusions"]] == [
        "known-before-primary"
    ]
    _, _, without_future = apply_candidate_state_contract(
        races, timeline, polls.iloc[:1], as_of="2022-09-09", structural_gaps=gap,
    )
    assert meta["snapshot_sha256"] == without_future["snapshot_sha256"]


def test_withdrawal_blocks_until_source_backed_replacement_resolves_identity():
    race_id = "senate-2026-AA"
    base = _pair(race_id, "senate-2026", "2026-06-01")
    withdrawal = _event(
        race_id=race_id, election_id="senate-2026", side="modeled",
        candidate="Modeled Candidate", party="DEM", available_at="2026-07-01",
        event_type="withdrawal",
    )
    unresolved, _, meta = apply_candidate_state_contract(
        _race(race_id), pd.concat([base, pd.DataFrame([withdrawal])], ignore_index=True),
        as_of="2026-07-02",
    )
    assert unresolved.loc[0, "candidate_state"] == "identity_required"
    assert unresolved.loc[0, "candidate_identity_resolved"] is False
    assert meta["publication_eligible"] is False

    replacement = _event(
        race_id=race_id, election_id="senate-2026", side="modeled",
        candidate="Replacement Candidate", party="DEM", available_at="2026-07-03",
        event_type="replacement",
    )
    resolved, _, meta = apply_candidate_state_contract(
        _race(race_id),
        pd.concat([base, pd.DataFrame([withdrawal, replacement])], ignore_index=True),
        as_of="2026-07-04",
    )
    assert resolved.loc[0, "candidate_identity_resolved"] is True
    assert resolved.loc[0, "modeled_candidate_name"] == "Replacement Candidate"
    assert meta["publication_eligible"] is True


def test_vacancy_or_death_requires_a_later_source_backed_identity_event():
    race_id = "senate-2026-AA"
    base = _pair(race_id, "senate-2026", "2026-06-01")
    for event_type in ("vacancy", "death"):
        transition = _event(
            race_id=race_id, election_id="senate-2026", side="modeled",
            candidate="Modeled Candidate", party="DEM", available_at="2026-07-01",
            event_type=event_type,
        )
        applied, _, meta = apply_candidate_state_contract(
            _race(race_id),
            pd.concat([base, pd.DataFrame([transition])], ignore_index=True),
            as_of="2026-07-02",
        )
        assert applied.loc[0, "candidate_state"] == "identity_required"
        assert applied.loc[0, "candidate_identity_resolved"] is False
        assert meta["publication_eligible"] is False


def test_ambiguous_matchups_are_removed_without_eventual_nominee_selection():
    race_id = "senate-2026-AA"
    polls = pd.DataFrame([
        _poll("p1", race_id, "2026-08-01", "D One", "R One"),
        _poll("p2", race_id, "2026-08-02", "D One", "R Two"),
    ])
    applied, safe, meta = apply_candidate_state_contract(
        _race(race_id), pd.DataFrame(), polls, as_of="2026-09-01",
    )
    assert applied.loc[0, "candidate_state"] == "structurally_unresolved"
    assert safe.empty
    assert meta["publication_eligible"] is True
    assert meta["ambiguous_poll_matchups"][0]["race_id"] == race_id
    assert {row["poll_id"] for row in meta["poll_exclusions"]} == {"p1", "p2"}


def test_nonbinary_special_is_explicitly_excluded_from_binary_scoring():
    race_id = "senate-2020-GA-special"
    gap = [{
        "race_id": race_id,
        "gap_type": "special_election_finalists_not_yet_determined",
        "event_date": "2020-11-03",
        "reason": "runoff pair follows cutoff",
    }]
    applied, _, meta = apply_candidate_state_contract(
        _race(race_id, state="GA", election_id="senate-2020"),
        pd.DataFrame(), pd.DataFrame([_poll("p", race_id, "2020-09-01", "D", "R")]),
        as_of="2020-10-04", structural_gaps=gap,
    )
    assert applied.loc[0, "candidate_state"] == "ineligible_for_binary_scoring"
    assert applied.loc[0, "binary_score_eligible"] is False
    results = pd.DataFrame([{"race_id": race_id, "score_eligible": True}])
    filtered, exclusions = filter_candidate_state_score_exclusions(results, meta)
    assert filtered.empty
    assert exclusions == [{
        "race_id": race_id, "reason": "final_binary_pairing_not_yet_determined",
    }]


def test_california_official_timeline_overrides_side_only_and_is_fingerprinted():
    race_id = "senate-2024-CA"
    timeline = _pair(
        race_id, "senate-2024", "2024-08-29",
        modeled="Adam Example", opposing="Steve Example",
    )
    races = _race(race_id, state="CA", election_id="senate-2024")
    before, _, before_meta = apply_candidate_state_contract(
        races, timeline, as_of="2024-08-28",
    )
    after, _, after_meta = apply_candidate_state_contract(
        races, timeline, as_of="2024-08-29",
    )
    assert before.loc[0, "candidate_state"] == "identity_required"
    assert before_meta["publication_eligible"] is False
    assert after.loc[0, "candidate_identity_resolved"] is True
    assert after_meta["publication_eligible"] is True
    assert before_meta["snapshot_sha256"] != after_meta["snapshot_sha256"]


def test_candidate_classification_change_changes_snapshot_hash():
    races = _race("senate-2020-GA-special", state="GA", election_id="senate-2020")
    _, _, ordinary = apply_candidate_state_contract(
        races, pd.DataFrame(), as_of="2020-10-04",
    )
    _, _, excluded = apply_candidate_state_contract(
        races,
        pd.DataFrame(),
        as_of="2020-10-04",
        structural_gaps=[{
            "race_id": "senate-2020-GA-special",
            "gap_type": "special_election_finalists_not_yet_determined",
        }],
    )
    assert ordinary["snapshot_sha256"] != excluded["snapshot_sha256"]
    empty = pd.DataFrame()
    snapshot_a, _ = evidence_snapshot_fingerprint(
        election_id="senate-2020", as_of="2020-10-04",
        polls=empty, races=races, candidate_timeline=ordinary,
        pollster_ratings=None, prior_snapshot_sha256=None,
        presidential_source_sha256=None,
    )
    snapshot_b, _ = evidence_snapshot_fingerprint(
        election_id="senate-2020", as_of="2020-10-04",
        polls=empty, races=races, candidate_timeline=excluded,
        pollster_ratings=None, prior_snapshot_sha256=None,
        presidential_source_sha256=None,
    )
    assert snapshot_a != snapshot_b


def test_source_readiness_candidate_domain_green_for_materially_safe_states(tmp_path: Path):
    normalized = tmp_path / "normalized"
    manifests = tmp_path / "manifests"
    raw = tmp_path / "raw"
    normalized.mkdir(); manifests.mkdir(); raw.mkdir()
    races = pd.concat([
        _race(f"senate-{year}-AA", election_id=f"senate-{year}")
        for year in (2018, 2020, 2022, 2024, 2026)
    ], ignore_index=True)
    races.to_parquet(normalized / "races_official.parquet", index=False)
    report = audit_source_readiness(
        election_id="senate-2026",
        as_of="2026-09-27",
        normalized_dir=normalized,
        manifests_dir=manifests,
        raw_dir=raw,
        environ={},
    )
    candidate = report["domains"]["candidate_timeline"]
    assert candidate["status"] == "ready"
    assert candidate["missing_coverage"] == []
    assert all(
        cutoff["n_side_only_stable"] == 1
        for cutoff in candidate["cutoffs"].values()
    )
