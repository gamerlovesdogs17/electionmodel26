"""Cheap regression tests for evidence refresh transactions."""

from __future__ import annotations

import json
import zipfile
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pandas as pd

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


def test_finance_transaction_includes_keyless_current_bulk_source():
    names = {path.name for path in preparation._finance_snapshot_paths()}
    assert "fec_house_senate_reports_2026.csv" in names
    attributes = Path(".gitattributes").read_text(encoding="utf-8")
    assert "data/raw/external/fec_house_senate_reports_*.csv -text" in attributes


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


def test_finance_refresh_failure_reports_exact_incomplete_cutoff(
    monkeypatch, tmp_path: Path,
):
    original = tmp_path / "sealed.json"
    original.write_bytes(b"sealed")
    monkeypatch.setattr(preparation, "_finance_snapshot_paths", lambda: (original,))
    monkeypatch.setattr(fec, "prepare_official_finance_history", lambda **_: {
        "production_eligible": False,
        "coverage": {
            "senate-2024-lead-30": {
                "production_eligible": False,
                "missing_race_ids": ["senate-2024-AA"],
                "excluded_race_ids": ["senate-2024-BB"],
            },
        },
    })

    result = preparation._refresh_official_finance(as_of="2026-10-16")

    assert result["status"] == "refresh_failed"
    assert "senate-2024-lead-30" in result["error"]
    assert "senate-2024-AA" in result["error"]
    assert "senate-2024-BB" in result["error"]
    assert original.read_bytes() == b"sealed"


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


def test_current_finance_query_uses_ticket_candidates_and_state_fallback(monkeypatch):
    links = pd.DataFrame([
        {"candidate_id": "D1", "candidate_name": "Alpha, Alice", "committee_id": "C1", "state": "AA", "party": "DEM"},
        {"candidate_id": "R1", "candidate_name": "Beta, Bob", "committee_id": "C2", "state": "AA", "party": "REP"},
        {"candidate_id": "X1", "candidate_name": "Other, Casey", "committee_id": "C3", "state": "AA", "party": "DEM"},
        {"candidate_id": "D2", "candidate_name": "Someone, Dana", "committee_id": "C4", "state": "BB", "party": "DEM"},
        {"candidate_id": "R2", "candidate_name": "Else, Evan", "committee_id": "C5", "state": "BB", "party": "REP"},
    ])
    monkeypatch.setattr(fec, "current_registry_tickets", lambda: {
        "senate-2026-AA": {
            "state": "AA", "dem_name": "Alice Alpha", "rep_name": "Bob Beta",
        },
        "senate-2026-BB": {
            "state": "BB", "dem_name": "Missing Person", "rep_name": "Evan Else",
        },
    })

    selected, audit = fec._current_ticket_relevant_links(links)

    assert set(selected["candidate_id"]) == {"D1", "R1", "D2", "R2"}
    assert "X1" not in set(selected["candidate_id"])
    assert audit["n_matched_races"] == 1
    assert audit["fallback_races"] == [{
        "race_id": "senate-2026-BB",
        "state": "BB",
        "modeled_candidate_matched": False,
        "opposing_candidate_matched": True,
        "fallback": "all_state_senate_principal_committees",
    }]


def test_current_finance_query_matches_named_independent_without_party_substitution(
    monkeypatch,
):
    links = pd.DataFrame([
        {"candidate_id": "I1", "candidate_name": "EXAMPLE, INDY", "committee_id": "CI", "state": "AA", "party": "IND"},
        {"candidate_id": "D1", "candidate_name": "EXAMPLE, DEM", "committee_id": "CD", "state": "AA", "party": "DEM"},
        {"candidate_id": "R1", "candidate_name": "EXAMPLE, RILEY", "committee_id": "CR", "state": "AA", "party": "REP"},
    ])
    monkeypatch.setattr(fec, "current_registry_tickets", lambda: {
        "senate-2026-AA": {
            "state": "AA",
            "modeled_candidate_name": "Indy Example",
            "opposing_candidate_name": "Riley Example",
            "modeled_ballot_party": "I",
            "opposing_ballot_party": "R",
        },
    })

    selected, audit = fec._current_ticket_relevant_links(links)

    assert set(selected["candidate_id"]) == {"I1", "R1"}
    assert "D1" not in set(selected["candidate_id"])
    assert audit["n_matched_races"] == 1
    assert audit["fallback_races"] == []


