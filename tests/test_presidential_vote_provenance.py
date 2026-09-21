"""Point-in-time source and parser integrity tests; no derived partisan values."""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pandas as pd
import pytest

from midterms.evidence.eligibility import audit_structural_prior
from midterms.evidence.presidential_results import (
    PARSER_VERSION, RAW_SOURCE_DIR, SOURCES, build_vote_count_store,
    select_source_years, verified_source_set_sha256,
)
from midterms.evidence.warehouse import Warehouse


@pytest.mark.parametrize(("as_of", "years"), [
    (date(2018, 9, 1), (2016, 2012)),
    (date(2020, 9, 1), (2016, 2012)),
    (date(2022, 9, 1), (2020, 2016)),
    (date(2024, 9, 1), (2020, 2016)),
    (date(2026, 9, 1), (2024, 2020)),
])
def test_source_vintage_selection(as_of, years):
    assert select_source_years(as_of) == years
    assert select_source_years(as_of) == years


def test_source_not_available_on_election_night():
    assert 2024 not in select_source_years(date(2024, 11, 6))
    assert 2020 not in select_source_years(date(2020, 11, 4))
    assert select_source_years(date(2014, 9, 1)) == (2012,)
    assert select_source_years(date(2012, 11, 7)) == ()


def test_verified_fec_vote_count_parser_and_manifest(tmp_path):
    store = tmp_path / "counts.parquet"
    manifest_path = tmp_path / "sources.json"
    manifest = build_vote_count_store(
        raw_dir=RAW_SOURCE_DIR, out_path=store, manifest_path=manifest_path,
    )
    assert manifest["parser_version"] == PARSER_VERSION
    assert manifest["derived_prior_status"] == "not_computed"
    assert {row["year"] for row in manifest["source_blocks"]} == {2012, 2016, 2020, 2024}
    for source in SOURCES:
        assert hashlib.sha256((RAW_SOURCE_DIR / source.filename).read_bytes()).hexdigest() == source.sha256
    frame = pd.read_parquet(store)
    assert len(frame) == 4 * 51
    assert frame.groupby("election_year")["state"].nunique().eq(51).all()
    assert not frame.loc[frame["state"].eq("DC"), "prior_eligible_state"].any()
    assert frame.loc[frame["state"].isin(["ME", "NE"])].groupby(
        ["election_year", "state"]
    ).size().eq(1).all()
    assert verified_source_set_sha256(
        raw_dir=RAW_SOURCE_DIR, manifest_path=manifest_path, vote_store_path=store,
    ) == manifest["source_set_sha256"]
    altered = json.loads(manifest_path.read_text())
    altered["source_set_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="fingerprint is stale"):
        verified_source_set_sha256(
            raw_dir=RAW_SOURCE_DIR, manifest_path=manifest_path, vote_store_path=store,
        )
    manifest_path.write_text(json.dumps(manifest))
    store.write_bytes(store.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="normalized vote store is stale"):
        verified_source_set_sha256(
            raw_dir=RAW_SOURCE_DIR, manifest_path=manifest_path, vote_store_path=store,
        )


@pytest.mark.parametrize("source_label", ["synthetic_fixture", "randomized_fixture",
                                         "legacy_unverified_fixture"])
def test_fixture_prior_is_not_publication_eligible(source_label):
    fixture = pd.DataFrame([{"race_id": "synthetic-one", "not_up": False,
                             "prior_source": source_label,
                             "prior_provenance_sha256": "a" * 64,
                             "prior_production_eligible": False}])
    assert not audit_structural_prior(fixture)["eligible"]
    fixture["prior_source"] = "observed_presidential_relative_v1"
    fixture["prior_production_eligible"] = True
    assert audit_structural_prior(fixture)["eligible"]
    fixture["prior_provenance_sha256"] = "stale"
    assert not audit_structural_prior(fixture)["eligible"]


def test_warehouse_attaches_identity_and_labels_unverified_prior(tmp_path, monkeypatch):
    from midterms.evidence import tickets

    monkeypatch.setattr(tickets, "TICKETS_2026", {"ZZ": {
        "dem_name": "Avery Cedar", "dem_party": "I", "rep_name": "Blair Birch",
        "modeled_caucus": None, "modeled_caucus_basis": None,
        "opposing_caucus": "caucus_b", "opposing_caucus_basis": "declared_assumption",
    }})
    warehouse = Warehouse.__new__(Warehouse)
    warehouse.normalized_dir = tmp_path
    warehouse.polls = pd.DataFrame([{
        "poll_id": "synthetic-poll", "election_id": "senate-2026",
        "available_at": "2026-01-01", "exclusion_status": "include",
    }])
    warehouse.races = pd.DataFrame([{
        "race_id": "senate-2026-ZZ", "election_id": "senate-2026",
        "state": "ZZ", "not_up": False,
    }])
    warehouse.results = pd.DataFrame()
    monkeypatch.setattr(warehouse, "_attach_poll_priors", lambda polls, as_of: (polls, {}))
    snapshot = warehouse.build_as_of("2026-02-01", "senate-2026")
    row = snapshot.races.iloc[0]
    assert row["modeled_ballot_party"] == "I"
    assert pd.isna(row["modeled_caucus"])
    assert row["opposing_caucus_basis"] == "declared_assumption"
    assert row["prior_source"] == "legacy_unverified_fixture"
    assert snapshot.presidential_source_sha256 is None
