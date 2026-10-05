"""Diagnostic chamber soft-fails must not abort validation-report assembly."""

from __future__ import annotations

from midterms.validation.cycle_replay import diagnostic_chamber_skip
from midterms.validation.lead_time_grid import replay_lead_time_grid


def test_diagnostic_chamber_skip_classifies_binary_ineligible():
    payload = diagnostic_chamber_skip(
        ValueError(
            "race is ineligible for binary chamber forecast: "
            "senate-2022-AK: no_single_predeclared_dem_vs_rep_final_pair"
        ),
        n_snapshot_seats=100,
        n_fit_races=34,
    )
    assert payload is not None
    assert payload["ok"] is False
    assert payload["status"] == "binary_chamber_ineligible_race"
    assert "senate-2022-AK" in payload["error"]


def test_diagnostic_chamber_skip_ignores_unrelated_errors():
    assert (
        diagnostic_chamber_skip(
            ValueError("unexpected numerical failure"),
            n_snapshot_seats=1,
            n_fit_races=1,
        )
        is None
    )


def test_lead_time_grid_soft_fails_alaska_binary_chamber():
    """PRIMARY_HOLDOUT 2022 includes Alaska RCV; chamber may soft-fail, not raise."""
    report = replay_lead_time_grid(year=2022, lead_days=(60,), draws=25)
    assert "error" not in report or report.get("error") != "no races"
    assert report["by_lead"], report
    chamber = report["by_lead"][0]["chamber"]
    assert "scores" in report["by_lead"][0]
    if chamber.get("ok") is False:
        assert chamber["status"] in {
            "binary_chamber_ineligible_race",
            "incomplete_point_in_time_race_universe",
            "nonstandard_chamber_mapping",
            "incomplete_predictive_chamber_coverage",
        }
    else:
        assert chamber.get("ok") is True


def test_rebuild_workflow_runs_validation_report_after_forecast():
    from pathlib import Path

    text = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
    assert text.index("--require-publishable") < text.index("validation-report --full")
    assert text.index("validation-report --full") < text.index(
        "validation-report --refresh-status-only"
    )
    assert "resume_run_id" in text
    assert "inputs.refresh_safe_sources && inputs.resume_run_id == ''" in text