def test_historical_finance_coverage_requires_only_complete_binary_tickets(monkeypatch):
    races = pd.DataFrame([
        {"election_id": "senate-2024", "race_id": "senate-2024-AA", "state": "AA", "not_up": False},
        {"election_id": "senate-2024", "race_id": "senate-2024-BB", "state": "BB", "not_up": False},
        {"election_id": "senate-2024", "race_id": "senate-2024-CC", "state": "CC", "not_up": True},
    ])
    monkeypatch.setattr(fec, "historical_nominee_tickets", lambda _election_id: {
        "senate-2024-AA": {
            "race_id": "senate-2024-AA", "state": "AA",
            "dem_name": "Alpha", "rep_name": "Beta",
        },
    })

    universe = fec._finance_coverage_universe(races, "senate-2024")

    assert universe["policy"] == "historical_complete_dem_rep_ticket_only"
    assert universe["all_contested_race_ids"] == ["senate-2024-AA", "senate-2024-BB"]
    assert universe["required_race_ids"] == ["senate-2024-AA"]
    assert universe["required_states"] == ["AA"]
    assert universe["excluded_race_ids"] == ["senate-2024-BB"]
    assert "excluded_from_binary_finance_scoring" in universe["excluded_reason"]


def test_current_finance_coverage_keeps_nonstandard_declared_candidates_required():
    races = pd.DataFrame([
        {"election_id": "senate-2026", "race_id": "senate-2026-AA", "state": "AA", "not_up": False},
        {"election_id": "senate-2026", "race_id": "senate-2026-BB", "state": "BB", "not_up": False},
    ])

    universe = fec._finance_coverage_universe(races, "senate-2026")

    assert universe["policy"] == "current_declared_candidate_side"
    assert universe["required_race_ids"] == ["senate-2026-AA", "senate-2026-BB"]
    assert universe["excluded_race_ids"] == []


def test_demo_key_rate_limit_fails_immediately_and_points_to_bulk(monkeypatch):
    headers = Message()
    headers["Retry-After"] = "33893"
    error = HTTPError(
        "https://api.open.fec.gov/v1/reports/house-senate/",
        429,
        "Too Many Requests",
        headers,
        None,
    )
    calls = {"n": 0}

    def rate_limited(*_args, **_kwargs):
        calls["n"] += 1
        raise error

    monkeypatch.delenv("FEC_API_KEY", raising=False)
    monkeypatch.setattr(fec, "urlopen", rate_limited)

    try:
        fec._get_raw("/reports/house-senate/", {"cycle": 2026})
    except RuntimeError as exc:
        message = str(exc)
        assert "public DEMO_KEY" in message
        assert "official House/Senate bulk source" in message
        assert "Retry-After=33893s" in message
    else:
        raise AssertionError("shared DEMO_KEY rate limit did not fail closed")
    assert calls["n"] == 1


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
    assert "FEC_API_KEY" not in rebuild
    assert "FEC_API_KEY" not in standalone
    assert "Require personal FEC key for current finance refresh" not in rebuild
    assert "Require personal FEC key for current finance refresh" not in standalone
    assert "prepare-evidence --election-id senate-2026" in rebuild
    assert "prepare-evidence --election-id senate-2026" in standalone


def test_workflow_refreshes_finance_audit_before_structural_parity():
    workflow = Path(".github/workflows/rebuild-research.yml").read_text(
        encoding="utf-8",
    )
    parity = workflow.index("Historical structural-feature parity (pre-OOF)")
    audit = workflow.index(
        "python -m midterms.validation.probability_integrity_repair",
        parity,
    )
    parity_check = workflow.index(
        "python -m midterms.cli historical-model-feature-parity",
        parity,
    )
    assert audit < parity_check


