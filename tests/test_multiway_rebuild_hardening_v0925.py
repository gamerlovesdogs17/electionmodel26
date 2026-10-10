"""v0.9.25 multiway rebuild-hardening tests.

Covers:
- MT classified as genuine_multiway_plurality; cannot enter binary non-major race set.
- NE/ID principal-binary eligible; SD binary non-major; AK alaska only.
- source-readiness --strict-sources does not fail solely on forecast_complete=False.
- --require-forecast-complete does fail on incomplete forecast.
- rebuild-preflight importable and runs (may allow multiway withheld).
- Historical multiway validation artifact labels insufficient when no poll archive.
- Stack leakage assertion rejects challenger names.
- CLI rebuild-preflight command is registered and emits ok/blockers structure.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(races: list[dict]) -> dict:
    """Minimal candidate registry dict for testing."""
    return {
        "registry_version": "test",
        "reviewed_as_of": "2026-10-01",
        "races": races,
    }


def _race(race_id: str, state: str, contest_structure: str) -> dict:
    return {
        "race_id": race_id,
        "state": state,
        "contest_structure": contest_structure,
    }


# ---------------------------------------------------------------------------
# 1. MT classified multiway; cannot enter binary non-major set
# ---------------------------------------------------------------------------

class TestMTMultiwayClassification:
    """MT (Montana) must be classified as genuine_multiway_plurality."""

    def test_mt_classified_multiway_from_registry(self):
        """MT with multiway_plurality structure → not in binary set."""
        from midterms.evidence.modeling_paths import (
            binary_non_major_eligible_race_ids,
            multiway_plurality_race_ids,
        )

        registry = _make_registry([
            _race("senate-2026-MT", "MT", "multiway_plurality"),
            _race("senate-2026-NE", "NE", "principal_binary_with_minor_residual"),
            _race("senate-2026-PA", "PA", "binary_dem_vs_rep"),
        ])
        multiway = multiway_plurality_race_ids(registry)
        binary = binary_non_major_eligible_race_ids(registry)

        assert "senate-2026-MT" in multiway
        assert "senate-2026-MT" not in binary

    def test_mt_multiway_triggers_contamination_error(self):
        """If MT slips into binary set, assert_no_binary_contamination must raise."""
        from midterms.evidence.modeling_paths import assert_no_binary_contamination

        with pytest.raises(ValueError, match="contaminated"):
            assert_no_binary_contamination(
                binary_race_ids={"senate-2026-MT", "senate-2026-NE"},
                multiway_race_ids={"senate-2026-MT"},
            )

    def test_mt_contest_classifier_three_credible_principals(self):
        """Three credible principals → genuine_multiway_plurality (no hard-coding)."""
        from midterms.model.contest_classifier import (
            GENUINE_MULTIWAY_PLURALITY,
            classify_contest_structure,
        )

        result = classify_contest_structure(
            ballot_candidates=[
                {"ballot_party": "D", "candidate_name": "Candidate_D"},
                {"ballot_party": "R", "candidate_name": "Candidate_R"},
                {"ballot_party": "I", "candidate_name": "Candidate_I",
                 "caucus": ""},  # independent without D/R caucus
            ],
            # Provide poll shares so the independent is treated as principal.
            candidate_poll_shares={
                "Candidate_D": 0.33,
                "Candidate_R": 0.33,
                "Candidate_I": 0.34,
            },
        )
        assert result["category"] == GENUINE_MULTIWAY_PLURALITY

    def test_multiway_path_assignment(self):
        """Modeling path for multiway_plurality structure is multiway_plurality_adapter."""
        from midterms.evidence.modeling_paths import modeling_path_for_structure

        assert modeling_path_for_structure("multiway_plurality") == "multiway_plurality_adapter"


# ---------------------------------------------------------------------------
# 2. NE/ID principal-binary eligible; SD binary non-major; AK alaska only
# ---------------------------------------------------------------------------

class TestStatePathAssignments:
    """Verify that registry-derived path assignments are correct per spec."""

    def test_ne_principal_binary_eligible(self):
        from midterms.evidence.modeling_paths import binary_non_major_eligible_race_ids

        registry = _make_registry([
            _race("senate-2026-NE", "NE", "principal_binary_with_minor_residual"),
        ])
        binary = binary_non_major_eligible_race_ids(registry)
        assert "senate-2026-NE" in binary

    def test_id_principal_binary_eligible(self):
        from midterms.evidence.modeling_paths import binary_non_major_eligible_race_ids

        registry = _make_registry([
            _race("senate-2026-ID", "ID", "principal_binary_with_minor_residual"),
        ])
        binary = binary_non_major_eligible_race_ids(registry)
        assert "senate-2026-ID" in binary

    def test_sd_binary_non_major(self):
        from midterms.evidence.modeling_paths import (
            modeling_path_for_structure,
        )

        # SD is a binary non-major (non-D vs R) race

        # Either structure may apply; verify the path resolves correctly
        path = modeling_path_for_structure("non_major_party_vs_republican")
        assert path == "binary_non_major_adapter"

    def test_ak_rcv_path(self):
        from midterms.evidence.modeling_paths import (
            alaska_rcv_race_ids,
            modeling_path_for_structure,
        )

        registry = _make_registry([
            _race("senate-2026-AK", "AK", "ranked_choice_multiway"),
        ])
        ak = alaska_rcv_race_ids(registry)
        assert "senate-2026-AK" in ak
        assert modeling_path_for_structure("ranked_choice_multiway") == "alaska_rcv_adapter"

    def test_mt_not_in_ne_id_sd_binary_sets(self):
        """MT with multiway structure must not appear alongside binary-eligible states."""
        from midterms.evidence.modeling_paths import binary_non_major_eligible_race_ids

        registry = _make_registry([
            _race("senate-2026-MT", "MT", "multiway_plurality"),
            _race("senate-2026-NE", "NE", "principal_binary_with_minor_residual"),
            _race("senate-2026-ID", "ID", "principal_binary_with_minor_residual"),
        ])
        binary = binary_non_major_eligible_race_ids(registry)
        assert "senate-2026-MT" not in binary
        assert "senate-2026-NE" in binary
        assert "senate-2026-ID" in binary


# ---------------------------------------------------------------------------
# 3. source-readiness --strict-sources does not fail solely on forecast_complete=False
# ---------------------------------------------------------------------------

class TestSourceReadinessSplit:
    """--strict-sources and --require-forecast-complete are independent gates."""

    def test_strict_sources_ignores_forecast_incomplete(self):
        """When sources are ready but forecast incomplete, --strict-sources must pass."""
        from midterms.cli import main

        def fake_write_source_readiness(**kwargs):
            return {
                "ready_for_expensive_rebuild": True,  # sources OK
                "forecast_coverage_ready": False,      # forecast not done
                "blockers": [],
                "domains": {},
                "forecast_coverage": {
                    "summary": {
                        "forecast_complete": False,
                        "n_fail": 1,
                        "n_pass": 30,
                    }
                },
                "as_of": kwargs.get("as_of"),
                "path": "data/artifacts/source_readiness_latest.json",
            }

        with patch(
            "midterms.evidence.source_readiness.write_source_readiness",
            fake_write_source_readiness,
        ):
            # --strict-sources alone must not raise even though forecast is incomplete
            main([
                "source-readiness",
                "--election-id", "senate-2026",
                "--as-of", "2026-10-08",
                "--strict-sources",
                "--summary",
            ])

    def test_require_forecast_complete_fails_on_incomplete(self):
        """--require-forecast-complete must exit 1 when forecast_complete is False."""
        from midterms.cli import main

        def fake_write_source_readiness(**kwargs):
            return {
                "ready_for_expensive_rebuild": True,
                "forecast_coverage_ready": False,
                "blockers": [],
                "domains": {},
                "forecast_coverage": {
                    "summary": {
                        "forecast_complete": False,
                        "n_fail": 1,
                        "n_pass": 30,
                    }
                },
                "as_of": kwargs.get("as_of"),
                "path": "data/artifacts/source_readiness_latest.json",
            }

        with patch(
            "midterms.evidence.source_readiness.write_source_readiness",
            fake_write_source_readiness,
        ):
            with pytest.raises(SystemExit) as exc:
                main([
                    "source-readiness",
                    "--election-id", "senate-2026",
                    "--as-of", "2026-10-08",
                    "--require-forecast-complete",
                    "--summary",
                ])
            assert exc.value.code == 1

    def test_old_strict_flag_is_alias_for_both(self):
        """--strict is documented as deprecated alias for --strict-sources + --require-forecast-complete."""
        from midterms.cli import main

        def fake_write_source_readiness(**kwargs):
            return {
                "ready_for_expensive_rebuild": False,  # sources not ready
                "forecast_coverage_ready": False,
                "blockers": [{"domain": "polls", "status": "missing"}],
                "domains": {},
                "forecast_coverage": {
                    "summary": {
                        "forecast_complete": False,
                        "n_fail": 1,
                        "n_pass": 0,
                    }
                },
                "as_of": kwargs.get("as_of"),
                "path": "data/artifacts/source_readiness_latest.json",
            }

        with patch(
            "midterms.evidence.source_readiness.write_source_readiness",
            fake_write_source_readiness,
        ):
            with pytest.raises(SystemExit) as exc:
                main([
                    "source-readiness",
                    "--election-id", "senate-2026",
                    "--as-of", "2026-10-08",
                    "--strict",
                    "--summary",
                ])
            assert exc.value.code == 1


# ---------------------------------------------------------------------------
# 3b. A–F: source readiness vs forecast coverage separation
# ---------------------------------------------------------------------------

class TestSourceVsForecastGateSemantics:
    """Regression matrix for strict SOURCE gate vs forecast-coverage withholdal."""

    def test_a_evidence_ready_and_forecast_complete_strict_sources_passes(self):
        """A: evidence ready + full forecast coverage => --strict-sources passes."""
        from midterms.cli import main

        def fake_write(**kwargs):
            return {
                "ready_for_expensive_rebuild": True,
                "forecast_coverage_ready": True,
                "blockers": [],
                "domains": {},
                "forecast_coverage": {
                    "summary": {"forecast_complete": True, "n_fail": 0, "n_pass": 35},
                },
                "as_of": kwargs.get("as_of"),
                "path": "data/artifacts/source_readiness_latest.json",
            }

        with patch(
            "midterms.evidence.source_readiness.write_source_readiness", fake_write,
        ):
            main([
                "source-readiness",
                "--election-id", "senate-2026",
                "--as-of", "2026-10-10",
                "--strict-sources",
                "--summary",
            ])

    def test_b_permitted_multiway_only_strict_sources_passes(self):
        """B: evidence ready + only MT multiway incomplete => --strict-sources passes."""
        from midterms.cli import main
        from midterms.evidence.preparation import classify_strict_forecast_coverage

        readiness = {
            "ready_for_expensive_rebuild": True,
            "forecast_coverage_ready": False,
            "blockers": [],
            "domains": {
                "polls": {
                    "current_race_coverage": {
                        "races": [{
                            "race_id": "senate-2026-MT",
                            "state": "MT",
                            "forecast_status": "fail",
                            "evidence_status": "pass",
                            "reasons": [
                                "multiway_plurality_probability_model_not_historically_supported",
                            ],
                            "contest_structure": "multiway_plurality",
                        }],
                    }
                }
            },
            "forecast_coverage": {
                "summary": {
                    "forecast_complete": False,
                    "n_fail": 1,
                    "n_races": 35,
                    "evidence_ready": True,
                },
                "unsupported_or_withheld_race_ids": ["senate-2026-MT"],
            },
        }
        state = classify_strict_forecast_coverage(readiness)
        assert state["multiway_withheld_only"] is True
        assert state["blocking_forecast_incomplete"] is False

        def fake_write(**kwargs):
            return {**readiness, "as_of": kwargs.get("as_of"), "path": "x"}

        with patch(
            "midterms.evidence.source_readiness.write_source_readiness", fake_write,
        ):
            main([
                "source-readiness",
                "--election-id", "senate-2026",
                "--as-of", "2026-10-10",
                "--strict-sources",
                "--summary",
            ])

    def test_c_actual_source_failure_strict_sources_fails(self):
        """C: genuine source/evidence failure => --strict-sources exits 1."""
        from midterms.cli import main

        def fake_write(**kwargs):
            return {
                "ready_for_expensive_rebuild": False,
                "forecast_coverage_ready": False,
                "blockers": [{"domain": "polls", "status": "missing"}],
                "domains": {},
                "forecast_coverage": {
                    "summary": {"forecast_complete": True, "n_fail": 0},
                },
                "as_of": kwargs.get("as_of"),
                "path": "data/artifacts/source_readiness_latest.json",
            }

        with patch(
            "midterms.evidence.source_readiness.write_source_readiness", fake_write,
        ):
            with pytest.raises(SystemExit) as exc:
                main([
                    "source-readiness",
                    "--election-id", "senate-2026",
                    "--as-of", "2026-10-10",
                    "--strict-sources",
                    "--summary",
                ])
            assert exc.value.code == 1

    def test_d_ordinary_race_forecast_gap_not_approved_multiway(self):
        """D: ordinary binary forecast gap is blocking, not multiway withholdal."""
        from midterms.evidence.preparation import classify_strict_forecast_coverage

        readiness = {
            "ready_for_expensive_rebuild": True,
            "domains": {
                "polls": {
                    "current_race_coverage": {
                        "races": [{
                            "race_id": "senate-2026-OH",
                            "state": "OH",
                            "forecast_status": "fail",
                            "evidence_status": "pass",
                            "reasons": ["identity_sensitive_race_has_zero_compatible_polls"],
                            "contest_structure": "binary_dem_vs_rep",
                        }],
                    }
                }
            },
            "forecast_coverage": {
                "summary": {"forecast_complete": False, "n_fail": 1, "n_races": 35},
                "unsupported_or_withheld_race_ids": ["senate-2026-OH"],
            },
        }
        state = classify_strict_forecast_coverage(readiness)
        assert state["multiway_withheld_only"] is False
        assert state["blocking_forecast_incomplete"] is True
        assert "senate-2026-OH" in (state.get("unexpected_forecast_failure_race_ids") or [])

    def test_e_alaska_adapter_still_enforced_in_workflow(self):
        """E: Alaska RCV exception path remains a hard prepare_evidence requirement."""
        text = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
        prepare_start = text.index("  prepare_evidence:")
        tests_start = text.index("  tests:")
        prepare_block = text[prepare_start:tests_start]
        assert "prepare-alaska-rcv" in prepare_block
        assert "validate-alaska-rcv --as-of" in prepare_block
        assert "verify-exceptional-models --strict" in prepare_block

    def test_f_source_gate_separation_does_not_imply_promotion(self):
        """F: separating source gate must not make publication/promotion true by itself."""
        from midterms.cli import main

        # Sources green + forecast incomplete must still fail the conflated --strict
        # alias (sources + require-forecast-complete), proving promotion-path
        # consumers of --strict remain fail-closed.
        def fake_write(**kwargs):
            return {
                "ready_for_expensive_rebuild": True,
                "forecast_coverage_ready": False,
                "blockers": [],
                "domains": {},
                "forecast_coverage": {
                    "summary": {"forecast_complete": False, "n_fail": 1, "n_pass": 34},
                },
                "as_of": kwargs.get("as_of"),
                "path": "data/artifacts/source_readiness_latest.json",
            }

        with patch(
            "midterms.evidence.source_readiness.write_source_readiness", fake_write,
        ):
            with pytest.raises(SystemExit) as exc:
                main([
                    "source-readiness",
                    "--election-id", "senate-2026",
                    "--as-of", "2026-10-10",
                    "--strict",
                    "--summary",
                ])
            assert exc.value.code == 1

        text = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
        assert "evidence-eligibility" in text and "--publication-config" in text
        assert "--require-publishable" in text
        assert "acceptance-gates --strict" in text


# ---------------------------------------------------------------------------
# 4. rebuild-preflight importable and runs (may allow multiway withheld)
# ---------------------------------------------------------------------------

class TestRebuildPreflight:
    """rebuild_preflight module is importable and run_rebuild_preflight works."""

    def test_importable(self):
        from midterms.validation import rebuild_preflight  # noqa: F401
        from midterms.validation.rebuild_preflight import (
            run_rebuild_preflight,  # noqa: F401
        )

    def test_run_preflight_passes_with_mocks(self, monkeypatch):
        """Preflight should return ok=True when all mocked gates pass."""
        from midterms.validation.rebuild_preflight import run_rebuild_preflight

        # Mock source readiness
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_source_readiness",
            lambda election_id, as_of: {
                "gate": "source_readiness",
                "ok": True,
                "ready_for_expensive_rebuild": True,
                "blockers": [],
                "domain_status": {},
            },
        )
        # Mock forecast coverage
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_forecast_coverage",
            lambda election_id, as_of: {
                "gate": "forecast_coverage",
                "ok": True,
                "status": "complete",
                "forecast_complete": True,
                "n_fail": 0,
                "multiway_withheld": False,
                "summary": {},
            },
        )
        # Mock modeling paths
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_modeling_paths",
            lambda: {
                "gate": "modeling_paths",
                "ok": True,
                "failures": [],
                "path_assignments": {},
                "highlight_states": {},
                "n_binary_non_major": 2,
                "n_multiway": 1,
                "n_alaska": 1,
                "mt_in_binary_set": [],
            },
        )
        # Mock challenger/stack
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_challenger_stack_classification",
            lambda: {
                "gate": "challenger_stack_classification",
                "ok": True,
                "failures": [],
                "n_specification_challengers": 6,
                "stack_artifact_checked": False,
                "stack_artifact_exists": False,
            },
        )
        # Mock exceptional lineage (skipped)
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_exceptional_lineage",
            lambda: {
                "gate": "exceptional_lineage",
                "ok": True,
                "skipped": True,
                "reason": "artifact not yet generated",
            },
        )
        # Mock multiway share smoke
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_multiway_share_smoke",
            lambda: {
                "gate": "multiway_share_smoke",
                "ok": True,
                "failures": [],
            },
        )
        # Mock chamber accounting
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_chamber_accounting",
            lambda: {
                "gate": "chamber_accounting",
                "ok": True,
                "failures": [],
                "n_active_contested_races": 34,
                "note": "",
            },
        )

        result = run_rebuild_preflight(election_id="senate-2026", as_of="2026-10-08")
        assert result["ok"] is True
        assert result["blockers"] == []
        assert result["multiway_withheld"] is False

    def test_run_preflight_allows_multiway_withheld(self, monkeypatch):
        """Preflight returns ok=True even when multiway_withheld=True."""
        from midterms.validation.rebuild_preflight import run_rebuild_preflight

        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_source_readiness",
            lambda election_id, as_of: {
                "gate": "source_readiness",
                "ok": True,
                "ready_for_expensive_rebuild": True,
                "blockers": [],
                "domain_status": {},
            },
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_forecast_coverage",
            lambda election_id, as_of: {
                "gate": "forecast_coverage",
                "ok": "withheld_multiway",
                "status": "withheld_multiway_only",
                "forecast_complete": False,
                "n_fail": 1,
                "multiway_withheld": True,
                "summary": {"forecast_complete": False, "n_fail": 1},
            },
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_modeling_paths",
            lambda: {"gate": "modeling_paths", "ok": True, "failures": [],
                     "path_assignments": {}, "highlight_states": {},
                     "n_binary_non_major": 2, "n_multiway": 1, "n_alaska": 1,
                     "mt_in_binary_set": []},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_challenger_stack_classification",
            lambda: {"gate": "challenger_stack_classification", "ok": True, "failures": [],
                     "n_specification_challengers": 6, "stack_artifact_checked": False,
                     "stack_artifact_exists": False},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_exceptional_lineage",
            lambda: {"gate": "exceptional_lineage", "ok": True, "skipped": True,
                     "reason": "artifact not yet generated"},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_multiway_share_smoke",
            lambda: {"gate": "multiway_share_smoke", "ok": True, "failures": []},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_chamber_accounting",
            lambda: {"gate": "chamber_accounting", "ok": True, "failures": [],
                     "n_active_contested_races": 33, "note": ""},
        )

        result = run_rebuild_preflight(election_id="senate-2026", as_of="2026-10-08")
        # ok=True because multiway_withheld is a warning, not a blocker
        assert result["ok"] is True
        assert result["multiway_withheld"] is True
        # Should have a warning about MULTIWAY_WITHHELD
        assert any("MULTIWAY_WITHHELD" in w for w in result["warnings"])

    def test_run_preflight_fails_on_source_blocker(self, monkeypatch):
        """Preflight returns ok=False when source readiness fails."""
        from midterms.validation.rebuild_preflight import run_rebuild_preflight

        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_source_readiness",
            lambda election_id, as_of: {
                "gate": "source_readiness",
                "ok": False,
                "ready_for_expensive_rebuild": False,
                "blockers": [{"domain": "polls", "status": "missing"}],
                "domain_status": {},
            },
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_forecast_coverage",
            lambda election_id, as_of: {"gate": "forecast_coverage", "ok": True,
                                         "multiway_withheld": False, "summary": {}},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_modeling_paths",
            lambda: {"gate": "modeling_paths", "ok": True, "failures": [],
                     "path_assignments": {}, "highlight_states": {},
                     "n_binary_non_major": 0, "n_multiway": 0, "n_alaska": 0,
                     "mt_in_binary_set": []},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_challenger_stack_classification",
            lambda: {"gate": "challenger_stack_classification", "ok": True, "failures": [],
                     "n_specification_challengers": 6, "stack_artifact_checked": False,
                     "stack_artifact_exists": False},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_exceptional_lineage",
            lambda: {"gate": "exceptional_lineage", "ok": True, "skipped": True,
                     "reason": "artifact not yet generated"},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_multiway_share_smoke",
            lambda: {"gate": "multiway_share_smoke", "ok": True, "failures": []},
        )
        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_chamber_accounting",
            lambda: {"gate": "chamber_accounting", "ok": True, "failures": [],
                     "n_active_contested_races": 33, "note": ""},
        )

        result = run_rebuild_preflight(election_id="senate-2026", as_of="2026-10-08")
        assert result["ok"] is False
        assert result["blockers"]

    def test_run_preflight_strict_exits_on_blocker(self, monkeypatch):
        """Preflight with strict=True raises SystemExit(1) when a blocker is present."""
        from midterms.validation.rebuild_preflight import run_rebuild_preflight

        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight._check_source_readiness",
            lambda election_id, as_of: {
                "gate": "source_readiness",
                "ok": False,
                "ready_for_expensive_rebuild": False,
                "blockers": [{"domain": "polls", "status": "missing"}],
                "domain_status": {},
            },
        )
        for gate in ("_check_forecast_coverage",):
            monkeypatch.setattr(
                f"midterms.validation.rebuild_preflight.{gate}",
                lambda *a, g=gate, **kw: {
                    "gate": g, "ok": True, "multiway_withheld": False, "summary": {},
                },
            )
        for gate in (
            "_check_modeling_paths",
            "_check_challenger_stack_classification",
            "_check_exceptional_lineage",
            "_check_multiway_share_smoke",
            "_check_chamber_accounting",
        ):
            monkeypatch.setattr(
                f"midterms.validation.rebuild_preflight.{gate}",
                lambda g=gate: {
                    "gate": g, "ok": True, "failures": [],
                    "path_assignments": {}, "highlight_states": {},
                    "n_binary_non_major": 0, "n_multiway": 0, "n_alaska": 0,
                    "mt_in_binary_set": [], "skipped": False,
                    "n_active_contested_races": 33, "note": "",
                    "n_specification_challengers": 6,
                    "stack_artifact_checked": False, "stack_artifact_exists": False,
                },
            )

        with pytest.raises(SystemExit) as exc:
            run_rebuild_preflight(
                election_id="senate-2026", as_of="2026-10-08", strict=True
            )
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# 5. CLI rebuild-preflight command is registered
# ---------------------------------------------------------------------------

class TestRebuildPreflightCLI:
    """CLI rebuild-preflight command is registered and emits a structured report."""

    def test_cli_rebuild_preflight_registered(self, monkeypatch):
        """rebuild-preflight subcommand must be discoverable via argparse."""
        from midterms.cli import main

        def fake_run(**kwargs):
            return {
                "ok": True,
                "multiway_withheld": False,
                "blockers": [],
                "warnings": [],
                "checks": {},
            }

        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight.run_rebuild_preflight", fake_run
        )
        # Must not raise
        main(["rebuild-preflight", "--election-id", "senate-2026", "--as-of", "2026-10-08"])

    def test_cli_rebuild_preflight_strict_exit(self, monkeypatch):
        """CLI rebuild-preflight --strict must propagate SystemExit(1)."""
        from midterms.cli import main

        def fake_run(**kwargs):
            if kwargs.get("strict"):
                raise SystemExit(1)
            return {"ok": False, "multiway_withheld": False, "blockers": ["test"], "warnings": [], "checks": {}}

        monkeypatch.setattr(
            "midterms.validation.rebuild_preflight.run_rebuild_preflight", fake_run
        )
        with pytest.raises(SystemExit) as exc:
            main(["rebuild-preflight", "--election-id", "senate-2026",
                  "--as-of", "2026-10-08", "--strict"])
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# 6. Historical multiway validation artifact: insufficient when no poll archive
# ---------------------------------------------------------------------------

class TestMultiwayValidationArtifact:
    """build_multiway_plurality_validation produces correct labels when no poll archive."""

    def test_insufficient_label_when_no_poll_archive(self, tmp_path, monkeypatch):
        """Without a historical candidate-level poll archive, win probabilities must be withheld."""
        from midterms.validation.multiway_plurality_validation import (
            build_multiway_plurality_validation,
        )

        # Patch artifact directory to tmp
        monkeypatch.setattr(
            "midterms.validation.multiway_plurality_validation.ARTIFACTS_DIR",
            tmp_path,
        )
        # Patch normalized dir to empty tmp (no poll archive files)
        monkeypatch.setattr(
            "midterms.validation.multiway_plurality_validation.NORMALIZED_DIR",
            tmp_path,
        )

        # Provide minimal analogs (structural only, no poll validation)
        analogs = {
            "n_analogs": 3,
            "n_ballot_multiway": 3,
            "n_principal_binary_with_minors": 0,
            "n_materially_multiway": 2,
            "status": "limited_structural_inventory",
            "years_present": [2018, 2020, 2022],
            "coverage_gaps": [],
            "analogs": [
                {
                    "race_id": "senate-2018-MT",
                    "state": "MT",
                    "year": 2018,
                    "n_candidates": 3,
                    "materially_multiway": True,
                    "structural_class": "materially_multiway",
                    "candidates": [
                        {"name": "A", "party": "D"},
                        {"name": "B", "party": "R"},
                        {"name": "C", "party": "L"},
                    ],
                },
                {
                    "race_id": "senate-2020-MT",
                    "state": "MT",
                    "year": 2020,
                    "n_candidates": 3,
                    "materially_multiway": True,
                    "structural_class": "materially_multiway",
                    "candidates": [
                        {"name": "A", "party": "D"},
                        {"name": "B", "party": "R"},
                        {"name": "C", "party": "L"},
                    ],
                },
            ],
        }

        result = build_multiway_plurality_validation(analogs=analogs, out_path=tmp_path / "mw.json")

        # Without poll archive: win probabilities must NOT be activated
        assert result["activates_forecast_probabilities"] is False
        assert result["win_probability_status"] == "fail_closed"
        assert "insufficient" in (result["summary"].get("label") or "").lower()
        assert result["probability_model_support_status"] != "limited_validation"

    def test_smoke_fit_multiway_share_draws(self):
        """Synthetic share draws must sum to one and produce multiple winners."""
        from midterms.validation.multiway_plurality_validation import (
            smoke_fit_multiway_share_draws,
        )

        result = smoke_fit_multiway_share_draws(n_candidates=4, n_draws=200, seed=42)
        assert result["shares_sum_to_one"] is True
        assert result["n_candidates"] == 4
        assert result["n_draws"] == 200
        assert result["n_unique_winners"] >= 2  # at least 2 different winners in 200 draws

    def test_multiway_validation_schema_version(self, tmp_path, monkeypatch):
        """Artifact must carry the correct schema version."""
        from midterms.validation.multiway_plurality_validation import (
            VALIDATION_SCHEMA,
            build_multiway_plurality_validation,
        )

        monkeypatch.setattr(
            "midterms.validation.multiway_plurality_validation.ARTIFACTS_DIR", tmp_path
        )
        monkeypatch.setattr(
            "midterms.validation.multiway_plurality_validation.NORMALIZED_DIR", tmp_path
        )

        result = build_multiway_plurality_validation(
            analogs={"n_analogs": 0, "analogs": [], "n_materially_multiway": 0},
            out_path=tmp_path / "mw.json",
        )
        assert result["schema_version"] == VALIDATION_SCHEMA


# ---------------------------------------------------------------------------
# 7. Stack leakage assertion rejects challenger names
# ---------------------------------------------------------------------------

class TestStackLeakageAssertion:
    """assert_no_same_family_stack_leakage must reject any production weight for challengers."""

    def test_rejects_challenger_in_production_weights(self):
        from midterms.validation.stack_weights import (
            SPECIFICATION_CHALLENGERS,
            assert_no_same_family_stack_leakage,
        )

        # Pick any challenger name from the spec set
        challenger = next(iter(SPECIFICATION_CHALLENGERS))
        bad_weights = {challenger: 0.1, "state_space": 0.9}

        with pytest.raises(ValueError, match="leaked"):
            assert_no_same_family_stack_leakage(bad_weights)

    def test_accepts_zero_weight_for_challenger(self):
        """Zero weight for a challenger should not trigger the leakage error."""
        from midterms.validation.stack_weights import (
            SPECIFICATION_CHALLENGERS,
            assert_no_same_family_stack_leakage,
        )

        challenger = next(iter(SPECIFICATION_CHALLENGERS))
        weights = {challenger: 0.0, "state_space": 1.0}
        # Must not raise
        assert_no_same_family_stack_leakage(weights)

    def test_accepts_clean_production_weights(self):
        from midterms.validation.stack_weights import (
            SPECIFICATION_CHALLENGERS,
            assert_no_same_family_stack_leakage,
        )

        # Ensure production-only names are not challengers
        production_names = {"state_space", "ridge_fundamentals"}
        overlap = production_names & SPECIFICATION_CHALLENGERS
        weights = {n: 0.5 for n in production_names - overlap}
        # Must not raise
        assert_no_same_family_stack_leakage(weights)

    def test_specification_challenger_names_nonempty(self):
        from midterms.validation.stack_weights import specification_challenger_names

        names = specification_challenger_names()
        assert len(names) > 0
        assert isinstance(names, frozenset)

    def test_challenger_names_match_sentinel(self):
        from midterms.validation.stack_weights import (
            SPECIFICATION_CHALLENGERS,
            specification_challenger_names,
        )

        assert specification_challenger_names() == SPECIFICATION_CHALLENGERS


# ---------------------------------------------------------------------------
# 8. Workflow structural checks
# ---------------------------------------------------------------------------

class TestWorkflowStructure:
    """Verify the rebuild-research.yml workflow has the correct v0.9.25 structure."""

    def _load_workflow(self):
        import yaml

        path = Path(".github/workflows/rebuild-research.yml")
        text = path.read_text(encoding="utf-8")
        return text, yaml.safe_load(text)

    def test_workflow_has_rebuild_preflight_before_oof(self):
        """rebuild-preflight must appear before OOF steps in the rebuild job."""
        text, _ = self._load_workflow()
        # Find rebuild job block
        rebuild_start = text.index("  rebuild:")
        rebuild_block = text[rebuild_start:]
        preflight_pos = rebuild_block.find("rebuild-preflight")
        oof_pos = rebuild_block.find("nested-component-loo")
        assert preflight_pos > 0, "rebuild-preflight not found in rebuild job"
        assert oof_pos > 0, "nested-component-loo not found in rebuild job"
        assert preflight_pos < oof_pos, (
            "rebuild-preflight must appear BEFORE nested-component-loo (OOF)"
        )

    def test_workflow_prepare_evidence_order(self):
        """prepare_evidence: diagnostic → refresh → strict-sources → forecast coverage gate."""
        text, _ = self._load_workflow()
        prepare_start = text.index("  prepare_evidence:")
        tests_start = text.index("  tests:")
        prepare_block = text[prepare_start:tests_start]

        # The diagnostic step (no --strict) must appear before refresh
        diag_pos = prepare_block.find("Diagnostic source preflight")
        refresh_pos = prepare_block.find("Refresh safe sources")
        strict_src_pos = prepare_block.find("--strict-sources")
        forecast_gate_pos = prepare_block.find("Forecast coverage gate")
        classify_pos = prepare_block.find("classify_strict_forecast_coverage")

        assert diag_pos > 0, "Diagnostic source preflight step missing"
        assert refresh_pos > 0, "Refresh safe sources step missing"
        assert strict_src_pos > 0, "--strict-sources step missing"
        assert forecast_gate_pos > 0, "Forecast coverage gate step missing"
        assert classify_pos > 0, "forecast coverage must classify via classify_strict_forecast_coverage"

        assert diag_pos < refresh_pos < strict_src_pos < forecast_gate_pos, (
            "prepare_evidence steps are out of order: "
            f"diag={diag_pos}, refresh={refresh_pos}, "
            f"strict_src={strict_src_pos}, fc_gate={forecast_gate_pos}"
        )
        # Hard CLI require-forecast-complete would ::error:: on approved MT withholdal.
        assert " --require-forecast-complete" not in prepare_block
        assert "--require-forecast-complete\n" not in prepare_block

    def test_workflow_no_conflated_strict_before_refresh(self):
        """The old --strict before refresh must be gone from prepare_evidence."""
        text, _ = self._load_workflow()
        prepare_start = text.index("  prepare_evidence:")
        tests_start = text.index("  tests:")
        prepare_block = text[prepare_start:tests_start]
        refresh_pos = prepare_block.find("Refresh safe sources")

        # Nothing before "Refresh safe sources" should be a --strict gate
        pre_refresh = prepare_block[:refresh_pos]
        # The diagnostic step should not contain --strict
        assert "--strict" not in pre_refresh, (
            "A --strict flag appears before 'Refresh safe sources' in prepare_evidence; "
            "the diagnostic step must be read-only (no --strict)"
        )

    def test_workflow_multiway_withheld_job_output_not_cross_job_env(self):
        """multiway_withheld must propagate via job outputs, not cross-job GITHUB_ENV."""
        text, parsed = self._load_workflow()
        prepare = parsed["jobs"]["prepare_evidence"]
        assert "multiway_withheld" in (prepare.get("outputs") or {})
        assert "steps.forecast_coverage.outputs.multiway_withheld" in prepare["outputs"][
            "multiway_withheld"
        ]
        assert "id: forecast_coverage" in text
        assert 'multiway_withheld={value}' in text
        assert "_write_flag(\"1\")" in text or "_write_flag('1')" in text
        assert "_write_flag(\"0\")" in text or "_write_flag('0')" in text
        # Downstream jobs must consume the prepare_evidence output, not env.MULTIWAY_WITHHELD
        assert "needs.prepare_evidence.outputs.multiway_withheld" in text
        assert "env.MULTIWAY_WITHHELD" not in text
        assert "POST_OOF_EXCEPTIONAL_MODEL_BLOCKER" in text
        assert "constraints/research-rebuild.txt" in text

    def test_workflow_commit_stages_full_raw_including_live_polls_csv(self):
        """Commit step must stage data/raw (incl. polls_live_votehub.csv), not only external/."""
        text, _ = self._load_workflow()
        prepare_start = text.index("  prepare_evidence:")
        tests_start = text.index("  tests:")
        prepare_block = text[prepare_start:tests_start]
        commit_pos = prepare_block.find("Commit coherent evidence state")
        assert commit_pos > 0
        commit_block = prepare_block[commit_pos:]
        assert "git add data/raw data/normalized data/manifests" in commit_block
        assert "git add data/raw/external" not in commit_block
        assert "polls_live_votehub.csv" in commit_block
        assert "git pull --rebase --autostash origin main" in commit_block

    def test_workflow_rebuild_preflight_strict(self):
        """rebuild-preflight in rebuild job must use --strict flag."""
        text, _ = self._load_workflow()
        rebuild_start = text.index("  rebuild:")
        rebuild_block = text[rebuild_start:]
        # Find the rebuild-preflight section
        pf_pos = rebuild_block.find("rebuild-preflight")
        assert pf_pos > 0
        # The --strict flag must appear near rebuild-preflight
        pf_context = rebuild_block[pf_pos:pf_pos + 500]
        assert "--strict" in pf_context


def test_live_generic_ballot_path_matches_fetch_receipt() -> None:
    """Live GB raw bytes must match the *current* fetch receipt (refreshable)."""
    import hashlib
    import inspect
    import json

    from midterms.evidence.ingest import (
        generic_ballot_aggregate,
        validate_local_fetch_receipt,
    )
    from midterms.evidence.live_generic_ballot import (
        LIVE_GENERIC_BALLOT_FETCH_MANIFEST,
        LIVE_GENERIC_BALLOT_FILENAME,
        LIVE_GENERIC_BALLOT_PATH,
        assert_live_generic_ballot_lineage_coherent,
        live_generic_ballot_lineage,
    )

    src = inspect.getsource(generic_ballot_aggregate)
    assert LIVE_GENERIC_BALLOT_FILENAME in src or "LIVE_GENERIC_BALLOT_PATH" in src
    receipt = json.loads(LIVE_GENERIC_BALLOT_FETCH_MANIFEST.read_text(encoding="utf-8"))
    checked = validate_local_fetch_receipt(
        LIVE_GENERIC_BALLOT_PATH, LIVE_GENERIC_BALLOT_FETCH_MANIFEST,
    )
    assert checked["ok"] is True
    # Compare against the current receipt — never a frozen historical hash.
    assert checked["sha256"] == receipt["sha256"]
    assert checked["bytes"] == receipt["bytes"]
    raw = LIVE_GENERIC_BALLOT_PATH.read_bytes()
    assert len(raw) == receipt["bytes"]
    assert hashlib.sha256(raw).hexdigest() == receipt["sha256"]
    # Windows autocrlf must not rewrite the live capture (see .gitattributes -text).
    assert b"\r\n" not in raw
    crlf_hash = hashlib.sha256(raw.replace(b"\n", b"\r\n")).hexdigest()
    assert crlf_hash != receipt["sha256"]
    lineage = assert_live_generic_ballot_lineage_coherent()
    assert lineage["canonical_filename"] == LIVE_GENERIC_BALLOT_FILENAME
    assert lineage["file_matches_fetch_receipt"] is True
    assert lineage["file_sha256"] == receipt["sha256"]
    assert (
        live_generic_ballot_lineage()["archive_filename_not_used_for_live"]
        == "votehub_generic_ballot.json"
    )
    attrs = Path(".gitattributes").read_text(encoding="utf-8")
    assert "votehub_generic_ballot_2026.json -text" in attrs
    assert "fetch_votehub_generic_ballot_2026.json -text" in attrs


def test_raw_generic_ballot_change_without_receipt_fails(tmp_path: Path) -> None:
    """A raw GB mutation that leaves the receipt stale must fail closed."""
    import json

    from midterms.evidence.ingest import validate_local_fetch_receipt

    raw = tmp_path / "votehub_generic_ballot_2026.json"
    receipt = tmp_path / "fetch_votehub_generic_ballot_2026.json"
    raw.write_bytes(b'{"polls":[]}')
    receipt.write_text(
        json.dumps({
            "name": "votehub_generic_ballot_2026",
            "sha256": "0" * 64,
            "bytes": 999,
            "path": str(raw),
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match on-disk raw file"):
        validate_local_fetch_receipt(raw, receipt)


def test_canonical_votehub_fetch_writes_matching_raw_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_votehub_polls must seal raw + receipt that validate together."""
    import json

    from midterms.evidence import ingest

    raw_dir = tmp_path / "raw" / "external"
    manifests = tmp_path / "manifests"
    raw_dir.mkdir(parents=True)
    manifests.mkdir(parents=True)
    monkeypatch.setattr(ingest, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ingest, "MANIFESTS_DIR", manifests)

    def fake_get(path: str, params: dict | None = None):
        assert path == "/polls"
        return {
            "polls": [
                {"id": "gb-1", "poll_type": "generic-ballot", "subject": "2026"},
            ],
        }

    meta = ingest.fetch_votehub_polls(
        poll_type="generic-ballot",
        subject="2026",
        client=fake_get,
        dest_dir=None,
    )
    raw_path = Path(meta["path"])
    if not raw_path.is_file():
        # recorded_path may be repo-relative; resolve under monkeypatched RAW_DIR
        raw_path = (tmp_path / "raw" / "external" / "votehub_generic_ballot_2026.json")
    receipt_path = manifests / "fetch_votehub_generic_ballot_2026.json"
    assert raw_path.is_file()
    assert receipt_path.is_file()
    checked = ingest.validate_local_fetch_receipt(raw_path, receipt_path)
    assert checked["ok"] is True
    assert checked["sha256"] == meta["sha256"]
    assert b"\r\n" not in raw_path.read_bytes()
    assert receipt_path.read_bytes().endswith(b"\n")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["url"] == "https://api.votehub.com/polls"
    assert receipt["license"] == "CC BY 4.0"
    assert "VoteHub" in receipt["attribution"]


def test_workflow_stages_raw_and_fetch_receipts_together() -> None:
    """Evidence seal commit must stage data/raw and data/manifests together."""
    text = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
    prepare = text[text.index("  prepare_evidence:"): text.index("  tests:")]
    commit = prepare[prepare.index("Commit coherent evidence state"):]
    assert "git add data/raw data/normalized data/manifests" in commit
    prepare_ev = Path(".github/workflows/prepare-evidence.yml").read_text(encoding="utf-8")
    assert "git add data/raw data/normalized data/manifests" in prepare_ev
