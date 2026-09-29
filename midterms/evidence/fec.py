"""OpenFEC / FEC bulk campaign-finance ingest → Dem fundraising share by Senate race."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.tickets import TICKETS_2026

PARSER_VERSION = "fec-v2-report-receipt-asof"
REPORT_FINANCE_SCHEMA_VERSION = "fec-report-finance-v1"
OPENFEC = "https://api.open.fec.gov/v1"
# FEC all-candidates summary (no API key; same underlying filings as OpenFEC).
WEBALL_URL = "https://www.fec.gov/files/bulk-downloads/{cycle}/weball{yy}.zip"
CANDIDATE_COMMITTEE_URL = (
    "https://www.fec.gov/files/bulk-downloads/{cycle}/ccl{yy}.zip"
)
FORM3_ENDPOINT = "/reports/house-senate/"
REQUIRED_FINANCE_CUTOFFS: dict[int, tuple[str, ...]] = {
    2018: ("2018-09-07", "2018-10-07"),
    2020: ("2020-09-04", "2020-10-04"),
    2022: ("2022-09-09", "2022-10-09"),
    2024: ("2024-09-06", "2024-10-06"),
}
CANDIDATE_COMMITTEE_COLUMNS = (
    "candidate_id", "candidate_election_year", "fec_election_year",
    "committee_id", "committee_type", "committee_designation", "linkage_id",
)
# Pipe fields: see https://www.fec.gov/campaign-finance-data/all-candidates-file-description/
WEBALL_COLS = [
    "candidate_id",
    "name",
    "incumbent_challenger_status",
    "party_code",
    "party",
    "receipts",
    "transfers_from_auth",
    "disbursements",
    "transfers_to_auth",
    "cash_on_hand_bop",
    "cash_on_hand_end_period",
    "candidate_contrib",
    "candidate_loans",
    "other_loans",
    "candidate_loan_repay",
    "other_loan_repay",
    "debts_owed_by",
    "indiv_contrib",
    "state",
    "district",
    "special_election_status",
    "primary_election_status",
    "runoff_election_status",
    "general_election_status",
    "general_election_pct",
    "other_pol_cmte_contrib",
    "party_contrib",
    "coverage_end_date",
    "indiv_refunds",
    "cmte_refunds",
]

REPORT_REQUIRED_COLUMNS = {
    "committee_id", "report_type", "coverage_start_date", "coverage_end_date",
    "receipt_date", "filing_id", "amendment_indicator", "receipts",
    "disbursements", "cash_on_hand_end_period",
}
LINK_REQUIRED_COLUMNS = {
    "candidate_id", "committee_id", "state", "party", "available_at",
}


def resolve_form3_reports_as_of(
    reports: pd.DataFrame, *, as_of: str | date,
) -> pd.DataFrame:
    """Resolve report amendments using only filings received by the cutoff.

    ``coverage_end_date`` identifies the reporting period. ``receipt_date`` is
    the sole availability clock. A later amendment cannot replace a filing in
    an earlier replay.
    """
    missing = sorted(REPORT_REQUIRED_COLUMNS - set(reports.columns))
    if missing:
        raise ValueError(f"FEC Form 3 reports missing columns: {missing}")
    cutoff = pd.Timestamp(as_of).date()
    work = reports.copy()
    work["receipt_date"] = pd.to_datetime(work["receipt_date"], errors="coerce").dt.date
    work["coverage_start_date"] = pd.to_datetime(
        work["coverage_start_date"], errors="coerce"
    ).dt.date
    work["coverage_end_date"] = pd.to_datetime(
        work["coverage_end_date"], errors="coerce"
    ).dt.date
    work = work[work["receipt_date"].notna() & (work["receipt_date"] <= cutoff)].copy()
    if work.empty:
        return work.assign(available_at=pd.Series(dtype=str))
    for column in ("receipts", "disbursements", "cash_on_hand_end_period"):
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0.0)
    if "amendment_chain_id" not in work.columns:
        work["amendment_chain_id"] = work.apply(
            lambda row: "|".join(map(str, (
                row["committee_id"], row["report_type"],
                row["coverage_start_date"], row["coverage_end_date"],
            ))), axis=1,
        )
    work["available_at"] = work["receipt_date"].map(date.isoformat)
    # Receipt ordering is authoritative; filing_id is a deterministic tiebreak.
    work = work.sort_values(
        ["amendment_chain_id", "receipt_date", "filing_id"], kind="stable",
    )
    return work.groupby("amendment_chain_id", as_index=False, sort=True).tail(1).reset_index(drop=True)


def select_candidate_committee_reports_as_of(
    reports: pd.DataFrame,
    candidate_committees: pd.DataFrame,
    *,
    as_of: str | date,
) -> pd.DataFrame:
    """Select each candidate committee's latest valid Form 3 report as of cutoff."""
    missing = sorted(LINK_REQUIRED_COLUMNS - set(candidate_committees.columns))
    if missing:
        raise ValueError(f"FEC candidate/committee links missing columns: {missing}")
    cutoff = pd.Timestamp(as_of).date()
    links = candidate_committees.copy()
    links["available_at"] = pd.to_datetime(links["available_at"], errors="coerce").dt.date
    links = links[links["available_at"].notna() & (links["available_at"] <= cutoff)].copy()
    if "is_authorized" in links.columns:
        links = links[links["is_authorized"].fillna(False).astype(bool)]
    links = links.sort_values(
        ["candidate_id", "committee_id", "available_at"], kind="stable"
    ).drop_duplicates(["candidate_id", "committee_id"], keep="last")
    # FEC occasionally reuses one principal committee across successive
    # candidate IDs (including a change of state). At a historical cutoff the
    # latest Form 2 receipt then available is the active linkage. Count the
    # committee once and never let a later Form 2 leak backward.
    links = links.sort_values(
        ["committee_id", "available_at", "candidate_id"], kind="stable",
    ).drop_duplicates("committee_id", keep="last")
    links = links.rename(columns={"available_at": "link_available_at"})
    resolved = resolve_form3_reports_as_of(reports, as_of=cutoff)
    merged = links.merge(resolved, on="committee_id", how="inner", validate="one_to_many")
    if merged.empty:
        return merged
    merged = merged.sort_values(
        ["candidate_id", "committee_id", "coverage_end_date", "receipt_date", "filing_id"],
        kind="stable",
    )
    return merged.groupby(
        ["candidate_id", "committee_id"], as_index=False, sort=True
    ).tail(1).reset_index(drop=True)


