"""Official FEC campaign-finance ingest → fundraising share by Senate race."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.tickets import TICKETS_2026

PARSER_VERSION = "fec-v4-candidate-aware-bulk-current"
REPORT_FINANCE_SCHEMA_VERSION = "fec-report-finance-v1"
OPENFEC = "https://api.open.fec.gov/v1"
# FEC all-candidates summary (no API key; same underlying filings as OpenFEC).
WEBALL_URL = "https://www.fec.gov/files/bulk-downloads/{cycle}/weball{yy}.zip"
CANDIDATE_COMMITTEE_URL = (
    "https://www.fec.gov/files/bulk-downloads/{cycle}/ccl{yy}.zip"
)
FORM3_ENDPOINT = "/reports/house-senate/"
HOUSE_SENATE_REPORTS_BULK_URL = (
    "https://www.fec.gov/files/bulk-downloads/{cycle}/"
    "{cycle}_HOUSE_SENATE_CAMPAIGNS_DOWNLOAD.csv"
)
FORM3_BULK_SCHEMA_VERSION = "fec-house-senate-report-summary-bulk-v1"
CURRENT_BULK_MAX_BACKFILL_DAYS = 1
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


def _strip_accents(text: str) -> str:
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
        "ü": "u", "ä": "a", "ö": "o",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def _normalize_person_name(name: str | None) -> str:
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    text = _strip_accents(
        str(name)
        .replace(".", " ")
        .replace(",", " ")
        .replace("'", "")
        .replace("’", "")
        .replace("`", "")
        .replace("(", " ")
        .replace(")", " ")
        .lower()
    )
    # FEC often uses LAST FIRST; registry uses First Last.
    parts = [
        p
        for p in text.replace("-", " ").split()
        if p and p not in {"jr", "sr", "ii", "iii", "iv"} and len(p) > 1
    ]
    return " ".join(sorted(parts))


_NICKNAMES = {
    "chris": "christopher",
    "christopher": "christopher",
    "chuck": "charles",
    "charles": "charles",
    "dan": "daniel",
    "daniel": "daniel",
    "ed": "edward",
    "edward": "edward",
    "jim": "james",
    "james": "james",
    "bill": "william",
    "william": "william",
    "bob": "robert",
    "robert": "robert",
    "tom": "thomas",
    "thomas": "thomas",
    "mike": "michael",
    "michael": "michael",
    "jon": "jonathan",
    "jonathan": "jonathan",
    "john": "john",
    "ben": "benjamin",
    "benjamin": "benjamin",
    "matt": "matthew",
    "matthew": "matthew",
    "rick": "richard",
    "richard": "richard",
    "deb": "deborah",
    "deborah": "deborah",
    "cindy": "cynthia",
    "cynthia": "cynthia",
    "joe": "joseph",
    "joseph": "joseph",
    "dave": "david",
    "david": "david",
    "ted": "edward",
    "beto": "beto",
    "jd": "jd",
}


def _canonical_given(token: str) -> str:
    return _NICKNAMES.get(token, token)


def _name_tokens(name: str | None) -> list[str]:
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return []
    text = _strip_accents(
        str(name)
        .replace(".", " ")
        .replace(",", " ")
        .replace("'", "")
        .replace("’", "")
        .replace("`", "")
        .replace("(", " ")
        .replace(")", " ")
        .lower()
    )
    raw = [p for p in text.replace("-", " ").split() if p and p not in {"jr", "sr", "ii", "iii", "iv"}]
    # Keep single-letter initials; also join adjacent initials (J D -> jd).
    tokens: list[str] = []
    i = 0
    while i < len(raw):
        if len(raw[i]) == 1:
            initials = [raw[i]]
            j = i + 1
            while j < len(raw) and len(raw[j]) == 1:
                initials.append(raw[j])
                j += 1
            tokens.append("".join(initials))
            i = j
        else:
            tokens.append(raw[i])
            i += 1
    return tokens


def fec_name_matches(fec_name: str | None, ticket_name: str | None) -> bool:
    """True when FEC and ticket names refer to the same person (order-insensitive)."""
    a = _normalize_person_name(fec_name)
    b = _normalize_person_name(ticket_name)
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if a_tokens and b_tokens and (a_tokens <= b_tokens or b_tokens <= a_tokens):
        return True
    # Last-name + given-name (nickname / Christopher↔Chris) match.
    fec_tokens = _name_tokens(fec_name)
    ticket_tokens = _name_tokens(ticket_name)
    if len(fec_tokens) < 2 or len(ticket_tokens) < 2:
        return False
    # FEC bulk names are usually LAST, FIRST ...
    fec_raw = str(fec_name or "")
    if "," in fec_raw:
        fec_last = _name_tokens(fec_raw.split(",", 1)[0])
        fec_given = _name_tokens(fec_raw.split(",", 1)[1])
    else:
        fec_last = fec_tokens[:1]
        fec_given = fec_tokens[1:]
    ticket_last = ticket_tokens[-1:]
    ticket_given = ticket_tokens[:-1]
    if not fec_last or not ticket_last or fec_last[0] != ticket_last[0]:
        return False
    if not fec_given or not ticket_given:
        return False
    fec_given_canon = {_canonical_given(t) for t in fec_given}
    ticket_given_canon = {_canonical_given(t) for t in ticket_given}
    # Any overlapping given/nickname token after canonicalization is enough when
    # the last name already matched uniquely (handles BETO / TED parentheticals).
    return bool(fec_given_canon & ticket_given_canon)


def _party_code(series: pd.Series) -> pd.Series:
    return series.astype(str).map(_normalize_party)


def select_unique_party_candidate(
    candidates: pd.DataFrame,
    *,
    party: str,
    ticket_name: str | None,
) -> dict[str, Any] | None:
    """Return the unique FEC candidate matching the ticket name for one party.

    Fail closed (None) when the name is missing, unmatched, or ambiguous.
    Never selects 'highest receipts' as a surrogate for identity.
    """
    if not ticket_name or candidates is None or candidates.empty:
        return None
    work = candidates.copy()
    if "candidate_name" not in work.columns and "name" in work.columns:
        work = work.rename(columns={"name": "candidate_name"})
    party_rows = work[_party_code(work["party"]).eq(party)].copy()
    if party_rows.empty or "candidate_name" not in party_rows.columns:
        return None
    matched = party_rows[
        party_rows["candidate_name"].map(lambda n: fec_name_matches(n, ticket_name))
    ]
    if matched.empty:
        return None
    ids = sorted(matched["candidate_id"].astype(str).unique())
    if len(ids) != 1:
        return None
    cid = ids[0]
    row = matched[matched["candidate_id"].astype(str).eq(cid)].iloc[0]
    return {
        "candidate_id": cid,
        "candidate_name": str(row.get("candidate_name") or ticket_name),
        "party": party,
    }


def select_unique_named_candidate(
    candidates: pd.DataFrame,
    *,
    ticket_name: str | None,
) -> dict[str, Any] | None:
    """Resolve one FEC candidate by identity without guessing from party.

    Candidate identity is authoritative for nonstandard tickets.  Party is
    retained as source metadata, but an Independent modeled candidate must not
    be replaced by a Democratic filer merely because the downstream feature
    still uses legacy ``dem_*`` column names for the modeled side.
    """
    if not ticket_name or candidates is None or candidates.empty:
        return None
    work = candidates.copy()
    if "candidate_name" not in work.columns and "name" in work.columns:
        work = work.rename(columns={"name": "candidate_name"})
    if "candidate_name" not in work.columns:
        return None
    matched = work[
        work["candidate_name"].map(lambda value: fec_name_matches(value, ticket_name))
    ]
    ids = sorted(matched["candidate_id"].astype(str).unique())
    if len(ids) != 1:
        return None
    row = matched[matched["candidate_id"].astype(str).eq(ids[0])].iloc[0]
    return {
        "candidate_id": ids[0],
        "candidate_name": str(row.get("candidate_name") or ticket_name),
        "party": _normalize_party(str(row.get("party") or "")),
    }


def historical_nominee_tickets(election_id: str) -> dict[str, dict[str, str]]:
    """Map ``race_id`` → dem/rep nominee names from the certified ledger.

    These are election-day certified nominee identities. They must never be used
    for current-cycle 2026 forecasting (use the reviewed registry instead).
    When a major-party nominee is missing, that race is omitted so finance fails
    closed. Dual contests in the same state receive distinct ``race_id`` keys.
    """
    from midterms.evidence.official_ledger import load_ledger

    year = str(election_id).replace("senate-", "")
    ledger = load_ledger()
    cycle = (ledger.get("cycles") or {}).get(year) or {}
    contests = cycle.get("contests") or []
    out: dict[str, dict[str, str]] = {}
    for contest in contests:
        race_id = str(contest.get("race_id") or "").strip()
        state = str(contest.get("state") or "").upper()
        dem = contest.get("dem_nominee")
        rep = contest.get("rep_nominee")
        if not race_id or not state or not dem or not rep:
            continue
        out[race_id] = {
            "race_id": race_id,
            "state": state,
            "seat_class": str(contest.get("seat_class") or ""),
            "kind": str(contest.get("kind") or contest.get("term_type") or "regular"),
            "dem_name": str(dem),
            "rep_name": str(rep),
            "modeled_candidate_name": str(dem),
            "opposing_candidate_name": str(rep),
            "modeled_ballot_party": "D",
            "opposing_ballot_party": "R",
        }
    return out


def current_registry_tickets() -> dict[str, dict[str, str]]:
    """Current-cycle modeled ticket names keyed by ``race_id``."""
    from midterms.evidence.current_candidates import load_current_candidate_registry

    registry = load_current_candidate_registry()
    out: dict[str, dict[str, str]] = {}
    for row in registry["races"]:
        race_id = str(row["race_id"])
        out[race_id] = {
            "race_id": race_id,
            "state": str(row["state"]).upper(),
            "dem_name": str(row["modeled_candidate_name"]),
            "rep_name": str(row["opposing_candidate_name"]),
            "modeled_candidate_id": str(row["modeled_candidate_id"]),
            "opposing_candidate_id": str(row["opposing_candidate_id"]),
            "modeled_candidate_name": str(row["modeled_candidate_name"]),
            "opposing_candidate_name": str(row["opposing_candidate_name"]),
            "modeled_ballot_party": str(row.get("modeled_ballot_party") or ""),
            "opposing_ballot_party": str(row.get("opposing_ballot_party") or ""),
        }
    return out


def _ticket_lookup(
    ticket_map: dict[str, dict[str, Any]] | None,
    *,
    race_id: str,
    state: str,
) -> dict[str, Any]:
    """Resolve a ticket by race_id first; state key is legacy-only fallback."""
    if not ticket_map:
        return {}
    if race_id in ticket_map:
        return ticket_map[race_id]
    # Legacy callers / fixtures may still pass state-keyed maps.
    return ticket_map.get(str(state).upper()) or {}


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
    tickets: dict[str, dict[str, Any]] | None = None,
    require_candidate_match: bool = True,
) -> pd.DataFrame:
    """Derive race finance features from receipt-safe official report summaries.

    When ``require_candidate_match`` is True (production default), fundraising
    uses only the modeled candidate and opposing candidate's authorized
    committees for that race. Party-wide sums and max-receipt surrogates are
    refused. Legacy ``dem_*`` columns mean the modeled side for nonstandard
    current tickets. Unmatched identities yield a neutral 0.5 share with
    explicit ``match_status`` provenance rather than silently picking another
    filer.
    """
    selected = select_candidate_committee_reports_as_of(
        reports, candidate_committees, as_of=as_of,
    )
    rows: list[dict[str, Any]] = []
    if selected.empty:
        return pd.DataFrame(rows)
    # Candidate identity comes from committee linkage (has names), not reports.
    name_lookup = (
        candidate_committees[["candidate_id", "candidate_name", "state", "party"]]
        .drop_duplicates(["candidate_id"])
        if "candidate_name" in candidate_committees.columns
        else pd.DataFrame(columns=["candidate_id", "candidate_name", "state", "party"])
    )
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
    if not name_lookup.empty:
        by_candidate = by_candidate.merge(
            name_lookup, on=["candidate_id", "state", "party"], how="left",
        )
    else:
        by_candidate["candidate_name"] = None

    ticket_map = tickets
    if ticket_map is None and require_candidate_match:
        if str(election_id) == "senate-2026":
            ticket_map = current_registry_tickets()
        else:
            ticket_map = historical_nominee_tickets(str(election_id))

    # Race-id primary identity. Iterate tickets (one row per contested race),
    # never collapse dual Senate contests by state.
    race_targets: list[tuple[str, str, dict[str, Any]]] = []
    if ticket_map:
        for key, ticket in sorted(ticket_map.items(), key=lambda item: str(item[0])):
            race_id = str(ticket.get("race_id") or key)
            state_key = str(ticket.get("state") or "").upper()
            # Legacy state-keyed maps: synthesize ordinary race_id.
            if not state_key and len(str(key)) == 2:
                state_key = str(key).upper()
                race_id = f"{election_id}-{state_key}"
            if not state_key and "-" in race_id:
                parts = race_id.split("-")
                if len(parts) >= 3:
                    state_key = parts[2].upper()
            if not state_key:
                continue
            race_targets.append((race_id, state_key, dict(ticket)))
    else:
        # Diagnostic / no-ticket path: one synthetic race per FEC state.
        for state in sorted(by_candidate["state"].astype(str).unique()):
            state_key = str(state).upper()
            race_targets.append(
                (f"{election_id}-{state_key}", state_key, {})
            )

    for race_id, state_key, ticket in race_targets:
        group = by_candidate[by_candidate["state"].astype(str).str.upper().eq(state_key)]
        dem_ticket = ticket.get("dem_name") or ticket.get("modeled_candidate_name")
        rep_ticket = ticket.get("rep_name") or ticket.get("opposing_candidate_name")
        dem_sel = select_unique_named_candidate(group, ticket_name=dem_ticket)
        rep_sel = select_unique_named_candidate(group, ticket_name=rep_ticket)
        match_status = "candidate_matched"
        dem = group.iloc[0:0]
        rep = group.iloc[0:0]
        if require_candidate_match:
            if dem_sel is None or rep_sel is None:
                match_status = "candidate_unmatched_neutral"
                dem_receipts = rep_receipts = 0.0
                dem_cash = rep_cash = 0.0
                dem_disb = rep_disb = 0.0
                dem_id = dem_sel["candidate_id"] if dem_sel else None
                rep_id = rep_sel["candidate_id"] if rep_sel else None
                dem_committees: list[str] = []
                rep_committees: list[str] = []
                filing_ids: list[str] = []
                committee_ids: list[str] = []
                available = max(group["available_at"]) if len(group) else None
            else:
                dem = group[group["candidate_id"].astype(str).eq(dem_sel["candidate_id"])]
                rep = group[group["candidate_id"].astype(str).eq(rep_sel["candidate_id"])]
                dem_receipts = float(dem["receipts"].sum())
                rep_receipts = float(rep["receipts"].sum())
                dem_cash = float(dem["cash_on_hand_end_period"].sum())
                rep_cash = float(rep["cash_on_hand_end_period"].sum())
                dem_disb = float(dem["disbursements"].sum())
                rep_disb = float(rep["disbursements"].sum())
                dem_id = dem_sel["candidate_id"]
                rep_id = rep_sel["candidate_id"]
                dem_committees = sorted(sum(dem["committee_ids"].tolist(), []))
                rep_committees = sorted(sum(rep["committee_ids"].tolist(), []))
                filing_ids = sorted(
                    sum(dem["filing_ids"].tolist(), []) + sum(rep["filing_ids"].tolist(), [])
                )
                committee_ids = sorted(set(dem_committees + rep_committees))
                available = max(
                    list(dem["available_at"]) + list(rep["available_at"])
                )
        else:
            # Legacy diagnostic path only — not production.
            dem = group[_party_code(group["party"]).eq("DEM")]
            rep = group[_party_code(group["party"]).eq("REP")]
            dem_receipts = float(dem["receipts"].sum()) if len(dem) else 0.0
            rep_receipts = float(rep["receipts"].sum()) if len(rep) else 0.0
            dem_cash = float(dem["cash_on_hand_end_period"].sum()) if len(dem) else 0.0
            rep_cash = float(rep["cash_on_hand_end_period"].sum()) if len(rep) else 0.0
            dem_disb = float(dem["disbursements"].sum()) if len(dem) else 0.0
            rep_disb = float(rep["disbursements"].sum()) if len(rep) else 0.0
            dem_id = None
            rep_id = None
            dem_committees = sorted(sum(dem["committee_ids"].tolist(), [])) if len(dem) else []
            rep_committees = sorted(sum(rep["committee_ids"].tolist(), [])) if len(rep) else []
            filing_ids = sorted(sum(group["filing_ids"].tolist(), [])) if len(group) else []
            committee_ids = sorted(sum(group["committee_ids"].tolist(), [])) if len(group) else []
            available = max(group["available_at"]) if len(group) else None
            match_status = "legacy_party_aggregate"

        receipts_total = dem_receipts + rep_receipts
        cash_total = dem_cash + rep_cash
        if match_status == "candidate_unmatched_neutral":
            share = 0.5
            cash_share = 0.5
        else:
            share = dem_receipts / receipts_total if receipts_total else 0.5
            cash_share = dem_cash / cash_total if cash_total else 0.5
        rows.append({
            "election_id": election_id,
            "state": state_key,
            "race_id": race_id,
            "fundraising_share": share,
            "cash_share": cash_share,
            "dem_receipts": dem_receipts,
            "rep_receipts": rep_receipts,
            "dem_disbursements": dem_disb,
            "rep_disbursements": rep_disb,
            "dem_cash_on_hand": dem_cash,
            "rep_cash_on_hand": rep_cash,
            "modeled_candidate_name": dem_ticket,
            "opposing_candidate_name": rep_ticket,
            "modeled_ballot_party": ticket.get("modeled_ballot_party") or "D",
            "opposing_ballot_party": ticket.get("opposing_ballot_party") or "R",
            "dem_fec_candidate_id": dem_id,
            "rep_fec_candidate_id": rep_id,
            "dem_committee_ids": dem_committees,
            "rep_committee_ids": rep_committees,
            "match_status": match_status,
            "filing_ids": filing_ids,
            "committee_ids": committee_ids,
            "available_at": available,
            "availability_basis": "fec_receipt_date",
            "source": "fec_form3_report_summaries",
            "parser_version": PARSER_VERSION,
            "finance_identity_schema": "candidate-specific-race-v3",
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


def _finance_coverage_universe(
    races: pd.DataFrame,
    election_id: str,
) -> dict[str, Any]:
    """Declare which contests require a candidate-specific finance feature.

    Historical stack validation scores only contests with a frozen Democratic
    and Republican nominee pair.  Same-party finals, Independent contests, and
    uncontested major-party races are explicitly excluded from binary scoring;
    requiring a D/R fundraising share for them previously encouraged unsafe
    party-wide aggregation.  Current races remain required because the current
    production adapters model their declared candidate side directly.
    """
    selected = races[races["election_id"].astype(str).eq(election_id)].copy()
    selected = selected[~selected.get(
        "not_up", pd.Series(False, index=selected.index),
    ).fillna(False).astype(bool)]
    all_ids = sorted(selected["race_id"].dropna().astype(str).unique())
    if str(election_id) == "senate-2026":
        required_ids = all_ids
        policy = "current_declared_candidate_side"
    else:
        ticket_ids = set(historical_nominee_tickets(str(election_id)))
        required_ids = sorted(set(all_ids) & ticket_ids)
        policy = "historical_complete_dem_rep_ticket_only"
    excluded_ids = sorted(set(all_ids) - set(required_ids))
    required_states = sorted(
        selected[selected["race_id"].astype(str).isin(required_ids)]["state"]
        .dropna().astype(str).unique()
    )
    return {
        "policy": policy,
        "all_contested_race_ids": all_ids,
        "required_race_ids": required_ids,
        "required_states": required_states,
        "excluded_race_ids": excluded_ids,
        "excluded_reason": (
            None if not excluded_ids else
            "no_complete_dem_rep_ticket_and_excluded_from_binary_finance_scoring"
        ),
    }


def _current_ticket_relevant_links(
    links: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Limit the current Form 3 normalization to relevant committees.

    The current-cycle source is the FEC's complete, nightly House/Senate report
    summary download.  Filtering happens only after its exact bytes have been
    sealed, and therefore does not require an OpenFEC API key.

    For an unambiguous D/R ticket, retain every principal committee linked to
    the two matched candidates.  When either side cannot be matched, retain
    the full state candidate set so the existing fail-safe neutral/unmatched
    behavior and its provenance remain unchanged.  No candidate is selected
    by receipts or by a highest-value surrogate.
    """
    tickets = current_registry_tickets()
    selected_candidate_ids: set[str] = set()
    fallback_races: list[dict[str, Any]] = []
    matched_races: list[str] = []
    for race_id, ticket in sorted(tickets.items()):
        state = str(ticket.get("state") or "").upper()
        state_links = links[
            links["state"].astype(str).str.upper().eq(state)
        ].copy()
        dem = select_unique_named_candidate(
            state_links,
            ticket_name=ticket.get("modeled_candidate_name") or ticket.get("dem_name"),
        )
        rep = select_unique_named_candidate(
            state_links,
            ticket_name=ticket.get("opposing_candidate_name") or ticket.get("rep_name"),
        )
        if dem is not None and rep is not None:
            selected_candidate_ids.update((dem["candidate_id"], rep["candidate_id"]))
            matched_races.append(str(race_id))
            continue
        # Preserve the prior unmatched-race semantics.  Keeping the state's
        # complete source set also prevents an unrelated query optimization
        # from fabricating an availability timestamp for a neutral feature.
        selected_candidate_ids.update(state_links["candidate_id"].astype(str))
        fallback_races.append({
            "race_id": str(race_id),
            "state": state,
            "modeled_candidate_matched": dem is not None,
            "opposing_candidate_matched": rep is not None,
            "fallback": "all_state_senate_principal_committees",
        })
    selected = links[
        links["candidate_id"].astype(str).isin(selected_candidate_ids)
    ].copy()
    if selected.empty:
        raise ValueError("current ticket finance query selected no FEC committees")
    return selected, {
        "schema_version": "fec-current-ticket-query-scope-v1",
        "selection_basis": "reviewed_ticket_identity_with_state_fallback",
        "n_source_links": len(links),
        "n_selected_links": len(selected),
        "n_selected_candidate_ids": int(selected["candidate_id"].astype(str).nunique()),
        "n_matched_races": len(matched_races),
        "matched_race_ids": matched_races,
        "fallback_races": fallback_races,
    }


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
    prior_manifest_path = MANIFESTS_DIR / "fundraising_shares.json"
    prior_manifest = (
        json.loads(prior_manifest_path.read_text(encoding="utf-8"))
        if prior_manifest_path.is_file()
        else {}
    )
    prior_sources = {
        int(row.get("cycle")): row
        for row in (prior_manifest.get("sources") or [])
        if isinstance(row, dict) and row.get("cycle") is not None
    }

    for cycle in cycles:
        cutoffs = list(REQUIRED_FINANCE_CUTOFFS.get(int(cycle), ()))
        if int(cycle) == 2026:
            cutoffs.append(current_cutoff.isoformat())
        if not cutoffs:
            raise ValueError(f"no finance cutoff declared for cycle {cycle}")
        max_cutoff = max(pd.Timestamp(value).date() for value in cutoffs)
        yy = f"{int(cycle) % 100:02d}"
        linkage_url = CANDIDATE_COMMITTEE_URL.format(cycle=int(cycle), yy=yy)
        linkage_path = external_dir / f"fec_candidate_committee_linkage_{cycle}.zip"
        form2_path = external_dir / f"fec_form2_{cycle}.csv"
        form2_url = (
            f"https://www.fec.gov/files/bulk-downloads/{cycle}/Form2Filer_{cycle}.csv"
        )
        immutable_historical = int(cycle) != 2026
        reuse_linkage = immutable_historical and linkage_path.is_file()
        reuse_form2 = immutable_historical and form2_path.is_file()
        if reuse_linkage:
            linkage_blob = linkage_path.read_bytes()
        else:
            linkage_blob = _download_bytes(linkage_url)
            linkage_path.write_bytes(linkage_blob)
        if not reuse_form2:
            form2_path.write_bytes(_download_bytes(form2_url))
        links = candidate_committee_links_from_sources(
            cycle=int(cycle), linkage_blob=linkage_blob, form2_path=form2_path,
            max_as_of=max_cutoff,
        )
        if links.empty:
            raise ValueError(f"no receipt-timed Senate principal committees for {cycle}")
        report_links = links
        query_scope: dict[str, Any] = {
            "schema_version": "fec-form3-query-scope-v1",
            "selection_basis": "all_cycle_senate_principal_committees",
            "n_source_links": len(links),
            "n_selected_links": len(links),
        }
        if int(cycle) == 2026:
            report_links, query_scope = _current_ticket_relevant_links(links)
            reports_archive = external_dir / f"fec_house_senate_reports_{cycle}.csv"
            reports, report_meta = fetch_form3_reports_from_official_bulk(
                cycle=int(cycle),
                committee_ids=sorted(report_links["committee_id"].astype(str).unique()),
                max_as_of=max_cutoff,
                raw_path=reports_archive,
            )
        else:
            reports_archive = external_dir / f"fec_form3_reports_{cycle}.zip"
            reports, report_meta = fetch_form3_reports_for_committees(
                cycle=int(cycle),
                committee_ids=sorted(report_links["committee_id"].astype(str).unique()),
                max_as_of=max_cutoff,
                archive_path=reports_archive,
                batch_size=25,
                # Historical receipts are immutable and reused only after their
                # query descriptor verifies. No API request occurs when the
                # checked-in archive matches its sealed descriptor.
                reuse_existing=True,
            )
        if reports.empty:
            raise ValueError(f"no official Form 3 reports returned for {cycle}")
        all_links.append(report_links)
        all_reports.append(reports)
        previous = prior_sources.get(int(cycle)) or {}
        previous_linkage = previous.get("linkage") or {}
        previous_form2 = previous.get("form2") or {}
        previous_form3 = previous.get("form3") or {}
        sources.append({
            "cycle": int(cycle),
            "linkage": {
                "path": linkage_path.name,
                "url": linkage_url,
                "retrieved_at": (
                    previous_linkage.get("retrieved_at")
                    if reuse_linkage else retrieved_at
                ),
                "sha256": _sha256_file(linkage_path),
            },
            "form2": {
                "path": form2_path.name,
                "url": form2_url,
                "retrieved_at": (
                    previous_form2.get("retrieved_at")
                    if reuse_form2 else retrieved_at
                ),
                "sha256": _sha256_file(form2_path),
                "role": "receipt clock for principal-committee designation",
            },
            "form3": {
                **report_meta,
                "query_scope": query_scope,
                "path": reports_archive.name,
                "url": report_meta.get("source_url") or f"{OPENFEC}{FORM3_ENDPOINT}",
                "retrieved_at": (
                    previous_form3.get("retrieved_at")
                    if immutable_historical and report_meta.get("reused_sealed_archive")
                    else retrieved_at
                ),
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
        coverage_universe = _finance_coverage_universe(races, election_id)
        required_states = coverage_universe["required_states"]
        required_race_ids = coverage_universe["required_race_ids"]
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
            if len(share) and "race_id" in share.columns and required_race_ids:
                share = share[share["race_id"].astype(str).isin(required_race_ids)].copy()
            else:
                share = share[share["state"].astype(str).isin(required_states)].copy()
            share["feature_as_of"] = cutoff_value
            share["cutoff_label"] = label
            covered_states = sorted(share["state"].astype(str).unique()) if len(share) else []
            covered_race_ids = (
                sorted(share["race_id"].astype(str).unique()) if len(share) else []
            )
            missing_states = sorted(set(required_states) - set(covered_states))
            missing_race_ids = sorted(set(required_race_ids) - set(covered_race_ids))
            coverage[label] = {
                "as_of": cutoff_value,
                "coverage_policy": coverage_universe["policy"],
                "all_contested_race_ids": coverage_universe["all_contested_race_ids"],
                "excluded_race_ids": coverage_universe["excluded_race_ids"],
                "excluded_reason": coverage_universe["excluded_reason"],
                "required_states": required_states,
                "covered_states": covered_states,
                "missing_states": missing_states,
                "required_race_ids": required_race_ids,
                "covered_race_ids": covered_race_ids,
                "missing_race_ids": missing_race_ids,
                "n_rows": int(len(share)),
                "production_eligible": (
                    not missing_race_ids and bool(required_race_ids)
                ),
            }
            snapshots.append(share)
    shares = pd.concat(snapshots, ignore_index=True) if snapshots else pd.DataFrame()
    if len(shares):
        shares = shares.sort_values(
            ["election_id", "feature_as_of", "race_id"], kind="stable",
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
        "source_url": "https://www.fec.gov/data/browse-data/?tab=filings",
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


def _openfec_rate_limit_error(exc: HTTPError) -> RuntimeError:
    retry_after = str((exc.headers or {}).get("Retry-After") or "unknown")
    credential = "public DEMO_KEY" if _fec_api_key() == "DEMO_KEY" else "configured key"
    return RuntimeError(
        "OpenFEC rate limit exhausted for "
        f"{credential} (Retry-After={retry_after}s); current-cycle finance "
        "refreshes must use the official House/Senate bulk source, while an "
        "unavailable historical sealed archive must fail closed"
    )


def _openfec_retry_after_seconds(exc: HTTPError) -> int:
    try:
        return max(0, int(str((exc.headers or {}).get("Retry-After") or "0")))
    except ValueError:
        return 0


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
            if int(getattr(exc, "code", 0) or 0) == 429:
                retry_after = _openfec_retry_after_seconds(exc)
                if _fec_api_key() == "DEMO_KEY" or retry_after > 60:
                    raise _openfec_rate_limit_error(exc) from exc
                if attempt + 1 < retries:
                    time.sleep(max(float(retry_after), 1.5 * (2**attempt)))
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
            if int(getattr(exc, "code", 0) or 0) == 429:
                retry_after = _openfec_retry_after_seconds(exc)
                if _fec_api_key() == "DEMO_KEY" or retry_after > 60:
                    raise _openfec_rate_limit_error(exc) from exc
                if attempt + 1 < retries:
                    time.sleep(max(float(retry_after), 2.0 * (2**attempt)))
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


def _parse_bulk_receipt_date(value: Any) -> date | None:
    """Parse the documented day-month-year receipt clock in the FEC CSV."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%b-%y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _numeric_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_form3_reports_official_bulk(
    blob: bytes,
    *,
    cycle: int,
    committee_ids: list[str],
    max_as_of: str | date,
    source_url: str,
) -> pd.DataFrame:
    """Normalize the FEC's nightly House/Senate report-summary CSV.

    The FEC bulk file is a processed current-version report extract.  It
    exposes the report receipt date, coverage dates, and cumulative Form 3
    totals without requiring an API credential.  Receipt date remains the
    availability clock.  Rows received after ``max_as_of`` are rejected.

    The bulk extract has no OpenFEC ``file_number`` column.  ``filing_id`` is
    therefore a deterministic surrogate derived from every source field in the
    row.  ``source_record_sha256`` and ``filing_id_basis`` make that distinction
    explicit; the surrogate is only a stable identity/tiebreak and is never
    represented as an FEC filing number.
    """
    required = {
        "CMTE_ID", "CMTE_NM", "FORM_TP_CD", "RPT_TP", "RECEIPT_DT",
        "CVG_START_DT", "CVG_END_DT", "TTL_RECEIPTS", "TTL_DISB", "COH_COP",
    }
    source = pd.read_csv(io.BytesIO(blob), dtype=str, keep_default_na=False)
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(f"FEC House/Senate bulk file missing columns: {missing}")
    selected_ids = set(map(str, committee_ids))
    cutoff = pd.Timestamp(max_as_of).date()
    source = source[
        source["CMTE_ID"].astype(str).isin(selected_ids)
        & source["FORM_TP_CD"].astype(str).eq("F3")
    ].copy()
    rows: list[dict[str, Any]] = []
    for source_row in source.to_dict(orient="records"):
        received = _parse_bulk_receipt_date(source_row.get("RECEIPT_DT"))
        if received is None or received > cutoff:
            continue
        canonical = json.dumps(
            {str(key): str(value) for key, value in sorted(source_row.items())},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        record_sha256 = _sha256_bytes(canonical)
        coverage_start = _coverage_iso(source_row.get("CVG_START_DT", ""))
        coverage_end = _coverage_iso(source_row.get("CVG_END_DT", ""))

        committee_id = str(source_row.get("CMTE_ID") or "")
        report_type = str(source_row.get("RPT_TP") or "")
        rows.append({
            "cycle": int(cycle),
            "committee_id": committee_id,
            "committee_name": str(source_row.get("CMTE_NM") or ""),
            "report_type": report_type,
            "coverage_start_date": coverage_start,
            "coverage_end_date": coverage_end,
            "receipt_date": received.isoformat(),
            # Fits signed int64 while remaining deterministic across platforms.
            "filing_id": int(record_sha256[:15], 16),
            "filing_id_basis": "synthetic_bulk_row_sha256",
            "amendment_indicator": "current_processed_version",
            "amendment_chain_id": "|".join((
                committee_id, report_type, str(coverage_start), str(coverage_end),
            )),
            "receipts": _numeric_or_zero(source_row.get("TTL_RECEIPTS")),
            "disbursements": _numeric_or_zero(source_row.get("TTL_DISB")),
            "cash_on_hand_end_period": _numeric_or_zero(source_row.get("COH_COP")),
            "source_record_sha256": record_sha256,
            "source_url": source_url,
            "source_mode": FORM3_BULK_SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(
        ["committee_id", "receipt_date", "filing_id"], kind="stable",
    ).drop_duplicates(
        ["committee_id", "amendment_chain_id", "receipt_date"], keep="last",
    ).reset_index(drop=True)


def fetch_form3_reports_from_official_bulk(
    *,
    cycle: int,
    committee_ids: list[str],
    max_as_of: str | date,
    raw_path: Path,
    retrieved_at: str | datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Download, atomically seal, and parse the official no-key report CSV."""
    cutoff = pd.Timestamp(max_as_of).date()
    retrieval_time = pd.Timestamp(retrieved_at or datetime.now(UTC))
    if retrieval_time.tzinfo is None:
        retrieval_time = retrieval_time.tz_localize("UTC")
    else:
        retrieval_time = retrieval_time.tz_convert("UTC")
    retrieval_date = retrieval_time.date()
    if cutoff > retrieval_date:
        raise ValueError("FEC current bulk cutoff cannot be later than retrieval date")
    if cutoff < retrieval_date - timedelta(days=CURRENT_BULK_MAX_BACKFILL_DAYS):
        raise ValueError(
            "FEC current-version bulk extract cannot reconstruct a historical "
            f"cutoff older than {CURRENT_BULK_MAX_BACKFILL_DAYS} day; reuse a "
            "sealed versioned archive instead"
        )
    url = HOUSE_SENATE_REPORTS_BULK_URL.format(cycle=int(cycle))
    blob = _download_bytes(url)
    temporary = raw_path.with_name(f".{raw_path.name}.refreshing")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_bytes(blob)
        temporary.replace(raw_path)
    finally:
        if temporary.is_file():
            temporary.unlink()
    frame = parse_form3_reports_official_bulk(
        blob,
        cycle=int(cycle),
        committee_ids=committee_ids,
        max_as_of=cutoff,
        source_url=url,
    )
    return frame, {
        "schema_version": FORM3_BULK_SCHEMA_VERSION,
        "cycle": int(cycle),
        "max_as_of": cutoff.isoformat(),
        "retrieved_at": retrieval_time.isoformat(),
        "source_url": url,
        "raw_sha256": _sha256_bytes(blob),
        "n_source_bytes": len(blob),
        "n_selected_committees": len(set(map(str, committee_ids))),
        "n_reports": len(frame),
        "availability_basis": "fec_receipt_date",
        "filing_id_basis": "synthetic_bulk_row_sha256",
        "api_key_required": False,
    }


def fetch_form3_reports_for_committees(
    *, cycle: int, committee_ids: list[str], max_as_of: str | date,
    archive_path: Path, batch_size: int = 25, reuse_existing: bool = True,
    replace_existing_on_query_change: bool = False,
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
    if reuse_existing and replace_existing_on_query_change and archive_path.is_file():
        with zipfile.ZipFile(archive_path) as archive:
            stored_descriptor = (
                json.loads(archive.read("request-metadata.json").decode("utf-8"))
                if "request-metadata.json" in archive.namelist()
                else None
            )
        if stored_descriptor is not None and any(
            stored_descriptor.get(key) != value for key, value in descriptor.items()
        ):
            # The current-cycle cutoff and candidate/committee universe move
            # over time.  A caller that explicitly opts in may replace that
            # receipt, but the default remains fail-closed for historical use.
            reuse_existing = False
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
    temporary_archive = archive_path.with_name(f".{archive_path.name}.refreshing")
    try:
        with zipfile.ZipFile(
            temporary_archive, "w", compression=zipfile.ZIP_DEFLATED,
        ) as archive:
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
                        "url": url.replace(
                            f"api_key={_fec_api_key()}", "api_key=REDACTED"
                        ),
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
        temporary_archive.replace(archive_path)
    finally:
        if temporary_archive.is_file():
            temporary_archive.unlink()
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
    tickets: dict[str, dict[str, Any]] | None = None,
    require_candidate_match: bool = True,
) -> pd.DataFrame:
    """Build Dem fundraising shares with amendment / coverage discipline.

    Production default matches modeled ticket candidates only. Legacy
    highest-receipt party surrogates require require_candidate_match=False.
    """
    if totals.empty:
        return curated_fundraising_shares(election_id)
    work = totals.copy()
    if as_of and "available_at" in work.columns:
        work = work[pd.to_datetime(work["available_at"]).dt.date <= date.fromisoformat(str(as_of)[:10])]
    if work.empty:
        return curated_fundraising_shares(election_id)
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

    ticket_map = tickets
    if ticket_map is None and require_candidate_match:
        if str(election_id) == "senate-2026" or int(cycle) == 2026:
            ticket_map = current_registry_tickets()
        else:
            ticket_map = historical_nominee_tickets(str(election_id))

    rows = []
    race_targets: list[tuple[str, str, dict[str, Any]]] = []
    if ticket_map:
        for key, ticket in sorted(ticket_map.items(), key=lambda item: str(item[0])):
            race_id = str(ticket.get("race_id") or key)
            state_key = str(ticket.get("state") or "").upper()
            if not state_key and len(str(key)) == 2:
                state_key = str(key).upper()
                race_id = f"senate-{cycle}-{state_key}"
            if not state_key and "-" in race_id:
                parts = race_id.split("-")
                if len(parts) >= 3:
                    state_key = parts[2].upper()
            if state_key:
                race_targets.append((race_id, state_key, dict(ticket)))
    else:
        for state in sorted(work["state"].astype(str).unique()):
            state_key = str(state).upper()
            race_targets.append((f"senate-{cycle}-{state_key}", state_key, {}))

    for race_id, state_key, ticket in race_targets:
        g = work[work["state"].astype(str).str.upper().eq(state_key)]
        dem_ticket = ticket.get("dem_name") or ticket.get("modeled_candidate_name")
        rep_ticket = ticket.get("rep_name") or ticket.get("opposing_candidate_name")
        dem_sel = select_unique_party_candidate(g, party="DEM", ticket_name=dem_ticket)
        rep_sel = select_unique_party_candidate(g, party="REP", ticket_name=rep_ticket)

        if require_candidate_match and (dem_sel is None or rep_sel is None):
            dem_rec = rep_rec = dem_cash = rep_cash = dem_disb = rep_disb = 0.0
            share = cash_share = 0.5
            match_status = "candidate_unmatched_neutral"
            dem_id = dem_sel["candidate_id"] if dem_sel else None
            rep_id = rep_sel["candidate_id"] if rep_sel else None
        elif require_candidate_match:
            dem = g[g["candidate_id"].astype(str).eq(dem_sel["candidate_id"])]
            rep = g[g["candidate_id"].astype(str).eq(rep_sel["candidate_id"])]
            dem_rec = float(dem["receipts"].sum()) if len(dem) else 0.0
            rep_rec = float(rep["receipts"].sum()) if len(rep) else 0.0
            dem_cash = (
                float(dem["cash_on_hand_end_period"].sum())
                if len(dem) and "cash_on_hand_end_period" in dem
                else 0.0
            )
            rep_cash = (
                float(rep["cash_on_hand_end_period"].sum())
                if len(rep) and "cash_on_hand_end_period" in rep
                else 0.0
            )
            dem_disb = (
                float(dem["disbursements"].sum())
                if len(dem) and "disbursements" in dem
                else 0.0
            )
            rep_disb = (
                float(rep["disbursements"].sum())
                if len(rep) and "disbursements" in rep
                else 0.0
            )
            total = dem_rec + rep_rec
            share = dem_rec / total if total > 0 else 0.5
            cash_tot = dem_cash + rep_cash
            cash_share = dem_cash / cash_tot if cash_tot > 0 else 0.5
            match_status = "candidate_matched"
            dem_id = dem_sel["candidate_id"]
            rep_id = rep_sel["candidate_id"]
        else:
            dem = g[g["party"].astype(str).str.upper().str.startswith("DEM")]
            rep = g[g["party"].astype(str).str.upper().str.startswith("REP")]
            dem_rec = float(dem["receipts"].max()) if len(dem) else 0.0
            rep_rec = float(rep["receipts"].max()) if len(rep) else 0.0
            dem_cash = (
                float(dem["cash_on_hand_end_period"].max())
                if len(dem) and "cash_on_hand_end_period" in dem
                else 0.0
            )
            rep_cash = (
                float(rep["cash_on_hand_end_period"].max())
                if len(rep) and "cash_on_hand_end_period" in rep
                else 0.0
            )
            dem_disb = (
                float(dem["disbursements"].max())
                if len(dem) and "disbursements" in dem
                else 0.0
            )
            rep_disb = (
                float(rep["disbursements"].max())
                if len(rep) and "disbursements" in rep
                else 0.0
            )
            total = dem_rec + rep_rec
            share = dem_rec / total if total > 0 else 0.5
            cash_tot = dem_cash + rep_cash
            cash_share = dem_cash / cash_tot if cash_tot > 0 else 0.5
            match_status = "legacy_party_max"
            dem_id = None
            rep_id = None

        chains = []
        for cid in g.get("candidate_id", pd.Series(dtype=str)).dropna().astype(str).tolist():
            chains.extend(chain_by_cand.get(cid, []))
        cov_dates = sorted(set(chains))
        src_vals = set(g["source"].dropna().astype(str)) if "source" in g.columns else set()
        if "openfec" in src_vals:
            src = "openfec"
        elif "fec_weball" in src_vals:
            src = "fec_weball"
        else:
            src = "openfec"
        available = str(g["available_at"].max()) if len(g) else None
        rows.append(
            {
                "election_id": election_id,
                "state": state_key,
                "race_id": race_id,
                "fundraising_share": round(float(share), 6),
                "cash_share": round(float(cash_share), 6),
                "dem_receipts": dem_rec,
                "rep_receipts": rep_rec,
                "dem_cash_on_hand": dem_cash,
                "rep_cash_on_hand": rep_cash,
                "dem_disbursements": dem_disb,
                "rep_disbursements": rep_disb,
                "modeled_candidate_name": dem_ticket,
                "opposing_candidate_name": rep_ticket,
                "dem_fec_candidate_id": dem_id,
                "rep_fec_candidate_id": rep_id,
                "match_status": match_status,
                "matched_window_id": f"cycle-{cycle}-coverage-end-asof",
                "amendment_chain": ">".join(cov_dates) if cov_dates else "latest_totals_row",
                "n_filings_in_chain": int(len(cov_dates)),
                "source": src,
                "available_at": available,
                "parser_version": PARSER_VERSION,
                "finance_identity_schema": "candidate-specific-race-v2",
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
    races: pd.DataFrame,
    *,
    as_of: str | date | None = None,
    allow_state_fallback: bool = False,
) -> pd.DataFrame:
    """Attach fundraising shares by exact ``race_id``.

    Production / formal validation must keep ``allow_state_fallback=False``.
    State-only fallback is permitted solely for labeled development fixtures.
    Missing race-specific finance keeps the existing share or neutral 0.5.
    """
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
            ["election_id", "feature_as_of", "race_id"], kind="stable",
        ).drop_duplicates(["election_id", "race_id"], keep="last")
    by_race = shares.set_index("race_id")["fundraising_share"].to_dict() if len(shares) else {}
    by_state = (
        shares.set_index("state")["fundraising_share"].to_dict()
        if allow_state_fallback and len(shares)
        else {}
    )
    vals = []
    for _, r in out.iterrows():
        if r["race_id"] in by_race:
            vals.append(float(by_race[r["race_id"]]))
        elif allow_state_fallback and r["state"] in by_state:
            vals.append(float(by_state[r["state"]]))
        else:
            cur = r.get("fundraising_share")
            vals.append(float(cur) if pd.notna(cur) else 0.5)
    out["fundraising_share"] = vals
    return out
