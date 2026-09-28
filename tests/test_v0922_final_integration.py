"""Cheap synthetic contracts for the final v0.9.22 pre-rebuild integration."""

from __future__ import annotations

import ast
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from midterms.config import MODEL_VERSION
from midterms.evidence.approval import (
    aggregate_historical_approval_cutoffs,
    parse_historical_approval_archive,
)
from midterms.evidence.candidate_timeline import apply_candidate_timeline
from midterms.evidence.demographic_vintages import (
    attach_official_urban_features,
    census_demographic_policy,
    transform_acs_state_features,
)
from midterms.evidence.fec import (
    resolve_form3_reports_as_of,
    select_candidate_committee_reports_as_of,
)
from midterms.evidence.ratings import VERIFIED_VENDORED_RATING_SNAPSHOTS
from midterms.model.pymc_model import FitResult
from midterms.model.poll_structure import PollStructureConfig
from midterms.validation import nested_component_loo as nested
from midterms.validation.validated_model_spec import (
    CANONICAL_OOF_PHASE,
    finalize_validated_model_spec,
    load_validated_model_spec,
    poll_structure_identity,
    write_candidate_model_spec,
)


def test_approval_archive_cutoff_excludes_future_poll(tmp_path: Path):
    source = tmp_path / "approval.csv"
    source.write_text(
        "president,poll_start,poll_end,polling_institute,approval,disapproval,sample_size\n"
        "Donald Trump,2018-08-01,2018-08-03,A,45,50,1000\n"
        "Donald Trump,2018-09-20,2018-09-22,B,70,20,1000\n",
        encoding="utf-8",
    )
    polls = parse_historical_approval_archive(source)
    report = aggregate_historical_approval_cutoffs(
        polls,
        cutoffs={"senate-2018-lead-60": "2018-09-08"},
        raw_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        window_days=60,
    )
    assert report.loc[0, "n_polls"] == 1
    assert report.loc[0, "net_approval"] == -5.0
    assert report.loc[0, "production_eligible"] == False  # noqa: E712
    assert "publication timestamp" in report.loc[0, "production_ineligible_reason"]


def test_checked_in_approval_archive_hash_matches_manifest_bytes():
    raw = Path("data/raw/external/historical_presidential_approval_polls_1937_2024.csv")
    manifest = json.loads(Path("data/manifests/pres_approval.json").read_text())
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == manifest["historical_raw_sha256"]


def _acs_row() -> pd.DataFrame:
    row = {"NAME": "Alabama", "state": "01", "B15003_001E": 1000,
           "B03002_001E": 1000, "B03002_003E": 600, "B01001_001E": 1000}
    for number in range(22, 26):
        row[f"B15003_{number:03d}E"] = 100
    for number in (*range(20, 26), *range(44, 50)):
        row[f"B01001_{number:03d}E"] = 15
    return pd.DataFrame([row])


def test_census_features_and_release_policy_are_cutoff_safe():
    features = transform_acs_state_features(_acs_row())
    assert features.loc[0, "state"] == "AL"
    assert features.loc[0, "college"] == pytest.approx(0.4)
    assert features.loc[0, "nonwhite"] == pytest.approx(0.4)
    urban = pd.DataFrame([
        {"state": "AL", "classification_year": 2010, "urban_population": 600, "total_population": 1000},
        {"state": "AL", "classification_year": 2020, "urban_population": 800, "total_population": 1000},
    ])
    old = attach_official_urban_features(features, urban, classification_year=2010)
    new = attach_official_urban_features(features, urban, classification_year=2020)
    assert old.loc[0, "urban"] == pytest.approx(0.6)
    assert new.loc[0, "urban"] == pytest.approx(0.8)
    assert census_demographic_policy(2022)["urban_year"] == 2010
    assert census_demographic_policy(2024)["urban_year"] == 2020
    assert census_demographic_policy(2026)["release_date"] == "2026-01-29"