def test_official_form3_bulk_is_keyless_receipt_bounded_and_deterministic(
    monkeypatch, tmp_path: Path,
):
    source = pd.DataFrame([
        {
            "CMTE_ID": "C1", "CMTE_NM": "ALPHA", "FORM_TP_CD": "F3",
            "RPT_TP": "Q3", "RECEIPT_DT": "15-OCT-26",
            "CVG_START_DT": "2026-07-01", "CVG_END_DT": "2026-09-30",
            "TTL_RECEIPTS": "125.50", "TTL_DISB": "45.25", "COH_COP": "80.25",
        },
        {
            "CMTE_ID": "C1", "CMTE_NM": "ALPHA", "FORM_TP_CD": "F3",
            "RPT_TP": "12G", "RECEIPT_DT": "17-OCT-26",
            "CVG_START_DT": "2026-10-01", "CVG_END_DT": "2026-10-14",
            "TTL_RECEIPTS": "200", "TTL_DISB": "70", "COH_COP": "130",
        },
        {
            "CMTE_ID": "C2", "CMTE_NM": "OTHER", "FORM_TP_CD": "F3",
            "RPT_TP": "Q3", "RECEIPT_DT": "15-OCT-26",
            "CVG_START_DT": "2026-07-01", "CVG_END_DT": "2026-09-30",
            "TTL_RECEIPTS": "999", "TTL_DISB": "1", "COH_COP": "998",
        },
    ])
    blob = source.to_csv(index=False, lineterminator="\n").encode("utf-8")
    monkeypatch.setattr(fec, "_download_bytes", lambda url: blob)
    raw_path = tmp_path / "fec-house-senate.csv"

    first, metadata = fec.fetch_form3_reports_from_official_bulk(
        cycle=2026,
        committee_ids=["C1"],
        max_as_of="2026-10-16",
        raw_path=raw_path,
        retrieved_at="2026-10-17T12:00:00Z",
    )
    second = fec.parse_form3_reports_official_bulk(
        blob,
        cycle=2026,
        committee_ids=["C1"],
        max_as_of="2026-10-16",
        source_url=metadata["source_url"],
    )

    assert raw_path.read_bytes() == blob
    assert first["receipt_date"].tolist() == ["2026-10-15"]
    assert first["receipts"].tolist() == [125.5]
    assert first["disbursements"].tolist() == [45.25]
    assert first["cash_on_hand_end_period"].tolist() == [80.25]
    assert first["filing_id"].tolist() == second["filing_id"].tolist()
    assert first["source_record_sha256"].tolist() == second[
        "source_record_sha256"
    ].tolist()
    assert metadata["api_key_required"] is False
    assert metadata["raw_sha256"] == fec._sha256_bytes(blob)


def test_official_current_bulk_refuses_historical_backfill(monkeypatch, tmp_path: Path):
    calls = {"n": 0}

    def unexpected_download(_url):
        calls["n"] += 1
        return b""

    monkeypatch.setattr(fec, "_download_bytes", unexpected_download)
    try:
        fec.fetch_form3_reports_from_official_bulk(
            cycle=2026,
            committee_ids=["C1"],
            max_as_of="2026-10-14",
            raw_path=tmp_path / "reports.csv",
            retrieved_at="2026-10-17T12:00:00Z",
        )
    except ValueError as exc:
        assert "cannot reconstruct a historical cutoff" in str(exc)
    else:
        raise AssertionError("current-version FEC bulk source backfilled a stale cutoff")
    assert calls["n"] == 0


def test_stale_finance_fallback_makes_strict_refresh_fail_closed(
    monkeypatch, tmp_path: Path,
):
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    (manifest_dir / "fundraising_shares.json").write_text(json.dumps({
        "generated_at": "2026-09-01T00:00:00Z",
        "available_at": "2026-09-01",
    }))
    monkeypatch.setattr(preparation, "MANIFESTS_DIR", manifest_dir)
    monkeypatch.setattr(preparation, "refresh_safe_evidence", lambda **_: {
        "results": {
            "polls": {"status": "refresh_failed", "error": "offline"},
            "finance": {
                "status": "refresh_failed",
                "error": "HTTP Error 429: Too Many Requests",
                "transactional_restore": True,
            },
        },
    })
    readiness = {
        "ready_for_expensive_rebuild": True,
        "blockers": [],
        "forecast_coverage": {"summary": {
            "forecast_complete": True, "n_fail": 0,
        }},
    }
    monkeypatch.setattr(preparation, "write_source_readiness", lambda **_: readiness)
    monkeypatch.setattr(preparation, "classify_strict_forecast_coverage", lambda *_: {
        "summary": readiness["forecast_coverage"]["summary"],
        "blocking_forecast_incomplete": False,
        "multiway_withheld_only": False,
        "approved_multiway_withheld_race_ids": [],
        "unexpected_forecast_failure_race_ids": [],
    })
    monkeypatch.setattr(preparation, "_failing_forecast_races", lambda *_: [])
    monkeypatch.setattr(preparation, "_snapshot_poll_artifacts", dict)

    report = preparation.prepare_evidence(
        election_id="senate-2026",
        as_of="2026-10-16",
        mode="refresh-safe",
        strict=True,
    )

    assert report["strict_failure"] is True
    assert "finance_refresh_failed_stale_fallback" in report["strict_failure_reasons"]
    detail = report["strict_failure_detail"]["finance_refresh"]
    assert detail["error"] == "HTTP Error 429: Too Many Requests"
    assert detail["fallback_freshness"]["status"] == "stale_retrieval"

