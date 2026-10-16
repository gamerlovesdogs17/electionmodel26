"""Cheap regression tests for evidence refresh transactions."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from midterms.evidence import fec, preparation


def test_poll_rollback_snapshot_includes_raw_normalized_and_lineage_manifests():
    names = {path.name for path in preparation._poll_snapshot_paths()}
    assert {
        "polls.parquet",
        "polls_live_votehub.parquet",
        "votehub_us_senator.json",
        "fetch_votehub_us_senator.json",
        "fetch_votehub_generic_ballot_2026.json",
        "normalize_votehub_senate.json",
        "merge_live_polls.json",
    } <= names


def test_checked_in_votehub_store_has_coherent_lineage():
    from midterms.evidence.ingest import verify_votehub_poll_lineage

    assert verify_votehub_poll_lineage()["ok"] is True


def test_finance_refresh_failure_restores_entire_store(monkeypatch, tmp_path: Path):
    raw = tmp_path / "raw.zip"
    normalized = tmp_path / "fundraising.parquet"
    manifest = tmp_path / "fundraising.json"
    original = {
        raw: b"sealed-raw",
        normalized: b"sealed-normalized",
        manifest: b"sealed-manifest",
    }
    for path, payload in original.items():
        path.write_bytes(payload)

    monkeypatch.setattr(
        preparation, "_finance_snapshot_paths", lambda: tuple(original)
    )

    def failing_refresh(*, current_as_of: str):
        assert current_as_of == "2026-10-16"
        for path in original:
            path.write_bytes(b"partial-refresh")
        raise RuntimeError("synthetic FEC outage")

    monkeypatch.setattr(fec, "prepare_official_finance_history", failing_refresh)
    result = preparation._refresh_official_finance(as_of="2026-10-16")

    assert result["status"] == "refresh_failed"
    assert result["transactional_restore"] is True
    assert "synthetic FEC outage" in result["error"]
    for path, payload in original.items():
        assert path.read_bytes() == payload


def test_current_form3_receipt_can_atomically_replace_changed_query(
    monkeypatch, tmp_path: Path,
):
    archive_path = tmp_path / "reports.zip"
    old_descriptor = {
        "schema_version": "fec-form3-response-archive-v1",
        "cycle": 2026,
        "max_as_of": "2026-09-27",
        "committee_ids": ["C1"],
        "batch_size": 25,
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("request-metadata.json", json.dumps(old_descriptor))
        archive.writestr(
            "batch-001-page-001.json",
            json.dumps({"pagination": {"pages": 1}, "results": []}),
        )

    calls = {"n": 0}
    response = json.dumps({
        "pagination": {"pages": 1},
        "results": [{
            "committee_id": "C1",
            "committee_name": "Synthetic committee",
            "report_type": "Q3",
            "coverage_start_date": "2026-07-01",
            "coverage_end_date": "2026-09-30",
            "receipt_date": "2026-10-15",
            "file_number": 123,
            "amendment_indicator": "N",
            "amendment_chain": [123],
            "total_receipts_ytd": 10,
            "total_disbursements_ytd": 4,
            "cash_on_hand_end_period": 6,
        }],
    }).encode("utf-8")

    def fake_get_raw(path, params, retries=5):
        calls["n"] += 1
        assert params["max_receipt_date"] == "10/16/2026"
        return response, "https://api.open.fec.gov/v1/reports/house-senate/?api_key=x"

    monkeypatch.setattr(fec, "_get_raw", fake_get_raw)
    frame, metadata = fec.fetch_form3_reports_for_committees(
        cycle=2026,
        committee_ids=["C1"],
        max_as_of="2026-10-16",
        archive_path=archive_path,
        replace_existing_on_query_change=True,
    )

    assert calls["n"] == 1
    assert frame["receipt_date"].tolist() == ["2026-10-15"]
    assert metadata["max_as_of"] == "2026-10-16"
    with zipfile.ZipFile(archive_path) as archive:
        descriptor = json.loads(archive.read("request-metadata.json"))
        assert descriptor["max_as_of"] == "2026-10-16"
        assert archive.read("batch-001-page-001.json") == response
    assert not (tmp_path / ".reports.zip.refreshing").exists()


def test_historical_form3_query_change_still_fails_closed(tmp_path: Path):
    archive_path = tmp_path / "reports.zip"
    old_descriptor = {
        "schema_version": "fec-form3-response-archive-v1",
        "cycle": 2022,
        "max_as_of": "2022-10-09",
        "committee_ids": ["C1"],
        "batch_size": 25,
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("request-metadata.json", json.dumps(old_descriptor))
        archive.writestr(
            "batch-001-page-001.json",
            json.dumps({"pagination": {"pages": 1}, "results": []}),
        )

    try:
        fec.fetch_form3_reports_for_committees(
            cycle=2022,
            committee_ids=["C1"],
            max_as_of="2022-10-10",
            archive_path=archive_path,
        )
    except ValueError as exc:
        assert "query identity changed" in str(exc)
    else:
        raise AssertionError("historical query identity change did not fail closed")


def test_workflow_checks_publication_eligibility_before_sealing():
    rebuild = Path(".github/workflows/rebuild-research.yml").read_text(encoding="utf-8")
    prepare = rebuild[rebuild.index("  prepare_evidence:"):rebuild.index("  tests:")]
    assert prepare.index("Strict publication-domain eligibility before seal") < prepare.index(
        "Seal and strictly verify evidence bundle"
    )
    standalone = Path(".github/workflows/prepare-evidence.yml").read_text(encoding="utf-8")
    assert standalone.index("Strict publication-domain eligibility before seal") < standalone.index(
        "Seal evidence bundle"
    )