def report_level_fundraising_shares_as_of(
    reports: pd.DataFrame,
    candidate_committees: pd.DataFrame,
    *,
    election_id: str,
    as_of: str | date,
) -> pd.DataFrame:
    """Derive race finance features from receipt-safe official report summaries."""
    selected = select_candidate_committee_reports_as_of(
        reports, candidate_committees, as_of=as_of,
    )
    rows: list[dict[str, Any]] = []
    if selected.empty:
        return pd.DataFrame(rows)
    by_candidate = selected.groupby(
        ["candidate_id", "state", "party"], as_index=False, sort=True
    ).agg(
        receipts=("receipts", "sum"),
        disbursements=("disbursements", "sum"),
        cash_on_hand_end_period=("cash_on_hand_end_period", "sum"),
        available_at=("available_at", "max"),
        filing_ids=("filing_id", lambda values: sorted(map(str, values))),
        committee_ids=("committee_id", lambda values: sorted(map(str, values))),
    )
    for state, group in by_candidate.groupby("state", sort=True):
        dem = group[group["party"].astype(str).map(_normalize_party).eq("DEM")]
        rep = group[group["party"].astype(str).map(_normalize_party).eq("REP")]
        dem_receipts = float(dem["receipts"].sum())
        rep_receipts = float(rep["receipts"].sum())
        dem_cash = float(dem["cash_on_hand_end_period"].sum())
        rep_cash = float(rep["cash_on_hand_end_period"].sum())
        receipts_total = dem_receipts + rep_receipts
        cash_total = dem_cash + rep_cash
        rows.append({
            "election_id": election_id,
            "state": str(state),
            "race_id": f"{election_id}-{state}",
            "fundraising_share": dem_receipts / receipts_total if receipts_total else 0.5,
            "cash_share": dem_cash / cash_total if cash_total else 0.5,
            "dem_receipts": dem_receipts,
            "rep_receipts": rep_receipts,
            "dem_disbursements": float(dem["disbursements"].sum()),
            "rep_disbursements": float(rep["disbursements"].sum()),
            "dem_cash_on_hand": dem_cash,
            "rep_cash_on_hand": rep_cash,
            "filing_ids": sorted(sum(group["filing_ids"].tolist(), [])),
            "committee_ids": sorted(sum(group["committee_ids"].tolist(), [])),
            "available_at": max(group["available_at"]),
            "availability_basis": "fec_receipt_date",
            "source": "fec_form3_report_summaries",
            "parser_version": PARSER_VERSION,
        })
    return pd.DataFrame(rows)


def _frame_semantic_sha256(frame: pd.DataFrame) -> str:
    records = frame.copy()
    records = records.reindex(sorted(records.columns), axis=1)
    records = records.astype(object).where(pd.notna(records), None)
    canonical_rows = sorted(
        json.dumps(
            row, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False,
        )
        for row in records.to_dict(orient="records")
    )
    payload = ("[" + ",".join(canonical_rows) + "]").encode("utf-8")
    return _sha256_bytes(payload)


def _required_race_states(races: pd.DataFrame, election_id: str) -> list[str]:
    selected = races[races["election_id"].astype(str).eq(election_id)].copy()
    selected = selected[~selected.get(
        "not_up", pd.Series(False, index=selected.index),
    ).fillna(False).astype(bool)]
    return sorted(selected["state"].dropna().astype(str).unique())