def _reports() -> pd.DataFrame:
    base = {
        "committee_id": "C1", "report_type": "Q3", "coverage_start_date": "2022-07-01",
        "coverage_end_date": "2022-09-30", "disbursements": 4.0,
        "cash_on_hand_end_period": 3.0,
    }
    return pd.DataFrame([
        {**base, "receipt_date": "2022-10-10", "filing_id": "100", "amendment_indicator": "N", "receipts": 10.0},
        {**base, "receipt_date": "2022-10-25", "filing_id": "101", "amendment_indicator": "A", "receipts": 20.0},
    ])


def test_fec_receipt_date_controls_amendment_visibility():
    early = resolve_form3_reports_as_of(_reports(), as_of="2022-10-20")
    late = resolve_form3_reports_as_of(_reports(), as_of="2022-10-30")
    assert early["filing_id"].tolist() == ["100"]
    assert late["filing_id"].tolist() == ["101"]
    assert early.loc[0, "available_at"] == "2022-10-10"
    links = pd.DataFrame([{
        "candidate_id": "S1", "committee_id": "C1", "state": "ZZ", "party": "DEM",
        "available_at": "2022-01-01", "is_authorized": True,
    }])
    first = select_candidate_committee_reports_as_of(_reports(), links, as_of="2022-10-20")
    second = select_candidate_committee_reports_as_of(_reports(), links, as_of="2022-10-20")
    pd.testing.assert_frame_equal(first, second)


def test_fec_filer_event_cannot_satisfy_ballot_identity():
    races = pd.DataFrame([{"race_id": "r1", "not_up": False}])
    event = pd.DataFrame([{
        "race_id": "r1", "event_id": "e1", "candidate_id": "c1",
        "modeled_side": "modeled", "event_type": "declared",
        "effective_at": "2022-01-01", "available_at": "2022-01-02",
        "candidate_name": "Synthetic", "ballot_party": "I",
    }])
    applied, meta = apply_candidate_timeline(races, event, as_of="2022-10-01")
    assert applied.loc[0, "candidate_timeline_status"] == "degraded_filer_or_nonballot_event"
    assert meta["production_eligible"] is False


def test_pollster_content_vintage_is_not_availability_date():
    assert VERIFIED_VENDORED_RATING_SNAPSHOTS["2018"]["available_at"] == "2019-11-05"
    assert VERIFIED_VENDORED_RATING_SNAPSHOTS["2020"]["available_at"] == "2021-03-19"
    assert VERIFIED_VENDORED_RATING_SNAPSHOTS["2023"]["available_at"] == "2024-01-25"


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_validated_model_spec_binds_selected_structure_and_rejects_stale_stack(tmp_path: Path):
    selection = _write_json(tmp_path / "selection.json", {
        "model_version": MODEL_VERSION,
        "source_nested_loo_sha256": "1" * 64,
        "source_validation_phase": "poll_structure_selection",
        "source_evidence_bundle_id": "eb-test",
        "source_evidence_bundle_sha256": "2" * 64,
        "freeze_before_truth": True,
        "outer_folds": [
            {"outer_heldout_cycle": year, "heldout_truth_used_for_selection": False}
            for year in (2018, 2020, 2022, 2024)
        ],
        "final_production_candidate_recommendation": {
            "selected_structure": "hier_plus_study_effect",
            "selected_poll_structure_id": poll_structure_identity(
                PollStructureConfig(study_effect=True)
            ),
        },
    })
    bundle = _write_json(tmp_path / "bundle.json", {
        "model_version": MODEL_VERSION, "evidence_bundle_id": "eb-test",
        "evidence_bundle_sha256": "2" * 64,
    })
    readiness = _write_json(tmp_path / "readiness.json", {
        "model_version": MODEL_VERSION, "ready_for_expensive_rebuild": True,
    })
    candidate_path = tmp_path / "candidate.json"
    candidate = write_candidate_model_spec(
        selection_path=selection, evidence_bundle_path=bundle,
        source_readiness_path=readiness, out_path=candidate_path,
    )
    assert candidate["selected_poll_structure"]["study_effect"] is True
    config_id = candidate["selected_poll_structure_id"]
    canonical_path = _write_json(tmp_path / "canonical.json", {
        "model_version": MODEL_VERSION, "validation_phase": CANONICAL_OOF_PHASE,
        "poll_structure_config_id": config_id, "evidence_bundle_id": "eb-test",
        "evidence_bundle_sha256": "2" * 64,
        "model_spec_candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "frozen_draws_sha256": "3" * 64, "failures": [],
    })
    canonical_sha = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
    stack_path = _write_json(tmp_path / "stack.json", {
        "source_model_version": MODEL_VERSION, "source_nested_sha256": canonical_sha,
        "source_poll_structure_config_id": config_id, "source_evidence_bundle_id": "eb-test",
        "source_evidence_bundle_sha256": "2" * 64,
        "source_model_spec_candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "stack_weights_production": {"pymc": 1.0}, "reproduction": {"ok": True},
    })
    calibration_path = _write_json(tmp_path / "calibration.json", {
        "model_version": MODEL_VERSION, "source": {"frozen_draws_sha256": "3" * 64},
    })
    latest = tmp_path / "latest.json"
    finalize_validated_model_spec(
        candidate_path=candidate_path, canonical_oof_path=canonical_path,
        stack_path=stack_path, calibration_path=calibration_path, out_path=latest,
    )
    loaded, config = load_validated_model_spec(
        path=latest, expected_evidence_bundle_id="eb-test",
        expected_evidence_bundle_sha256="2" * 64,
    )
    assert config.study_effect is True
    assert loaded["selected_poll_structure_id"] == poll_structure_identity(config)
    with pytest.raises(ValueError, match="evidence bundle ID"):
        load_validated_model_spec(path=latest, expected_evidence_bundle_id="eb-stale")
    stack = json.loads(stack_path.read_text())
    stack["source_poll_structure_config_id"] = "psc-stale"
    stack_path.write_text(json.dumps(stack))
    with pytest.raises(ValueError, match="stack_poll_structure"):
        finalize_validated_model_spec(
            candidate_path=candidate_path, canonical_oof_path=canonical_path,
            stack_path=stack_path, calibration_path=calibration_path,
            out_path=latest,
        )


