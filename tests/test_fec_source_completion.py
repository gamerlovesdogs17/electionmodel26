from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd

from midterms.evidence import fec


def _linkage_blob(lines: list[str]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("ccl.txt", "\n".join(lines) + "\n")
    return target.getvalue()


def test_principal_committee_link_uses_form2_receipt_and_excludes_other_links(tmp_path: Path):
    form2 = tmp_path / "form2.csv"
    pd.DataFrame([
        {
            "CANDIDATE_ID": "S8ZZ00001", "CANDIDATE_NAME": "ALPHA, ADA",
            "PARTY_CODE": "DEM", "CANDIDATE_OFFICE_CODE": "S",
            "CANDIDATE_OFFICE_STATE_CODE": "ZZ", "ELECTION_YEAR": "2018",
            "RECEIPT_DATE": "01-MAR-18", "BEGIN_IMAGE_NUMBER": "100",
        },
        {
            "CANDIDATE_ID": "S8ZZ00002", "CANDIDATE_NAME": "BETA, BOB",
            "PARTY_CODE": "REP", "CANDIDATE_OFFICE_CODE": "S",
            "CANDIDATE_OFFICE_STATE_CODE": "ZZ", "ELECTION_YEAR": "2018",
            "RECEIPT_DATE": "08-SEP-18", "BEGIN_IMAGE_NUMBER": "101",
        },
    ]).to_csv(form2, index=False)
    blob = _linkage_blob([
        "S8ZZ00001|2018|2018|C00000001|S|P|1",
        "S8ZZ00001|2018|2018|C00000009|S|A|2",
        "S8ZZ00002|2018|2018|C00000002|S|P|3",
    ])
    early = fec.candidate_committee_links_from_sources(
        cycle=2018, linkage_blob=blob, form2_path=form2, max_as_of="2018-09-07",
    )
    assert early["candidate_id"].tolist() == ["S8ZZ00001"]
    assert early["committee_id"].tolist() == ["C00000001"]
    assert early.loc[0, "available_at"] == "2018-03-01"
    assert early.loc[0, "availability_basis"] == "fec_form2_receipt_date"


def test_form3_fetch_seals_exact_response_and_receipt_fields(monkeypatch, tmp_path: Path):
    response = {
        "pagination": {"pages": 1},
        "results": [{
            "committee_id": "C00000001", "committee_name": "ALPHA",
            "report_type": "Q3", "coverage_start_date": "2018-07-01T00:00:00",
            "coverage_end_date": "2018-09-30T00:00:00",
            "receipt_date": "2018-10-02", "file_number": 123,
            "amendment_indicator": "N", "amendment_chain": [123],
            "total_receipts_ytd": "10.50", "total_disbursements_ytd": "4.25",
            "cash_on_hand_end_period": 6.25,
        }],
    }
    raw = json.dumps(response, separators=(",", ":")).encode()

    def fake_get_raw(path, params, retries=5):
        assert params["max_receipt_date"] == "10/07/2018"
        return raw, "https://api.open.fec.gov/v1/reports/house-senate/?api_key=x"

    monkeypatch.setattr(fec, "_get_raw", fake_get_raw)
    archive = tmp_path / "reports.zip"
    frame, metadata = fec.fetch_form3_reports_for_committees(
        cycle=2018, committee_ids=["C00000001"], max_as_of="2018-10-07",
        archive_path=archive,
    )
    assert frame.loc[0, "receipt_date"] == "2018-10-02"
    assert frame.loc[0, "filing_id"] == 123
    with zipfile.ZipFile(archive) as sealed:
        assert sealed.read("batch-001-page-001.json") == raw
    assert metadata["requests"][0]["response_sha256"] == fec._sha256_bytes(raw)


def test_report_snapshots_keep_later_amendment_out_of_earlier_cutoff():
    reports = pd.DataFrame([
        {
            "committee_id": "C1", "report_type": "Q3",
            "coverage_start_date": "2022-07-01", "coverage_end_date": "2022-09-30",
            "receipt_date": "2022-10-01", "filing_id": 100,
            "amendment_indicator": "N", "amendment_chain_id": "chain",
            "receipts": 10.0, "disbursements": 4.0,
            "cash_on_hand_end_period": 6.0,
        },
        {
            "committee_id": "C1", "report_type": "Q3",
            "coverage_start_date": "2022-07-01", "coverage_end_date": "2022-09-30",
            "receipt_date": "2022-10-20", "filing_id": 101,
            "amendment_indicator": "A", "amendment_chain_id": "chain",
            "receipts": 20.0, "disbursements": 5.0,
            "cash_on_hand_end_period": 15.0,
        },
    ])
    links = pd.DataFrame([{
        "candidate_id": "S1", "committee_id": "C1", "state": "ZZ",
        "party": "DEM", "available_at": "2022-01-01", "is_authorized": True,
    }])
    early = fec.report_level_fundraising_shares_as_of(
        reports, links, election_id="senate-2022", as_of="2022-10-09",
        require_candidate_match=False,
    )
    late = fec.report_level_fundraising_shares_as_of(
        reports, links, election_id="senate-2022", as_of="2022-10-21",
        require_candidate_match=False,
    )
    assert early.loc[0, "dem_receipts"] == 10.0
    assert late.loc[0, "dem_receipts"] == 20.0
    assert early.loc[0, "filing_ids"] == ["100"]
    assert late.loc[0, "filing_ids"] == ["101"]


def test_reused_committee_follows_latest_form2_link_visible_at_cutoff():
    reports = pd.DataFrame([{
        "committee_id": "C1", "report_type": "Q2",
        "coverage_start_date": "2022-04-01", "coverage_end_date": "2022-06-30",
        "receipt_date": "2022-07-15", "filing_id": 100,
        "amendment_indicator": "N", "amendment_chain_id": "q2",
        "receipts": 10.0, "disbursements": 4.0,
        "cash_on_hand_end_period": 6.0,
    }])
    links = pd.DataFrame([
        {
            "candidate_id": "S1", "committee_id": "C1", "state": "AA",
            "party": "REP", "available_at": "2021-03-01", "is_authorized": True,
        },
        {
            "candidate_id": "S2", "committee_id": "C1", "state": "BB",
            "party": "REP", "available_at": "2022-06-01", "is_authorized": True,
        },
    ])
    early = fec.select_candidate_committee_reports_as_of(
        reports, links, as_of="2022-05-01",
    )
    late = fec.select_candidate_committee_reports_as_of(
        reports, links, as_of="2022-09-01",
    )
    assert early.empty  # report itself was not yet received
    assert late[["candidate_id", "state"]].values.tolist() == [["S2", "BB"]]