def prepare_official_finance_history(
    *, current_as_of: str | date = "2026-09-27",
    cycles: tuple[int, ...] = (2018, 2020, 2022, 2024, 2026),
) -> dict[str, Any]:
    """Acquire and seal official receipt-safe Senate finance evidence.

    This is source preparation only. It never fits a model or reads election
    outcomes. Historical feature rows are materialized independently at each
    formal cutoff from reports that the FEC had received by that cutoff.
    """
    current_cutoff = pd.Timestamp(current_as_of).date()
    external_dir = RAW_DIR / "external"
    external_dir.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    races_path = NORMALIZED_DIR / "races_official.parquet"
    if not races_path.is_file():
        raise FileNotFoundError("official race universe is required for finance coverage")
    races = pd.read_parquet(races_path)
    all_links: list[pd.DataFrame] = []
    all_reports: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    retrieved_at = datetime.now(timezone.utc).isoformat()

    for cycle in cycles:
        cutoffs = list(REQUIRED_FINANCE_CUTOFFS.get(int(cycle), ()))
        if int(cycle) == 2026:
            cutoffs.append(current_cutoff.isoformat())
        if not cutoffs:
            raise ValueError(f"no finance cutoff declared for cycle {cycle}")
        max_cutoff = max(pd.Timestamp(value).date() for value in cutoffs)
        yy = f"{int(cycle) % 100:02d}"
        linkage_url = CANDIDATE_COMMITTEE_URL.format(cycle=int(cycle), yy=yy)
        linkage_blob = _download_bytes(linkage_url)
        linkage_path = external_dir / f"fec_candidate_committee_linkage_{cycle}.zip"
        linkage_path.write_bytes(linkage_blob)
        form2_path = external_dir / f"fec_form2_{cycle}.csv"
        form2_url = (
            f"https://www.fec.gov/files/bulk-downloads/{cycle}/Form2Filer_{cycle}.csv"
        )
        form2_path.write_bytes(_download_bytes(form2_url))
        links = candidate_committee_links_from_sources(
            cycle=int(cycle), linkage_blob=linkage_blob, form2_path=form2_path,
            max_as_of=max_cutoff,
        )
        if links.empty:
            raise ValueError(f"no receipt-timed Senate principal committees for {cycle}")
        reports_archive = external_dir / f"fec_form3_reports_{cycle}.zip"
        reports, report_meta = fetch_form3_reports_for_committees(
            cycle=int(cycle),
            committee_ids=sorted(links["committee_id"].astype(str).unique()),
            max_as_of=max_cutoff,
            archive_path=reports_archive,
        )
        if reports.empty:
            raise ValueError(f"no official Form 3 reports returned for {cycle}")
        all_links.append(links)
        all_reports.append(reports)
        sources.append({
            "cycle": int(cycle),
            "linkage": {
                "path": linkage_path.name,
                "url": linkage_url,
                "retrieved_at": retrieved_at,
                "sha256": _sha256_file(linkage_path),
            },
            "form2": {
                "path": form2_path.name,
                "url": form2_url,
                "retrieved_at": retrieved_at,
                "sha256": _sha256_file(form2_path),
                "role": "receipt clock for principal-committee designation",
            },
            "form3": {
                **report_meta,
                "path": reports_archive.name,
                "url": f"{OPENFEC}{FORM3_ENDPOINT}",
                "retrieved_at": retrieved_at,
                "sha256": _sha256_file(reports_archive),
            },
        })

    links_frame = pd.concat(all_links, ignore_index=True)
    reports_frame = pd.concat(all_reports, ignore_index=True)
    links_path = NORMALIZED_DIR / "fec_candidate_committees.parquet"
    reports_path = NORMALIZED_DIR / "fec_form3_reports.parquet"
    links_frame.to_parquet(links_path, index=False)
    reports_frame.to_parquet(reports_path, index=False)

    snapshots: list[pd.DataFrame] = []
    coverage: dict[str, Any] = {}
    for cycle in cycles:
        election_id = f"senate-{cycle}"
        cycle_cutoffs = list(REQUIRED_FINANCE_CUTOFFS.get(int(cycle), ()))
        if int(cycle) == 2026:
            cycle_cutoffs.append(current_cutoff.isoformat())
        required_states = _required_race_states(races, election_id)
        for cutoff_value in cycle_cutoffs:
            if int(cycle) == 2026:
                label = f"{election_id}-current"
            else:
                lead = 60 if cutoff_value == REQUIRED_FINANCE_CUTOFFS[int(cycle)][0] else 30
                label = f"{election_id}-lead-{lead}"
            share = report_level_fundraising_shares_as_of(
                reports_frame[reports_frame["cycle"].eq(int(cycle))],
                links_frame[links_frame["cycle"].eq(int(cycle))],
                election_id=election_id,
                as_of=cutoff_value,
            )
            share = share[share["state"].astype(str).isin(required_states)].copy()
            share["feature_as_of"] = cutoff_value
            share["cutoff_label"] = label
            covered_states = sorted(share["state"].astype(str).unique()) if len(share) else []
            missing_states = sorted(set(required_states) - set(covered_states))
            coverage[label] = {
                "as_of": cutoff_value,
                "required_states": required_states,
                "covered_states": covered_states,
                "missing_states": missing_states,
                "n_rows": int(len(share)),
                "production_eligible": not missing_states and bool(required_states),
            }
            snapshots.append(share)
    shares = pd.concat(snapshots, ignore_index=True) if snapshots else pd.DataFrame()
    if len(shares):
        shares = shares.sort_values(
            ["election_id", "feature_as_of", "state"], kind="stable",
        ).reset_index(drop=True)
    shares_path = NORMALIZED_DIR / "fundraising_shares.parquet"
    shares.to_parquet(shares_path, index=False)
    production_eligible = bool(coverage) and all(
        block["production_eligible"] for block in coverage.values()
    )
    manifest = {
        "schema_version": REPORT_FINANCE_SCHEMA_VERSION,
        "generated_at": retrieved_at,
        "parser_version": PARSER_VERSION,
        "availability_basis": "fec_receipt_date",
        "linkage_availability_basis": "fec_form2_receipt_date",
        "linkage_policy": "principal_campaign_committee_only",
        "source_provider": "Federal Election Commission",
        "source_url": f"{OPENFEC}{FORM3_ENDPOINT}",
        "tier": "first_party",
        "cycles": [int(value) for value in cycles],
        "current_as_of": current_cutoff.isoformat(),
        "sources": sources,
        "coverage": coverage,
        "normalized": {
            "fundraising_shares": {
                "path": shares_path.name,
                "sha256": _sha256_file(shares_path),
                "semantic_sha256": _frame_semantic_sha256(shares),
                "n_rows": int(len(shares)),
            },
            "candidate_committees": {
                "path": links_path.name,
                "sha256": _sha256_file(links_path),
                "semantic_sha256": _frame_semantic_sha256(links_frame),
                "n_rows": int(len(links_frame)),
            },
            "form3_reports": {
                "path": reports_path.name,
                "sha256": _sha256_file(reports_path),
                "semantic_sha256": _frame_semantic_sha256(reports_frame),
                "n_rows": int(len(reports_frame)),
            },
        },
        "production_eligible": production_eligible,
        "reasons": [] if production_eligible else [
            "one or more formal cutoffs lack report-level FEC finance coverage"
        ],
    }
    manifest_path = MANIFESTS_DIR / "fundraising_shares.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8",
    )
    return {
        "manifest": str(manifest_path),
        "production_eligible": production_eligible,
        "coverage": coverage,
        "n_shares": int(len(shares)),
        "n_links": int(len(links_frame)),
        "n_reports": int(len(reports_frame)),
    }


