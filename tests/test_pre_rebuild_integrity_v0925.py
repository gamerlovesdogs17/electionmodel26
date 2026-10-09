"""Integrity repairs for v0.9.25 pre-rebuild pass (cheap / deterministic)."""

from __future__ import annotations

import inspect
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from midterms.config import MODEL_VERSION, PREVIOUS_SEALED_MODEL_VERSION, PUBLIC_LIVE_ENABLED
from midterms.evidence.generic_ballot_context import (
    resolve_generic_ballot_context,
    write_generic_ballot_parity_artifact,
)
from midterms.evidence.ingest import (
    _extract_poll_page,
    _two_party_from_answers,
    extract_candidate_level_answers,
    fetch_votehub_polls,
)
from midterms.evidence.race_scoped_identity import (
    build_race_identity_index,
    resolve_race_candidate,
)
from midterms.model.challengers import (
    STATE_SPACE_REFERENCE_CONFIG,
    state_space_challenger_lineage,
)
from midterms.model.contest_classifier import (
    BINARY_NONMAJOR_V_R,
    GENUINE_MULTIWAY_PLURALITY,
    ORDINARY_DVR,
    classify_contest_structure,
)
from midterms.model.state_space import (
    DEFAULT_FUND_PULL,
    DEFAULT_PROCESS_SD_PER_SQRT_DAY,
    fit_state_space,
    process_variance_for_days,
)
from midterms.ops.forecast_artifact_coherence import (
    DEV_STUB_STATUS,
    assert_forecast_latest_coherent,
    write_development_forecast_stub,
)
from midterms.validation.exceptional_loo_diagnostics import leave_one_poll_out_diagnostics
from midterms.validation.historical_multiway_analogs import (
    discover_historical_multiway_plurality_analogs,
)
from midterms.validation.nested_component_loo import (
    _generic_ballot,
    _senate_poll_residual_generic_ballot_forbidden,
)


def test_model_version_bumped_for_probability_affecting_repairs():
    assert MODEL_VERSION == "senate-hierarchical-v0.9.25"
    assert PREVIOUS_SEALED_MODEL_VERSION == "senate-hierarchical-v0.9.23"
    assert PUBLIC_LIVE_ENABLED is False


def test_historical_oof_cannot_derive_gb_from_senate_polls():
    with pytest.raises(RuntimeError, match="forbidden"):
        _senate_poll_residual_generic_ballot_forbidden(None)
    src = inspect.getsource(_generic_ballot)
    assert "two_party_margin" not in src or "prior_lean" not in src
    assert "require_formal_generic_ballot" in src or "resolve_generic_ballot_context" in src


