"""Synthetic arithmetic and vintage tests for the statewide prior builder."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from midterms.evidence.presidential_prior import (
    PRIOR_METHOD, attach_prior_snapshot, derive_state_prior_snapshot,
)
from midterms.evidence.presidential_results import JURISDICTIONS, SOURCES


def _synthetic_counts() -> pd.DataFrame:
    rows = []
    for source in SOURCES:
        for state in sorted(JURISDICTIONS):
            dem, rep = (200, 100) if state == "AL" and source.year in {2016, 2024} else (100, 100)
            rows.append({
                "election_year": source.year, "state": state,
                "dem_votes": dem, "rep_votes": rep,
                "source_sha256": source.sha256,
                "available_at": source.available_at.isoformat(),
            })
    return pd.DataFrame(rows)


def test_synthetic_national_normalization_and_recency():
    snapshot = derive_state_prior_snapshot(
        _synthetic_counts(), as_of=date(2018, 9, 1), source_set_sha256="a" * 64,
    )
    assert snapshot["source_years_newest_first"] == [2016, 2012]
    assert snapshot["recency_weights_newest_first"] == pytest.approx([2 / 3, 1 / 3])
    assert len(snapshot["rows"]) == 50
    row = next(item for item in snapshot["rows"] if item["state"] == "AL")
    synthetic_national = 100 * (5200 - 5100) / (5200 + 5100)
    expected = (2 / 3) * (100 * (200 - 100) / (200 + 100) - synthetic_national)
    assert row["prior_lean"] == pytest.approx(expected)
    assert row["prior_source"] == PRIOR_METHOD
    assert row["prior_production_eligible"] is True
    components = row["provenance"]["provenance"]["components"]
    assert {item["source_election_date"][:4] for item in components} == {"2012", "2016"}
    assert all(item["source_sha256"] and item["source_url"] for item in components)
    assert derive_state_prior_snapshot(
        _synthetic_counts(), as_of=date(2018, 9, 1), source_set_sha256="a" * 64,
    ) == snapshot


@pytest.mark.parametrize(("as_of", "years"), [
    (date(2020, 9, 1), [2016, 2012]),
    (date(2022, 9, 1), [2020, 2016]),
    (date(2024, 9, 1), [2020, 2016]),
    (date(2026, 9, 1), [2024, 2020]),
])
def test_synthetic_prior_vintage_selection(as_of, years):
    snapshot = derive_state_prior_snapshot(
        _synthetic_counts(), as_of=as_of, source_set_sha256="a" * 64,
    )
    assert snapshot["source_years_newest_first"] == years
    for row in snapshot["rows"]:
        assert all(item["available_at"] <= as_of.isoformat()
                   for item in row["provenance"]["provenance"]["components"])


def test_one_source_fallback_is_deterministic_and_nonproduction():
    snapshot = derive_state_prior_snapshot(
        _synthetic_counts(), as_of=date(2014, 9, 1), source_set_sha256="a" * 64,
    )
    assert snapshot["source_years_newest_first"] == [2012]
    assert snapshot["recency_weights_newest_first"] == [1.0]
    assert snapshot["fallback"] == "one_published_source_non_production"
    assert not snapshot["production_eligible"]
    assert not any(row["prior_production_eligible"] for row in snapshot["rows"])


def test_attach_has_matching_snapshot_and_row_provenance():
    snapshot = derive_state_prior_snapshot(
        _synthetic_counts(), as_of=date(2018, 9, 1), source_set_sha256="a" * 64,
    )
    frame = pd.DataFrame([{"state": "AL", "race_id": "synthetic-contest", "prior_lean": 99.0}])
    attached = attach_prior_snapshot(frame, snapshot)
    assert attached.loc[0, "prior_lean"] != 99.0
    assert attached.loc[0, "prior_snapshot_sha256"] == snapshot["snapshot_sha256"]
    altered = json.loads(json.dumps(snapshot))
    altered["rows"][0]["prior_lean"] = 99.0
    with pytest.raises(ValueError, match="fingerprint changed"):
        attach_prior_snapshot(frame, altered)