def _fec_api_key() -> str:
    return os.environ.get("FEC_API_KEY") or "DEMO_KEY"


def _get(path: str, params: dict[str, Any], *, retries: int = 4) -> dict[str, Any]:
    q = dict(params)
    q["api_key"] = _fec_api_key()
    url = f"{OPENFEC}{path}?{urlencode(q, doseq=True)}"
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.2 (research)"})
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            last_exc = exc
            if int(getattr(exc, "code", 0) or 0) == 429 and attempt + 1 < retries:
                time.sleep(1.5 * (2**attempt))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(0.75 * (attempt + 1))
                continue
            raise
    raise last_exc or RuntimeError("OpenFEC request failed")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _download_bytes(url: str, *, retries: int = 4) -> bytes:
    request = Request(
        url, headers={"User-Agent": "midterms-senate-model/0.9.22 (research)"},
    )
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=90) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
    raise last_exc or RuntimeError(f"download failed: {url}")


def _get_raw(
    path: str, params: dict[str, Any], *, retries: int = 5,
) -> tuple[bytes, str]:
    query = dict(params)
    query["api_key"] = _fec_api_key()
    url = f"{OPENFEC}{path}?{urlencode(query, doseq=True)}"
    request = Request(
        url, headers={"User-Agent": "midterms-senate-model/0.9.22 (research)"},
    )
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=90) as response:
                return response.read(), url
        except HTTPError as exc:
            last_exc = exc
            if int(getattr(exc, "code", 0) or 0) == 429 and attempt + 1 < retries:
                time.sleep(2.0 * (2**attempt))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
    raise last_exc or RuntimeError("OpenFEC request failed")


def _parse_form2_receipt(value: Any) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.date()


