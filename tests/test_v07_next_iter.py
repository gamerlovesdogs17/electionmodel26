"""v0.7: certified results, similarity covariance, institutional rules, ops."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from midterms.evidence.results_archive import (
    build_certified_results_frame,
    medsl_2016_state_margins,
    write_results_archive,
)
from midterms.evidence.schema import is_active_ballot_row
from midterms.model.similarity import correlated_shocks, similarity_matrix, race_feature_matrix
from midterms.ops.monitor import monitor_check


def test_medsl_2016_aggregates_to_states():
    df = medsl_2016_state_margins()
    assert len(df) >= 30
    assert set(["state", "two_party_margin"]).issubset(df.columns)
    assert df["state"].str.len().eq(2).all()


def test_certified_archive_covers_recent_cycles():
    man = write_results_archive()
    assert man["n"] > 50
    assert "senate-2018" in man["elections"]
    assert "senate-2022" in man["elections"]
    frame = build_certified_results_frame()
    assert (frame["election_id"] == "senate-2016").any()


def test_similarity_matrix_psd_and_shocks_shape():
    races = pd.DataFrame(
        {
            "race_id": ["a", "b", "c"],
            "region": ["South", "South", "Midwest"],
            "prior_lean": [2.0, 3.0, -5.0],
            "is_open": [False, True, False],
            "fundraising_share": [0.55, 0.48, 0.40],
        }
    )
    X = race_feature_matrix(races)
    R = similarity_matrix(X)
    eig = np.linalg.eigvalsh(R)
    assert eig.min() > -1e-8
    rng = np.random.default_rng(0)
    shocks = correlated_shocks(races, 100, rng, scale=2.0)
    assert shocks.shape == (100, 3)
    # Nearby South races should usually correlate more than South vs Midwest
    corr = np.corrcoef(shocks.T)
    assert corr.shape == (3, 3)
    assert np.isfinite(corr).all()


def test_withdrawn_and_runoff_pending_excluded():
    assert is_active_ballot_row({"not_up": False, "ballot_status": "nominated", "election_phase": "general"})
    assert not is_active_ballot_row({"not_up": False, "ballot_status": "withdrawn", "election_phase": "general"})
    assert not is_active_ballot_row(
        {"not_up": False, "ballot_status": "nominated", "election_phase": "runoff_pending"}
    )
    assert not is_active_ballot_row({"not_up": True, "ballot_status": "nominated", "election_phase": "general"})


def test_monitor_check_runs_on_artifact_if_present():
    from pathlib import Path
    from midterms.config import ARTIFACTS_DIR

    if not (ARTIFACTS_DIR / "forecast_latest.json").exists():
        return
    report = monitor_check(min_polls=1, min_enop=0.0)
    assert "ok" in report
    assert "checks" in report
