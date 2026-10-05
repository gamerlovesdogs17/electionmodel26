"""Historical model-ready snapshot preparation and audit↔OOF parity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from midterms.evidence.historical_model_snapshot import (
    STRUCTURAL_FEATURE_SCHEMA,
    prepare_current_2026_sanity,
    prepare_formal_fold_snapshot,
    prepare_historical_model_snapshot,
    write_historical_model_feature_parity,
)
from midterms.evidence.warehouse import Warehouse
from midterms.model.personal_incumbency import personal_incumbency_signed
from midterms.validation.validated_model_spec import (
    ORDINARY_STATISTICAL_SPEC_FIELDS,
    ordinary_statistical_spec_identity,
)


@pytest.fixture(scope="module")
def warehouse() -> Warehouse:
    return Warehouse(ensure_fixtures=False)


def _race(snap, race_id: str):
    rows = snap.races[snap.races["race_id"].astype(str).eq(race_id)]
    assert len(rows) == 1, race_id
    return rows.iloc[0]


def test_raw_build_as_of_remains_without_personal_flags(warehouse: Warehouse):
    raw = warehouse.build_as_of("2018-09-07", "senate-2018")
    assert "modeled_candidate_is_incumbent" not in raw.races.columns
    prepared = prepare_historical_model_snapshot(
        raw, election_id="senate-2018", as_of="2018-09-07", lead_days=60,
    )
    assert "modeled_candidate_is_incumbent" in prepared.races.columns
    # Raw snapshot object was not mutated.
    assert "modeled_candidate_is_incumbent" not in raw.races.columns
    assert prepared.snapshot_id == raw.snapshot_id
    assert prepared.historical_structural_feature_sha256


def test_2018_az_open_seat_finance_and_zero_incumbency(warehouse: Warehouse):
    snap = prepare_formal_fold_snapshot(warehouse, year=2018, lead_days=60)
    row = _race(snap, "senate-2018-AZ")
    assert personal_incumbency_signed(row) == 0.0
    assert row["finance_match_status"] in {
        "candidate_matched",
        "candidate_unmatched_neutral",
        "missing_race_specific_finance_neutral",
    }
    assert float(row["fundraising_share"]) == float(row["fundraising_share"])


def test_2018_tx_cruz_personal_incumbency(warehouse: Warehouse):
    snap = prepare_formal_fold_snapshot(warehouse, year=2018, lead_days=60)
    row = _race(snap, "senate-2018-TX")
    assert bool(row["opposing_candidate_is_incumbent"]) is True
    assert personal_incumbency_signed(row) == -1.0


def test_2020_ga_regular_and_special_are_separate(warehouse: Warehouse):
    snap = prepare_formal_fold_snapshot(warehouse, year=2020, lead_days=30)
    regular = _race(snap, "senate-2020-GA")
    special = _race(snap, "senate-2020-GA-special")
    assert regular["race_id"] != special["race_id"]
    assert bool(regular["opposing_candidate_is_incumbent"]) is True  # Perdue
    # Special runoff pairing was not knowable at formal cutoffs — fail closed.
    assert special["personal_incumbency_identity_status"] == "nomination_not_knowable_at_cutoff"
    assert personal_incumbency_signed(special) == 0.0
    assert special["sitting_senator_name"] == "Kelly Loeffler"
    # Finance identities remain race-keyed even if shares happen to match.
    assert "finance_match_status" in regular.index
    assert "finance_match_status" in special.index


def test_2022_ok_regular_and_special_do_not_collide(warehouse: Warehouse):
    snap = prepare_formal_fold_snapshot(warehouse, year=2022, lead_days=60)
    regular = _race(snap, "senate-2022-OK")
    special = _race(snap, "senate-2022-OK-special")
    assert personal_incumbency_signed(regular) == -1.0  # Lankford
    assert personal_incumbency_signed(special) == 0.0  # open Inhofe special


def test_2024_az_open_and_known_incumbent(warehouse: Warehouse):
    snap = prepare_formal_fold_snapshot(warehouse, year=2024, lead_days=60)
    az = _race(snap, "senate-2024-AZ")
    assert personal_incumbency_signed(az) == 0.0
    tx = _race(snap, "senate-2024-TX")
    assert personal_incumbency_signed(tx) == -1.0  # Cruz


def test_no_state_finance_fallback_dual_races(warehouse: Warehouse):
    snap = prepare_formal_fold_snapshot(warehouse, year=2018, lead_days=60)
    mn = _race(snap, "senate-2018-MN")
    mn_s = _race(snap, "senate-2018-MN-special")
    # Distinct race IDs always; shares may differ when both matched.
    assert mn["race_id"] != mn_s["race_id"]
    assert mn.get("finance_match_status")
    assert mn_s.get("finance_match_status")


def test_ordinary_statistical_spec_includes_structural_schema():
    assert "historical_structural_feature_schema" in ORDINARY_STATISTICAL_SPEC_FIELDS
    identity = ordinary_statistical_spec_identity({
        "schema_version": "validated-model-spec-candidate-v1",
        "model_version": "senate-hierarchical-v0.9.23",
        "historical_structural_feature_schema": STRUCTURAL_FEATURE_SCHEMA,
    })
    assert identity["historical_structural_feature_schema"] == STRUCTURAL_FEATURE_SCHEMA


def test_current_2026_sanity_checks(warehouse: Warehouse):
    payload = prepare_current_2026_sanity(warehouse)
    checks = payload["checks"]
    assert checks["TX"]["personal_incumbency"] == 0.0
    assert checks["LA"]["personal_incumbency"] == 0.0
    assert checks["NE"]["personal_incumbency"] == -1.0
    assert checks["NM"]["personal_incumbency"] == 1.0
    assert checks["AK"]["opposing_candidate_is_incumbent"] is True
    assert checks["FL"]["opposing_candidate_is_incumbent"] is True
    assert checks["OH"]["opposing_candidate_is_incumbent"] is True


def test_full_parity_artifact_matches_audits(warehouse: Warehouse, tmp_path: Path):
    out = tmp_path / "parity.json"
    payload = write_historical_model_feature_parity(warehouse=warehouse, out_path=out)
    assert payload["status"] == "parity_ok"
    assert payload["n_parity_mismatches"] == 0
    assert payload["n_folds"] == 8
    assert payload["decision_gate"].startswith("HISTORICAL MODEL INPUTS CHANGED")
    assert out.is_file()
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk["aggregate_historical_structural_features_sha256"]
    for fold in disk["folds"]:
        assert fold["n_active_races"] >= 33
        assert fold["historical_structural_feature_sha256"]