def _read_linkage_zip(blob: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        member = next(
            name for name in archive.namelist() if name.lower().endswith(".txt")
        )
        payload = archive.read(member)
    frame = pd.read_csv(
        io.BytesIO(payload), sep="|", names=list(CANDIDATE_COMMITTEE_COLUMNS),
        dtype=str, keep_default_na=False,
    )
    return frame


def candidate_committee_links_from_sources(
    *, cycle: int, linkage_blob: bytes, form2_path: Path, max_as_of: str | date,
) -> pd.DataFrame:
    """Build receipt-timed principal-committee links from official FEC sources.

    The cycle linkage file identifies the candidate's principal campaign
    committee (designation ``P``). The candidate's Form 2 receipt is the
    availability clock because Form 2 is the filing on which the candidate
    designates that principal committee. Other authorized/joint committees are
    intentionally excluded: the bulk Form 2 extract does not expose the filing
    line needed to date those additional relationships without ambiguity.
    """
    cutoff = pd.Timestamp(max_as_of).date()
    links = _read_linkage_zip(linkage_blob)
    links = links[
        links["fec_election_year"].astype(str).eq(str(int(cycle)))
        & links["candidate_election_year"].astype(str).eq(str(int(cycle)))
        & links["committee_type"].astype(str).eq("S")
        & links["committee_designation"].astype(str).eq("P")
    ].copy()
    form2 = pd.read_csv(form2_path, dtype=str, keep_default_na=False)
    form2 = form2[
        form2["CANDIDATE_OFFICE_CODE"].astype(str).eq("S")
        & form2["ELECTION_YEAR"].astype(str).eq(str(int(cycle)))
    ].copy()
    form2["_receipt"] = form2["RECEIPT_DATE"].map(_parse_form2_receipt)
    form2 = form2[
        form2["_receipt"].notna() & (form2["_receipt"] <= cutoff)
    ].copy()
    # The earliest cycle filing is the first point at which the principal
    # committee designation can be known. Later Form 2 amendments remain in the
    # sealed raw source but cannot move that availability backward.
    form2 = form2.sort_values(
        ["CANDIDATE_ID", "_receipt", "BEGIN_IMAGE_NUMBER"], kind="stable",
    ).drop_duplicates("CANDIDATE_ID", keep="first")
    form2 = form2.rename(columns={
        "CANDIDATE_ID": "candidate_id",
        "CANDIDATE_NAME": "candidate_name",
        "PARTY_CODE": "party",
        "CANDIDATE_OFFICE_STATE_CODE": "state",
        "BEGIN_IMAGE_NUMBER": "form2_begin_image_number",
    })
    selected = links.merge(
        form2[[
            "candidate_id", "candidate_name", "party", "state", "_receipt",
            "form2_begin_image_number",
        ]],
        on="candidate_id", how="inner", validate="many_to_one",
    )
    if selected.empty:
        return pd.DataFrame(columns=[
            *LINK_REQUIRED_COLUMNS, "candidate_name", "committee_designation",
            "committee_type", "linkage_id", "is_authorized", "availability_basis",
            "form2_begin_image_number", "cycle", "parser_version",
        ])
    # A candidate can have successive or concurrent principal committees in a
    # cycle. The FEC linkage file is authoritative for that relationship; keep
    # every principal link so report totals do not silently omit one.
    selected["available_at"] = selected["_receipt"].map(date.isoformat)
    selected["is_authorized"] = True
    selected["availability_basis"] = "fec_form2_receipt_date"
    selected["cycle"] = int(cycle)
    selected["parser_version"] = PARSER_VERSION
    return selected[[
        "candidate_id", "candidate_name", "committee_id", "state", "party",
        "committee_designation", "committee_type", "linkage_id", "available_at",
        "is_authorized", "availability_basis", "form2_begin_image_number", "cycle",
        "parser_version",
    ]].sort_values(["state", "party", "candidate_id"], kind="stable").reset_index(drop=True)


def _form3_row(result: dict[str, Any], *, cycle: int, source_url: str) -> dict[str, Any]:
    chain = result.get("amendment_chain") or []
    chain_id = str(chain[0]) if chain else "|".join(map(str, (
        result.get("committee_id"), result.get("report_type"),
        str(result.get("coverage_start_date") or "")[:10],
        str(result.get("coverage_end_date") or "")[:10],
    )))

    def numeric(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "cycle": int(cycle),
        "committee_id": result.get("committee_id"),
        "committee_name": result.get("committee_name"),
        "report_type": result.get("report_type"),
        "coverage_start_date": str(result.get("coverage_start_date") or "")[:10] or None,
        "coverage_end_date": str(result.get("coverage_end_date") or "")[:10] or None,
        "receipt_date": str(result.get("receipt_date") or "")[:10] or None,
        "filing_id": result.get("file_number"),
        "amendment_indicator": result.get("amendment_indicator"),
        "amendment_chain_id": chain_id,
        "receipts": numeric(result.get("total_receipts_ytd")
        if result.get("total_receipts_ytd") is not None
        else result.get("total_receipts_period")),
        "disbursements": numeric(result.get("total_disbursements_ytd")
        if result.get("total_disbursements_ytd") is not None
        else result.get("total_disbursements_period")),
        "cash_on_hand_end_period": numeric(result.get("cash_on_hand_end_period")),
        "source_url": source_url,
        "parser_version": PARSER_VERSION,
    }


def fetch_form3_reports_for_committees(
    *, cycle: int, committee_ids: list[str], max_as_of: str | date,
    archive_path: Path, batch_size: int = 25, reuse_existing: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch cutoff-bounded Form 3 reports and seal every API response byte."""
    cutoff = pd.Timestamp(max_as_of).date()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    requests_meta: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    descriptor = {
        "schema_version": "fec-form3-response-archive-v1",
        "cycle": int(cycle),
        "max_as_of": cutoff.isoformat(),
        "committee_ids": sorted(set(committee_ids)),
        "batch_size": int(batch_size),
    }
    if reuse_existing and archive_path.is_file():
        needs_descriptor = False
        with zipfile.ZipFile(archive_path) as archive:
            if "request-metadata.json" in archive.namelist():
                stored_descriptor = json.loads(
                    archive.read("request-metadata.json").decode("utf-8")
                )
                if any(
                    stored_descriptor.get(key) != value
                    for key, value in descriptor.items()
                ):
                    raise ValueError(
                        f"sealed Form 3 archive query identity changed: {archive_path}"
                    )
            else:
                needs_descriptor = True
            members = sorted(
                name for name in archive.namelist()
                if name.startswith("batch-") and name.lower().endswith(".json")
            )
            for member in members:
                raw = archive.read(member)
                payload = json.loads(raw.decode("utf-8"))
                parts = Path(member).stem.split("-")
                batch_number, page = int(parts[1]), int(parts[3])
                start = (batch_number - 1) * batch_size
                batch = sorted(set(committee_ids[start:start + batch_size]))
                query = urlencode({
                    "cycle": int(cycle), "committee_id": batch,
                    "max_receipt_date": cutoff.strftime("%m/%d/%Y"),
                    "per_page": 100, "page": page, "sort": "receipt_date",
                    "api_key": "REDACTED",
                }, doseq=True)
                url = f"{OPENFEC}{FORM3_ENDPOINT}?{query}"
                results = payload.get("results") or []
                unexpected = sorted({
                    str(item.get("committee_id")) for item in results
                    if str(item.get("committee_id")) not in set(batch)
                })
                if unexpected:
                    raise ValueError(
                        f"sealed Form 3 archive batch identity changed: {unexpected}"
                    )
                for item in results:
                    received = pd.to_datetime(
                        item.get("receipt_date"), errors="coerce",
                    )
                    if pd.notna(received) and received.date() > cutoff:
                        raise ValueError(
                            f"sealed Form 3 archive contains future receipt: {member}"
                        )
                requests_meta.append({
                    "member": member, "url": url,
                    "response_sha256": _sha256_bytes(raw),
                    "n_results": len(results),
                })
                rows.extend(
                    _form3_row(item, cycle=cycle, source_url=url.split("?", 1)[0])
                    for item in results
                )
        if not members:
            raise ValueError(f"sealed Form 3 archive has no responses: {archive_path}")
        if needs_descriptor:
            with zipfile.ZipFile(
                archive_path, "a", compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                archive.writestr(
                    "request-metadata.json",
                    json.dumps(descriptor, sort_keys=True, separators=(",", ":")),
                )
        frame = pd.DataFrame(rows)
        if len(frame):
            frame = frame.sort_values(
                ["committee_id", "receipt_date", "filing_id"], kind="stable",
            ).drop_duplicates(
                ["committee_id", "filing_id"], keep="last",
            ).reset_index(drop=True)
        return frame, {
            "cycle": int(cycle), "max_as_of": cutoff.isoformat(),
            "archive_path": archive_path.name,
            "archive_sha256": _sha256_file(archive_path),
            "requests": requests_meta,
            "n_reports": len(frame),
            "reused_sealed_archive": True,
        }
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for batch_number, start in enumerate(range(0, len(committee_ids), batch_size), 1):
            batch = sorted(set(committee_ids[start:start + batch_size]))
            page = 1
            while True:
                raw, url = _get_raw(FORM3_ENDPOINT, {
                    "cycle": int(cycle),
                    "committee_id": batch,
                    "max_receipt_date": cutoff.strftime("%m/%d/%Y"),
                    "per_page": 100,
                    "page": page,
                    "sort": "receipt_date",
                })
                member = f"batch-{batch_number:03d}-page-{page:03d}.json"
                archive.writestr(member, raw)
                payload = json.loads(raw.decode("utf-8"))
                results = payload.get("results") or []
                requests_meta.append({
                    "member": member,
                    "url": url.replace(f"api_key={_fec_api_key()}", "api_key=REDACTED"),
                    "response_sha256": _sha256_bytes(raw),
                    "n_results": len(results),
                })
                rows.extend(
                    _form3_row(item, cycle=cycle, source_url=url.split("?", 1)[0])
                    for item in results
                )
                pages = int((payload.get("pagination") or {}).get("pages") or 1)
                if page >= pages:
                    break
                page += 1
                time.sleep(0.15)
            time.sleep(0.15)
        archive.writestr(
            "request-metadata.json",
            json.dumps(descriptor, sort_keys=True, separators=(",", ":")),
        )
    frame = pd.DataFrame(rows)
    if len(frame):
        frame = frame.sort_values(
            ["committee_id", "receipt_date", "filing_id"], kind="stable",
        ).drop_duplicates(["committee_id", "filing_id"], keep="last").reset_index(drop=True)
    return frame, {
        "cycle": int(cycle),
        "max_as_of": cutoff.isoformat(),
        "archive_path": archive_path.name,
        "archive_sha256": _sha256_file(archive_path),
        "requests": requests_meta,
        "n_reports": len(frame),
    }


def _normalize_party(raw: str) -> str:
    p = (raw or "").strip().upper()
    if p.startswith("DEM") or p in {"D", "DEM"}:
        return "DEM"
    if p.startswith("REP") or p in {"R", "REP"}:
        return "REP"
    return p[:3]


def _coverage_iso(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10] if len(s) >= 10 else None


def fetch_senate_bulk_totals(cycle: int = 2026) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    FEC weball bulk download — first-party alternative when OpenFEC rate-limits.

    Same official filings as the API; no key required.
    """
    yy = f"{int(cycle) % 100:02d}"
    url = WEBALL_URL.format(cycle=int(cycle), yy=yy)
    req = Request(url, headers={"User-Agent": "midterms-senate-model/0.2 (research)"})
    try:
        with urlopen(req, timeout=90) as resp:
            blob = resp.read()
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), {"error": f"bulk:{exc}", "n": 0, "source": "fec_weball"}
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
            text = zf.read(name).decode("latin-1")
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), {"error": f"bulk_unzip:{exc}", "n": 0, "source": "fec_weball"}

    rows: list[dict[str, Any]] = []
    retrieved = datetime.now(timezone.utc).isoformat()
    for line in text.splitlines():
        if not line or not line.startswith("S"):
            continue
        parts = line.split("|")
        if len(parts) < 20:
            continue
        # Pad short rows so zipfile schema drift doesn't crash.
        while len(parts) < len(WEBALL_COLS):
            parts.append("")
        rec = dict(zip(WEBALL_COLS, parts[: len(WEBALL_COLS)]))
        state = (rec.get("state") or "").strip().upper()
        if len(state) != 2:
            cid = rec.get("candidate_id") or ""
            state = cid[2:4].upper() if len(cid) >= 4 else ""
        cov = _coverage_iso(str(rec.get("coverage_end_date") or ""))
        try:
            receipts = float(rec.get("receipts") or 0.0)
        except ValueError:
            receipts = 0.0
        try:
            disbursements = float(rec.get("disbursements") or 0.0)
        except ValueError:
            disbursements = 0.0
        try:
            cash = float(rec.get("cash_on_hand_end_period") or 0.0)
        except ValueError:
            cash = 0.0
        rows.append(
            {
                "cycle": cycle,
                "candidate_id": rec.get("candidate_id"),
                "name": rec.get("name"),
                "party": _normalize_party(str(rec.get("party") or "")),
                "state": state,
                "receipts": receipts,
                "disbursements": disbursements,
                "cash_on_hand_end_period": cash,
                "coverage_start_date": None,
                "coverage_end_date": cov,
                "last_file_date": cov,
                "amendment_indicator": None,
                "filing_id": rec.get("candidate_id"),
                "available_at": cov or datetime.now(timezone.utc).date().isoformat(),
                "retrieved_at": retrieved,
                "parser_version": PARSER_VERSION,
                "source": "fec_weball",
            }
        )
    return pd.DataFrame(rows), {"n": len(rows), "source": "fec_weball", "url": url, "bytes": len(blob)}