def test_gb_as_of_filtering_rejects_future_polls(tmp_path: Path):
    archive = tmp_path / "historical_generic_ballot_archive.json"
    archive.write_text(
        json.dumps(
            {
                "weighting_method": "test",
                "rows": [
                    {
                        "election_id": "senate-2022",
                        "election_year": 2022,
                        "margin": 2.0,
                        "available_at": "2022-09-01",
                        "field_end": "2022-08-28",
                        "poll_id": "gb-past",
                    },
                    {
                        "election_id": "senate-2022",
                        "election_year": 2022,
                        "margin": 9.0,
                        "available_at": "2022-11-10",
                        "field_end": "2022-11-05",
                        "poll_id": "gb-future",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    import midterms.evidence.generic_ballot_context as gbc

    monkey_path = gbc.HISTORICAL_GB_STORE
    gbc.HISTORICAL_GB_STORE = archive
    try:
        ctx = resolve_generic_ballot_context("senate-2022", "2022-10-01", require_point_in_time=True)
        assert ctx.n_polls == 1
        assert ctx.margin == pytest.approx(2.0)
        assert "gb-future" not in ctx.source_ids
    finally:
        gbc.HISTORICAL_GB_STORE = monkey_path


def test_gb_parity_artifact_documents_eight_cutoffs():
    payload = write_generic_ballot_parity_artifact()
    assert payload["n_cutoffs"] == 8
    assert payload["senate_poll_residual_forbidden"] is True
    assert Path(payload["path"]).is_file()


def test_state_space_no_ed_fund_repull_differs_only_in_fund_pull():
    lineage = state_space_challenger_lineage("state_space_no_ed_fund_repull")
    assert lineage["lineage_ok"] is True
    assert lineage["differing_keys"] == ["fund_pull"]
    assert lineage["challenger_config"]["fund_pull"] == 0.0
    assert STATE_SPACE_REFERENCE_CONFIG["fund_pull"] == DEFAULT_FUND_PULL


def test_calendar_day_process_variance_scales():
    assert process_variance_for_days(0) == 0.0
    assert process_variance_for_days(1) == pytest.approx(DEFAULT_PROCESS_SD_PER_SQRT_DAY**2)
    assert process_variance_for_days(7) == pytest.approx(7 * DEFAULT_PROCESS_SD_PER_SQRT_DAY**2)
    assert process_variance_for_days(30) == pytest.approx(30 * DEFAULT_PROCESS_SD_PER_SQRT_DAY**2)


def test_last_poll_to_as_of_propagation_and_no_duplicate_ed():
    from midterms.evidence.warehouse import EvidenceSnapshot

    as_of = date(2026, 10, 1)
    races = pd.DataFrame(
        [
            {
                "race_id": "senate-2026-IA",
                "state": "IA",
                "region": "Midwest",
                "election_day": "2026-11-03",
                "prior_lean": -5.0,
                "not_up": False,
                "fundraising_share": 0.5,
            }
        ]
    )
    polls = pd.DataFrame(
        [
            {
                "poll_id": "p1",
                "race_id": "senate-2026-IA",
                "field_end": "2026-09-01",
                "two_party_margin": 2.0,
                "sample_size": 800,
                "quality_weight": 1.0,
                "influence_weight": 1.0,
                "mode": None,
                "population": "LV",
                "pollster_id": "Emerson",
            }
        ]
    )
    snap = EvidenceSnapshot(
        as_of=as_of,
        election_id="senate-2026",
        polls=polls,
        races=races,
        results_known=pd.DataFrame(),
        snapshot_id="test-ss-calendar",
    )
    fit = fit_state_space(
        snap,
        n_draws=50,
        seed=1,
        fund_pull=0.0,
        collect_race_diagnostics=True,
        allow_neutral_fill=True,
    )
    diag = fit.diagnostics["race_diagnostics"][0]
    assert diag["last_poll_to_as_of_days"] == 30
    assert diag["process_variance_last_poll_to_as_of"] == pytest.approx(
        process_variance_for_days(30)
    )
    assert fit.diagnostics["as_of_to_ed_uses_future_movement_only"] is True
    assert fit.diagnostics["last_poll_to_as_of_process"] is True


def test_votehub_pagination_consumes_multiple_pages_without_duplicates(tmp_path: Path):
    pages = {
        0: {
            "polls": [{"id": 1, "subject": "2026 Iowa"}, {"id": 2, "subject": "2026 Iowa"}],
            "next_token": "p1",
            "total_count": 5,
        },
        1: {
            "polls": [{"id": 3, "subject": "2026 Kansas"}, {"id": 2, "subject": "2026 Iowa"}],
            "next_token": "p2",
            "total_count": 5,
        },
        2: {
            "polls": [{"id": 4, "subject": "2026 Nebraska"}, {"id": 5, "subject": "2026 Nebraska"}],
            "total_count": 5,
        },
    }
    calls = {"n": 0}

    def fake_get(path, params=None):
        assert path == "/polls"
        idx = calls["n"]
        calls["n"] += 1
        return pages[idx]

    meta = fetch_votehub_polls(client=fake_get, max_pages=10, dest_dir=tmp_path)
    assert meta["n_pages"] == 3
    assert meta["n_polls"] == 5
    assert meta["pagination_terminated_cleanly"] is True
    assert len(set(meta["page_hashes"])) == 3


def test_extract_poll_page_shapes():
    polls, meta = _extract_poll_page([{"id": 1}])
    assert len(polls) == 1
    polls, meta = _extract_poll_page({"polls": [{"id": 1}], "next": "abc"})
    assert meta["next_token"] == "abc"


def test_race_scoped_party_beats_global_and_allows_collisions():
    index = build_race_identity_index(
        [
            {
                "race_id": "senate-2026-MT",
                "ballot_candidates": [
                    {
                        "candidate_id": "senate-2026-MT:kyle-austin",
                        "candidate_name": "Kyle Austin",
                        "ballot_party": "L",
                        "caucus": None,
                    }
                ],
            },
            {
                "race_id": "senate-2026-VA",
                "modeled_candidate_id": "senate-2026-VA:kyle-austin",
                "modeled_candidate_name": "Kyle Austin",
                "modeled_ballot_party": "R",
                "opposing_candidate_id": "senate-2026-VA:mark-warner",
                "opposing_candidate_name": "Mark Warner",
                "opposing_ballot_party": "D",
            },
        ]
    )
    mt = resolve_race_candidate("senate-2026-MT", "Kyle Austin", index=index)
    va = resolve_race_candidate("senate-2026-VA", "Kyle Austin", index=index)
    assert mt is not None and mt.ballot_party == "L"
    assert va is not None and va.ballot_party == "R"
    assert mt.candidate_id != va.candidate_id


def test_unresolved_candidate_name_fails_closed():
    index = build_race_identity_index(
        [
            {
                "race_id": "senate-2026-IA",
                "modeled_candidate_id": "senate-2026-IA:josh-turek",
                "modeled_candidate_name": "Josh Turek",
                "modeled_ballot_party": "D",
                "opposing_candidate_id": "senate-2026-IA:ashley-hinson",
                "opposing_candidate_name": "Ashley Hinson",
                "opposing_ballot_party": "R",
            }
        ]
    )
    assert resolve_race_candidate("senate-2026-IA", "Nobody Special", index=index) is None
    level = extract_candidate_level_answers(
        [{"choice": "Nobody Special", "pct": 10.0}],
        race_id="senate-2026-IA",
        identity_index=index,
    )
    assert level["candidates"][0]["resolution_status"] == "unresolved_fail_closed"
    assert level["candidates"][0]["ballot_party"] is None


def test_candidate_level_multiway_retains_all_and_montana_style():
    index = build_race_identity_index(
        [
            {
                "race_id": "senate-2026-MT",
                "modeled_candidate_id": "senate-2026-MT:seth-bodnar",
                "modeled_candidate_name": "Seth Bodnar",
                "modeled_ballot_party": "I",
                "opposing_candidate_id": "senate-2026-MT:kurt-alme",
                "opposing_candidate_name": "Kurt Alme",
                "opposing_ballot_party": "R",
                "ballot_candidates": [
                    {"candidate_id": "senate-2026-MT:kurt-alme", "candidate_name": "Kurt Alme", "ballot_party": "R"},
                    {"candidate_id": "senate-2026-MT:kyle-austin", "candidate_name": "Kyle Austin", "ballot_party": "L"},
                    {"candidate_id": "senate-2026-MT:alani-bankhead", "candidate_name": "Alani Bankhead", "ballot_party": "D"},
                    {"candidate_id": "senate-2026-MT:seth-bodnar", "candidate_name": "Seth Bodnar", "ballot_party": "I"},
                ],
            }
        ]
    )
    answers = [
        {"choice": "Kurt Alme", "pct": 42.0},
        {"choice": "Seth Bodnar", "pct": 28.0},
        {"choice": "Alani Bankhead", "pct": 18.0},
        {"choice": "Kyle Austin", "pct": 4.0},
        {"choice": "Undecided", "pct": 8.0},
    ]
    level = extract_candidate_level_answers(
        answers, race_id="senate-2026-MT", identity_index=index
    )
    assert level["n_candidates"] == 4
    assert level["multiway"] is True
    parties = {c["ballot_party"] for c in level["candidates"] if c["candidate_id"]}
    assert parties == {"R", "I", "D", "L"}
    derived = _two_party_from_answers(
        answers,
        race_id="senate-2026-MT",
        identity_index=index,
        modeled_candidate_id="senate-2026-MT:seth-bodnar",
        opposing_candidate_id="senate-2026-MT:kurt-alme",
    )
    assert derived is not None
    assert derived["modeled_ballot_party"] == "I"
    assert derived["derived_view"] is True
    # Must not silently select D because D exists.
    assert derived["modeled_candidate"] == "Seth Bodnar"


def test_historical_analog_finder_uses_candidate_level_results():
    payload = discover_historical_multiway_plurality_analogs()
    assert payload["status"] in {"ok", "no_qualifying_analogs", "source_unavailable"}
    if payload["status"] == "ok":
        assert payload["n_analogs"] > 0
        assert all(a["n_candidates"] >= 3 for a in payload["analogs"])


def test_sealed_forecast_stub_is_coherent(tmp_path: Path):
    art = tmp_path / "artifacts"
    art.mkdir()
    sealed = {
        "model_version": PREVIOUS_SEALED_MODEL_VERSION,
        "publishable": True,
        "races": [{"race_id": "senate-2026-IA", "p_win": 0.4}],
    }
    (art / "forecast_latest.json").write_text(json.dumps(sealed), encoding="utf-8")
    stub = write_development_forecast_stub(artifacts_dir=art, also_web_public=False)
    assert stub["status"] == DEV_STUB_STATUS
    assert stub["model_version"] == MODEL_VERSION
    assert stub["races"] == []
    assert (art / "forecast_sealed_v0923_publication.json").is_file()
    # Mutating sealed under old identity is prevented by leaving it untouched.
    sealed_loaded = json.loads((art / "forecast_sealed_v0923_publication.json").read_text())
    assert sealed_loaded["model_version"] == PREVIOUS_SEALED_MODEL_VERSION
    assert_forecast_latest_coherent(artifacts_dir=art)


def test_exceptional_loo_deterministic():
    polls = pd.DataFrame(
        [
            {
                "poll_id": "a",
                "race_id": "senate-2026-NE",
                "modeled_margin": 1.0,
                "sample_size": 500,
                "quality_weight": 1.0,
                "partisan": False,
                "pollster_id": "X",
                "sponsor_id": "none",
                "population": "LV",
                "field_end": "2026-09-01",
            },
            {
                "poll_id": "b",
                "race_id": "senate-2026-NE",
                "modeled_margin": 5.0,
                "sample_size": 600,
                "quality_weight": 1.0,
                "partisan": True,
                "pollster_id": "Y",
                "sponsor_id": "dem",
                "population": "LV",
                "field_end": "2026-09-10",
            },
        ]
    )
    # attach_poll_weights needs field_end relative to as_of
    d1 = leave_one_poll_out_diagnostics(polls, race_id="senate-2026-NE", as_of="2026-10-01")
    d2 = leave_one_poll_out_diagnostics(polls, race_id="senate-2026-NE", as_of="2026-10-01")
    assert d1["eligible"] is True
    assert d1["polls"][0]["delta_posterior_mean"] == d2["polls"][0]["delta_posterior_mean"]
    # Partisan poll must not have identical variance multiplier to neutral.
    by_id = {p["poll_id"]: p for p in d1["polls"]}
    assert by_id["b"]["partisan_variance_multiplier"] > by_id["a"]["partisan_variance_multiplier"]


def test_no_state_specific_probability_override_in_classifier():
    src = inspect.getsource(classify_contest_structure)
    assert 'state == "NE"' not in src
    assert 'state == "MT"' not in src
    mt = classify_contest_structure(
        ballot_candidates=[
            {"ballot_party": "R"},
            {"ballot_party": "I"},
            {"ballot_party": "D"},
            {"ballot_party": "L"},
        ]
    )
    assert mt["category"] == GENUINE_MULTIWAY_PLURALITY
    assert mt["state_identity_used"] is False
    sd = classify_contest_structure(
        ballot_candidates=[{"ballot_party": "I"}, {"ballot_party": "R"}]
    )
    assert sd["category"] == BINARY_NONMAJOR_V_R
    dr = classify_contest_structure(
        ballot_candidates=[{"ballot_party": "D"}, {"ballot_party": "R"}]
    )
    assert dr["category"] == ORDINARY_DVR


def test_release_spec_identity_changes_with_probability_config():
    assert MODEL_VERSION.endswith("v0.9.25")
    assert DEFAULT_FUND_PULL == 0.35
    assert DEFAULT_PROCESS_SD_PER_SQRT_DAY == 0.8
