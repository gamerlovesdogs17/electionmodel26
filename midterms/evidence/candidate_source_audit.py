"""Official candidate-list source inventory and point-in-time gap audit.

This module deliberately does not manufacture a candidate timeline.  It seals
official FEC state-ballot workbooks when they exist, records the official
server's publication timestamp, and reports which replay cutoffs the source
cannot support.  A workbook published after a cutoff remains useful archival
evidence, but it cannot establish what was knowable at that cutoff.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

SOURCE_AUDIT_SCHEMA_VERSION = "candidate-source-gap-audit-v1"
SOURCE_PARSER_VERSION = "fec-congressional-ballot-workbook-v1"
FEC_BALLOT_WORKBOOKS = {
    2022: "https://www.fec.gov/resources/cms-content/documents/2022congressgecands.xlsx",
    2024: "https://www.fec.gov/resources/cms-content/documents/2024congressgecands.xlsx",
}
REQUIRED_CUTOFFS = {
    2018: ("2018-09-07", "2018-10-07"),
    2020: ("2020-09-04", "2020-10-04"),
    2022: ("2022-09-09", "2022-10-09"),
    2024: ("2024-09-06", "2024-10-06"),
    2026: ("2026-09-27",),
}


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _fetch(url: str) -> tuple[bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": "electionmodel26-source-audit/1.0"})
    with urlopen(request, timeout=120) as response:
        return response.read(), {str(k): str(v) for k, v in response.headers.items()}


def _official_available_at(headers: dict[str, str]) -> str | None:
    raw = next(
        (value for key, value in headers.items() if key.lower() == "last-modified"),
        None,
    )
    if not raw:
        return None
    parsed = parsedate_to_datetime(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def parse_fec_congressional_ballot_workbook(
    blob: bytes,
    *,
    cycle: int,
    source_url: str,
    source_sha256: str,
    available_at: str,
    retrieved_at: str,
) -> pd.DataFrame:
    """Parse Senate rows without treating the workbook as pre-cutoff evidence."""
    workbook = pd.ExcelFile(io.BytesIO(blob))
    rows: list[dict[str, Any]] = []
    for sheet in workbook.sheet_names:
        frame = pd.read_excel(workbook, sheet_name=sheet, dtype=object)
        columns = {str(column).strip().upper(): column for column in frame.columns}
        state_col = columns.get("STATE ABBREVIATION")
        district_col = columns.get("DISTRICT")
        candidate_id_col = columns.get("FEC CANDIDATE ID#") or columns.get("FEC ID#")
        name_col = columns.get("CANDIDATE NAME")
        party_col = columns.get("PARTY")
        if not all((state_col, district_col, name_col, party_col)):
            continue
        for _, record in frame.iterrows():
            district = str(record.get(district_col) or "").strip()
            if not district.upper().startswith("S"):
                continue
            state = str(record.get(state_col) or "").strip().upper()
            candidate_name = str(record.get(name_col) or "").strip()
            ballot_party = str(record.get(party_col) or "").strip().upper()
            if len(state) != 2 or not candidate_name or not ballot_party:
                continue
            special = any(
                token in f"{sheet} {district}".lower()
                for token in ("special", "spec", "unexpired")
            )
            suffix = "-unexpired" if "unexpired" in district.lower() else (
                "-special" if special else ""
            )
            candidate_id = (
                str(record.get(candidate_id_col) or "").strip()
                if candidate_id_col else ""
            )
            rows.append({
                "cycle": int(cycle),
                "election_id": f"senate-{int(cycle)}",
                "race_id_hint": f"senate-{int(cycle)}-{state}{suffix}",
                "state": state,
                "contest_label": district,
                "candidate_id": candidate_id or None,
                "candidate_name": candidate_name,
                "ballot_party": ballot_party,
                "source_url": source_url,
                "source_object_sha256": source_sha256,
                "available_at": available_at,
                "retrieved_at": retrieved_at,
                "parser_version": SOURCE_PARSER_VERSION,
            })
    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError(f"official FEC workbook for {cycle} contained no Senate rows")
    return result.sort_values(
        ["race_id_hint", "ballot_party", "candidate_name"], kind="stable",
    ).reset_index(drop=True)


def prepare_official_candidate_source_audit(
    *,
    current_as_of: str = "2026-09-27",
    sources: dict[int, str] | None = None,
    fetcher: Callable[[str], tuple[bytes, dict[str, str]]] = _fetch,
    races_path: Path = NORMALIZED_DIR / "races_official.parquet",
    raw_dir: Path = RAW_DIR / "external",
    normalized_path: Path = NORMALIZED_DIR / "candidate_ballot_source_inventory.parquet",
    manifest_path: Path = MANIFESTS_DIR / "candidate_timeline_source_audit.json",
    gaps_path: Path = ARTIFACTS_DIR / "candidate_timeline_source_gaps_latest.json",
) -> dict[str, Any]:
    """Seal known official lists and report point-in-time coverage gaps."""
    source_map = dict(FEC_BALLOT_WORKBOOKS if sources is None else sources)
    if not races_path.is_file():
        raise FileNotFoundError("official race universe is required for candidate source audit")
    races = pd.read_parquet(races_path)
    retrieved_at = datetime.now(UTC).isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    gaps_path.parent.mkdir(parents=True, exist_ok=True)

    inventories: list[pd.DataFrame] = []
    receipts: dict[str, dict[str, Any]] = {}
    for cycle, url in sorted(source_map.items()):
        blob, headers = fetcher(url)
        source_hash = _sha256(blob)
        available_at = _official_available_at(headers)
        filename = f"fec_congressional_ballot_candidates_{int(cycle)}.xlsx"
        raw_path = raw_dir / filename
        raw_path.write_bytes(blob)
        receipt = {
            "cycle": int(cycle),
            "source_url": url,
            "raw_path": filename,
            "raw_sha256": source_hash,
            "retrieved_at": retrieved_at,
            "official_last_modified": available_at,
            "availability_basis": "official_http_last_modified",
            "parser_version": SOURCE_PARSER_VERSION,
        }
        receipts[str(cycle)] = receipt
        if available_at is None:
            receipt["status"] = "untraceable_availability"
            continue
        inventory = parse_fec_congressional_ballot_workbook(
            blob,
            cycle=int(cycle),
            source_url=url,
            source_sha256=source_hash,
            available_at=available_at,
            retrieved_at=retrieved_at,
        )
        receipt["status"] = "sealed_late_or_available"
        receipt["n_senate_candidates"] = len(inventory)
        inventories.append(inventory)

    inventory = pd.concat(inventories, ignore_index=True) if inventories else pd.DataFrame()
    inventory.to_parquet(normalized_path, index=False)
    inventory_sha = hashlib.sha256(normalized_path.read_bytes()).hexdigest()

    declared_cutoffs = dict(REQUIRED_CUTOFFS)
    declared_cutoffs[2026] = (pd.Timestamp(current_as_of).date().isoformat(),)
    coverage: dict[str, Any] = {}
    for cycle, cutoffs in sorted(declared_cutoffs.items()):
        election_id = f"senate-{cycle}"
        selected = races[races["election_id"].astype(str).eq(election_id)].copy()
        required = selected[~selected.get(
            "not_up", pd.Series(False, index=selected.index),
        ).fillna(False).astype(bool)]
        required_race_ids = sorted(required["race_id"].astype(str).unique())
        receipt = receipts.get(str(cycle))
        source_available_at = (receipt or {}).get("official_last_modified")
        for cutoff in cutoffs:
            cutoff_date = pd.Timestamp(cutoff).date()
            available = (
                pd.Timestamp(source_available_at).date() <= cutoff_date
                if source_available_at else False
            )
            label = (
                f"{election_id}-current" if cycle == 2026 else
                f"{election_id}-lead-{'60' if cutoff == cutoffs[0] else '30'}"
            )
            # Even an on-time cycle workbook is only an inventory until its
            # modeled/opposing identity mapping is validated race by race.
            reason = (
                "official cycle-wide ballot source was published after this cutoff"
                if source_available_at and not available else
                "no traceable official cycle-wide ballot source was established"
                if not source_available_at else
                "official source is available but modeled/opposing mapping is not validated"
            )
            coverage[label] = {
                "as_of": cutoff,
                "source_available_at": source_available_at,
                "source_available_by_cutoff": available,
                "required_race_ids": required_race_ids,
                "covered_race_ids": [],
                "missing_race_ids": required_race_ids,
                "status": "incomplete_coverage",
                "reason": reason,
                "publication_eligible": False,
            }

    report = {
        "schema_version": SOURCE_AUDIT_SCHEMA_VERSION,
        "parser_version": SOURCE_PARSER_VERSION,
        "generated_at": retrieved_at,
        "current_as_of": pd.Timestamp(current_as_of).date().isoformat(),
        "source_provider": "Federal Election Commission",
        "source_description": "Congressional state ballot lists compiled from state election offices",
        "receipts": receipts,
        "inventory": {
            "path": normalized_path.name,
            "sha256": inventory_sha,
            "n_rows": len(inventory),
        },
        "coverage": coverage,
        "production_eligible": False,
        "status": "incomplete_coverage",
        "note": (
            "Late official ballot lists are archived for provenance only. They are not "
            "backdated and do not create candidate-timeline events."
        ),
    }
    manifest_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    gaps_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