def fetch_senate_candidate_totals(cycle: int = 2026) -> tuple[pd.DataFrame, dict[str, Any]]:
    """OpenFEC totals first; on failure/empty, FEC weball bulk (no API key)."""
    rows: list[dict[str, Any]] = []
    page = 1
    try:
        while page <= 20:
            payload = _get(
                "/candidates/totals/",
                {
                    "cycle": cycle,
                    "office": "S",
                    "is_active_candidate": "true",
                    "sort": "-receipts",
                    "per_page": 100,
                    "page": page,
                },
            )
            results = payload.get("results") or []
            if not results:
                break
            for r in results:
                rows.append(
                    {
                        "cycle": cycle,
                        "candidate_id": r.get("candidate_id"),
                        "name": r.get("name"),
                        "party": (r.get("party") or "")[:3],
                        "state": r.get("state"),
                        "receipts": float(r.get("receipts") or 0.0),
                        "disbursements": float(r.get("disbursements") or 0.0),
                        "cash_on_hand_end_period": float(r.get("cash_on_hand_end_period") or 0.0),
                        "coverage_start_date": r.get("coverage_start_date"),
                        "coverage_end_date": r.get("coverage_end_date"),
                        "last_file_date": r.get("last_file_date") or r.get("candidate_inactive_date"),
                        "amendment_indicator": r.get("amendment_indicator")
                        or r.get("candidate_election_year"),
                        "filing_id": r.get("candidate_id"),
                        "available_at": r.get("coverage_end_date")
                        or r.get("last_file_date")
                        or datetime.now(timezone.utc).date().isoformat(),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "parser_version": PARSER_VERSION,
                        "source": "openfec",
                    }
                )
            pagination = payload.get("pagination") or {}
            if page >= int(pagination.get("pages") or 1):
                break
            page += 1
            time.sleep(0.35)  # stay under DEMO_KEY / shared-key rate limits
    except Exception as exc:  # noqa: BLE001
        # Partial DEMO_KEY pages are worse than the full weball dump.
        bulk, bmeta = fetch_senate_bulk_totals(cycle)
        if len(bulk):
            bmeta = {**bmeta, "openfec_error": str(exc), "openfec_partial_n": len(rows)}
            return bulk, bmeta
        if rows:
            return pd.DataFrame(rows), {"error": str(exc), "n": len(rows), "source": "openfec"}
        return pd.DataFrame(), {**bmeta, "openfec_error": str(exc)}
    if rows:
        return pd.DataFrame(rows), {"pages": page, "n": len(rows), "source": "openfec"}
    bulk, bmeta = fetch_senate_bulk_totals(cycle)
    bmeta = {**bmeta, "openfec_error": "empty_results"}
    return bulk, bmeta