def test_every_run_forecast_pymc_call_receives_selected_poll_structure():
    source = Path("midterms/pipeline/run_forecast.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id in {"fit_pymc", "fit_pymc_dynamic"}]
    assert len(calls) >= 5
    assert all(any(keyword.arg == "poll_structure" for keyword in call.keywords) for call in calls)


def test_canonical_oof_passes_poll_structure_to_static_and_dynamic(monkeypatch):
    seen: list[PollStructureConfig] = []

    def fit(method: str):
        return FitResult(
            race_ids=["r"], states=["ZZ"], mean_margin=np.array([0.0]),
            sd_margin=np.array([1.0]), draws_margin=np.array([[-1.0], [1.0]]),
            house_effects={}, diagnostics={"convergence": {"available": True,
            "r_hat_max": 1.0, "ess_bulk_min_frac": 1.0}}, method=method,
        )

    config = PollStructureConfig(study_effect=True)
    monkeypatch.setattr(nested, "_fit_hierarchical", lambda *args, poll_structure, **kwargs: (
        seen.append(PollStructureConfig.coerce(poll_structure)) or fit("pymc")
    ))
    monkeypatch.setattr(nested, "fit_pymc_dynamic", lambda *args, poll_structure, **kwargs: (
        seen.append(PollStructureConfig.coerce(poll_structure)) or fit("pymc_dynamic")
    ))
    monkeypatch.setattr(nested, "fit_state_space", lambda *args, **kwargs: fit("state_space"))
    monkeypatch.setattr(nested, "fit_poll_only_state_space", lambda *args, **kwargs: fit("state_space"))
    monkeypatch.setattr(nested, "fit_ridge_fundamentals", lambda *args, **kwargs: fit("ridge_fundamentals"))
    monkeypatch.setattr(nested, "BASELINES", {})
    snap = SimpleNamespace(as_of=date(2022, 1, 1), polls=pd.DataFrame(),
                           snapshot_id="s", prior_snapshot_sha256="p",
                           presidential_source_sha256="q")
    nested.freeze_component_predictions(
        snap, election_id="synthetic", holdout_year=2022, lead_days=60,
        hierarchical_method="pymc", n_draws=2, poll_structure=config,
        include_structural_challengers=False,
    )
    assert seen and all(value == config for value in seen)