def fixture_fundraising_shares(election_id: str = "senate-2026") -> pd.DataFrame:
    """Deterministic shares when OpenFEC is unavailable."""
    rows = []
    for st, _ticket in TICKETS_2026.items():
        h = int(hashlib.sha256(f"{election_id}|{st}".encode()).hexdigest()[:8], 16)
        share = 0.25 + (h % 5000) / 10000.0
        rows.append(
            {
                "election_id": election_id,
                "state": st,
                "race_id": f"senate-2026-{st}" if election_id == "senate-2026" else f"{election_id}-{st}",
                "fundraising_share": round(share, 3),
                "dem_receipts": round(1_000_000 * share, 2),
                "rep_receipts": round(1_000_000 * (1 - share), 2),
                "source": "fixture_hash",
                "available_at": "2026-09-01",
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def curated_fundraising_shares(election_id: str = "senate-2026") -> pd.DataFrame:
    """
    Non-publication lean-anchored estimates when both OpenFEC and FEC weball fail.

    Explicit curated tier — blocked for PUBLICATION_ELIGIBLE runs.
    """
    from midterms.evidence.official_ballot import BASE_LEANS

    year = election_id.split("-")[-1]
    rows = []
    states = list(TICKETS_2026.keys()) if str(year) == "2026" else list(BASE_LEANS.keys())
    for st in states:
        lean = float(BASE_LEANS.get(st, 0.0))
        share = 0.5 + max(-0.25, min(0.25, lean / 80.0))
        rows.append(
            {
                "election_id": election_id,
                "state": st,
                "race_id": f"senate-{year}-{st}",
                "fundraising_share": round(float(share), 3),
                "dem_receipts": round(2_000_000 * share, 2),
                "rep_receipts": round(2_000_000 * (1 - share), 2),
                "source": "curated_fec_browse_estimate",
                "source_url": "https://www.fec.gov/data/browse-data/?tab=candidates",
                "available_at": f"{year}-09-01",
                "parser_version": PARSER_VERSION,
                "tier": "curated",
            }
        )
    return pd.DataFrame(rows)


def shares_from_totals(
    totals: pd.DataFrame,
    election_id: str,
    cycle: int,
    *,
    as_of: str | None = None,
) -> pd.DataFrame:
    """
    Build Dem fundraising shares with amendment / coverage discipline.

    When multiple totals rows exist for a candidate, keep the latest
    coverage_end_date still known by `as_of` (blueprint finance amendment chain).
    """
    if totals.empty:
        return curated_fundraising_shares(election_id)
    work = totals.copy()
    if as_of and "available_at" in work.columns:
        work = work[pd.to_datetime(work["available_at"]).dt.date <= date.fromisoformat(str(as_of)[:10])]
    if work.empty:
        return curated_fundraising_shares(election_id)
    # Prefer latest coverage window per candidate (amendment / restatement chain)
    chain_by_cand: dict[str, list[str]] = {}
    if "candidate_id" in work.columns and "coverage_end_date" in work.columns:
        for cid, cg in work.groupby("candidate_id"):
            dates = sorted(
                {str(x)[:10] for x in cg["coverage_end_date"].dropna().tolist() if str(x)}
            )
            chain_by_cand[str(cid)] = dates
        work = work.sort_values(
            [c for c in ("coverage_end_date", "last_file_date", "available_at") if c in work.columns]
        )
        work = work.groupby("candidate_id", as_index=False).tail(1)
    rows = []
    for state, g in work.groupby("state"):
        dem = g[g["party"].astype(str).str.upper().str.startswith("DEM")]
        rep = g[g["party"].astype(str).str.upper().str.startswith("REP")]
        dem_rec = float(dem["receipts"].max()) if len(dem) else 0.0
        rep_rec = float(rep["receipts"].max()) if len(rep) else 0.0
        dem_cash = float(dem["cash_on_hand_end_period"].max()) if len(dem) and "cash_on_hand_end_period" in dem else 0.0
        rep_cash = float(rep["cash_on_hand_end_period"].max()) if len(rep) and "cash_on_hand_end_period" in rep else 0.0
        dem_disb = float(dem["disbursements"].max()) if len(dem) and "disbursements" in dem else 0.0
        rep_disb = float(rep["disbursements"].max()) if len(rep) and "disbursements" in rep else 0.0
        total = dem_rec + rep_rec
        share = dem_rec / total if total > 0 else 0.5
        cash_tot = dem_cash + rep_cash
        cash_share = dem_cash / cash_tot if cash_tot > 0 else 0.5
        chains = []
        for cid in g.get("candidate_id", pd.Series(dtype=str)).dropna().astype(str).tolist():
            chains.extend(chain_by_cand.get(cid, []))
        cov_dates = sorted(set(chains))
        src_vals = (
            set(g["source"].dropna().astype(str))
            if "source" in g.columns
            else set()
        )
        if "openfec" in src_vals:
            src = "openfec"
        elif "fec_weball" in src_vals:
            src = "fec_weball"
        else:
            src = "openfec"
        rows.append(
            {
                "election_id": election_id,
                "state": state,
                "race_id": f"senate-{cycle}-{state}",
                "fundraising_share": round(float(share), 3),
                "cash_share": round(float(cash_share), 3),
                "dem_receipts": dem_rec,
                "rep_receipts": rep_rec,
                "dem_cash_on_hand": dem_cash,
                "rep_cash_on_hand": rep_cash,
                "dem_disbursements": dem_disb,
                "rep_disbursements": rep_disb,
                "matched_window_id": f"cycle-{cycle}-coverage-end-asof",
                "amendment_chain": ">".join(cov_dates) if cov_dates else "latest_totals_row",
                "n_filings_in_chain": int(len(cov_dates)),
                "source": src,
                "available_at": str(g["available_at"].max()),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def _finance_tier_and_url(shares: pd.DataFrame, meta: dict[str, Any]) -> tuple[str, str]:
    sources = set(shares["source"].astype(str)) if len(shares) else set()
    if "openfec" in sources:
        return "aggregator", "https://api.open.fec.gov/v1/candidates/totals/"
    if "fec_weball" in sources:
        url = str(meta.get("url") or "https://www.fec.gov/files/bulk-downloads/")
        return "first_party", url
    return "curated", "https://www.fec.gov/data/browse-data/?tab=candidates"


def write_finance_store(election_id: str = "senate-2026", cycle: int = 2026) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    totals, meta = fetch_senate_candidate_totals(cycle)
    shares = shares_from_totals(totals, election_id, cycle)
    raw_path = RAW_DIR / f"fec_totals_{cycle}.csv"
    share_path = NORMALIZED_DIR / "fundraising_shares.parquet"
    if len(totals):
        totals.to_csv(raw_path, index=False)
    else:
        pd.DataFrame().to_csv(raw_path, index=False)
    shares.to_parquet(share_path, index=False)
    tier, source_url = _finance_tier_and_url(shares, meta if isinstance(meta, dict) else {})
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "cycle": cycle,
        "n_shares": int(len(shares)),
        "source_mix": shares["source"].value_counts().to_dict() if len(shares) else {},
        "fetch_meta": meta,
        "parser_version": PARSER_VERSION,
        "source_url": source_url,
        "tier": tier,
    }
    man_path = MANIFESTS_DIR / "fundraising_shares.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {"shares": str(share_path), "raw": str(raw_path), "manifest": str(man_path), **man}


def load_fundraising_shares() -> pd.DataFrame:
    path = NORMALIZED_DIR / "fundraising_shares.parquet"
    if not path.exists():
        write_finance_store()
    return pd.read_parquet(path)


def attach_fundraising_to_races(
    races: pd.DataFrame, *, as_of: str | date | None = None
) -> pd.DataFrame:
    shares = load_fundraising_shares()
    out = races.copy()
    if "fundraising_share" not in out.columns:
        out["fundraising_share"] = 0.5
    if as_of is not None and "available_at" in shares.columns:
        as_of_d = date.fromisoformat(str(as_of)[:10]) if not isinstance(as_of, date) else as_of
        shares = shares[
            pd.to_datetime(shares["available_at"]).dt.date <= as_of_d
        ]
        if "feature_as_of" in shares.columns:
            feature_dates = pd.to_datetime(shares["feature_as_of"], errors="coerce").dt.date
            shares = shares[feature_dates.notna() & (feature_dates <= as_of_d)]
    if "feature_as_of" in shares.columns and len(shares):
        shares = shares.sort_values(
            ["election_id", "feature_as_of", "state"], kind="stable",
        ).drop_duplicates(["election_id", "state"], keep="last")
    by_state = shares.set_index("state")["fundraising_share"].to_dict() if len(shares) else {}
    by_race = shares.set_index("race_id")["fundraising_share"].to_dict() if len(shares) else {}
    vals = []
    for _, r in out.iterrows():
        if r["race_id"] in by_race:
            vals.append(float(by_race[r["race_id"]]))
        elif r["state"] in by_state:
            vals.append(float(by_state[r["state"]]))
        else:
            cur = r.get("fundraising_share")
            vals.append(float(cur) if pd.notna(cur) else 0.5)
    out["fundraising_share"] = vals
    return out
