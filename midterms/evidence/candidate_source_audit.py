"""Official candidate-list ingestion and point-in-time gap audit.

Candidate events are emitted only after a declared mapping is verified against
the exact bytes of a dated government candidate document. Official FEC ballot
workbooks published after a cutoff remain archival evidence and are never
backdated. Primary calendars classify cases where a nominee or runoff pairing
did not yet exist; they never supply candidate identity.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pandas as pd
from pypdf import PdfReader

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.candidate_timeline import (
    apply_candidate_state_contract,
    candidate_timeline_fingerprint,
    validate_candidate_timeline_source,
)

SOURCE_AUDIT_SCHEMA_VERSION = "candidate-source-gap-audit-v3"
SOURCE_PARSER_VERSION = "fec-congressional-ballot-workbook-v1"
OFFICIAL_DOCUMENT_PARSER_VERSION = "official-candidate-document-v1"
OFFICIAL_DOCUMENT_SCHEMA_VERSION = "candidate-official-sources-v1"
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

SEALED_SOURCE_RECEIPTS = (
    RAW_DIR / "external" / "candidate_timeline" / "source_receipts.json"
)


def _normalized_pdf_text(blob: bytes) -> str:
    """Extract stable searchable text without changing or replacing source bytes."""
    reader = PdfReader(io.BytesIO(blob))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def _event_is_visible_in_document(event: dict[str, Any], document_text: str) -> bool:
    name = re.sub(
        r"[^A-Z0-9]+", " ",
        unicodedata.normalize("NFKD", str(event["candidate_name"]))
        .encode("ascii", "ignore").decode("ascii").upper(),
    ).strip()
    party = str(event["ballot_party"]).upper()
    party_token = {"DEM": "DEMOCRATIC", "REP": "REPUBLICAN"}.get(party, party)
    # The official documents place the ballot party immediately after the name.
    # Requiring both in a short window prevents a hand-authored mapping from being
    # accepted merely because the two tokens occur somewhere in a long PDF.
    position = document_text.find(name)
    return position >= 0 and party_token in document_text[position:position + len(name) + 80]


def prepare_sealed_candidate_timeline_sources(
    *,
    receipt_path: Path = SEALED_SOURCE_RECEIPTS,
    races_path: Path = NORMALIZED_DIR / "races_official.parquet",
    timeline_path: Path = NORMALIZED_DIR / "candidate_timeline.parquet",
    timeline_manifest_path: Path = MANIFESTS_DIR / "candidate_timeline.json",
    source_audit_path: Path = MANIFESTS_DIR / "candidate_timeline_source_audit.json",
    gaps_path: Path = ARTIFACTS_DIR / "candidate_timeline_source_gaps_latest.json",
) -> dict[str, Any]:
    """Build only candidate events verified against sealed official documents.

    The receipt is a mapping contract, not evidence. Every declared candidate and
    party must be found in the hashed official PDF before an event is emitted.
    Primary calendars are retained only to explain why a nominee could not yet
    exist at a replay cutoff; they never create candidate identity events.
    """
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("schema_version") != OFFICIAL_DOCUMENT_SCHEMA_VERSION:
        raise ValueError("unsupported official candidate source receipt schema")
    source_root = receipt_path.parent
    events: list[dict[str, Any]] = []
    document_receipts: dict[str, Any] = {}
    calendar_receipts: dict[str, Any] = {}
    for source in receipt.get("sources") or []:
        raw_path = source_root / str(source["raw_path"])
        if not raw_path.is_file():
            raise FileNotFoundError(f"sealed candidate source is missing: {raw_path}")
        blob = raw_path.read_bytes()
        actual_hash = _sha256(blob)
        if actual_hash != str(source["raw_sha256"]).lower():
            raise ValueError(f"candidate source hash changed: {raw_path.name}")
        base_receipt = {
            key: source.get(key) for key in (
                "source_url", "source_title", "raw_path", "raw_sha256",
                "retrieved_at", "available_at", "availability_basis",
                "document_data_as_of", "source_type",
            ) if source.get(key) is not None
        }
        if source.get("source_type") == "primary_calendar":
            calendar_receipts[str(source["cycle"])] = base_receipt
            continue
        document_text = _normalized_pdf_text(blob)
        declared = source.get("candidate_events") or []
        for event in declared:
            if not _event_is_visible_in_document(event, document_text):
                raise ValueError(
                    f"official candidate document does not verify "
                    f"{event.get('candidate_name')} / {event.get('ballot_party')}: "
                    f"{raw_path.name}"
                )
            events.append({
                **event,
                "effective_at": source["available_at"],
                "available_at": source["available_at"],
                "retrieved_at": source["retrieved_at"],
                "event_type": "ballot_qualification",
                "ballot_status": "qualified",
                "election_phase": "general",
                "source_url": source["source_url"],
                "source_tier": "official_state_certified_candidate_list",
                "source_object_sha256": actual_hash,
                "source_hash": actual_hash,
                "parser_version": OFFICIAL_DOCUMENT_PARSER_VERSION,
                "valid_from": source["available_at"],
            })
        document_receipts[raw_path.name] = {
            **base_receipt,
            "n_verified_events": len(declared),
        }

    timeline = validate_candidate_timeline_source(pd.DataFrame(events))
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline.to_parquet(timeline_path, index=False)
    semantic_hash = candidate_timeline_fingerprint(timeline)

    races = pd.read_parquet(races_path)
    cutoff_map: dict[str, str] = {}
    for cycle, cutoffs in REQUIRED_CUTOFFS.items():
        for index, cutoff in enumerate(cutoffs):
            label = (
                f"senate-{cycle}-current" if cycle == 2026 else
                f"senate-{cycle}-lead-{60 if index == 0 else 30}"
            )
            cutoff_map[label] = cutoff
    structural_by_label = receipt.get("structural_unavailability") or {}
    polls_path = timeline_path.with_name("polls.parquet")
    polls = pd.read_parquet(polls_path) if polls_path.is_file() else pd.DataFrame()
    coverage: dict[str, Any] = {}
    for label, cutoff in cutoff_map.items():
        election_id = "-".join(label.split("-")[:2])
        subset = races[races["election_id"].astype(str).eq(election_id)].copy()
        cutoff_polls = polls[
            polls.get("election_id", pd.Series("", index=polls.index)).astype(str).eq(election_id)
        ].copy() if len(polls) else polls
        structural = [dict(gap) for gap in structural_by_label.get(label, [])]
        applied, _safe_polls, metadata = apply_candidate_state_contract(
            subset,
            timeline,
            cutoff_polls,
            as_of=cutoff,
            structural_gaps=structural,
        )
        required = applied[~applied["not_up"].fillna(False).astype(bool)].copy()
        covered = sorted(required.loc[
            required["candidate_state_eligible"].map(
                lambda value: bool(value) if pd.notna(value) else False
            ),
            "race_id",
        ].astype(str).unique())
        required_ids = sorted(required["race_id"].astype(str).unique())
        missing = sorted(metadata.get("identity_required_and_missing_race_ids") or [])
        structural_ids = {str(gap["race_id"]) for gap in structural}
        coverage[label] = {
            "as_of": cutoff,
            "required_race_ids": required_ids,
            "covered_race_ids": covered,
            "missing_race_ids": missing,
            "missing_source_race_ids": sorted(set(missing) - structural_ids),
            "structurally_unavailable": structural,
            "n_required": len(required_ids),
            "n_covered": len(covered),
            "n_missing": len(missing),
            "snapshot_sha256": metadata.get("snapshot_sha256"),
            "candidate_timeline_snapshot_sha256": metadata.get(
                "candidate_timeline_snapshot_sha256"
            ),
            "candidate_state_counts": metadata.get("counts") or {},
            "classification_records": metadata.get("classification_records") or [],
            "ambiguous_poll_matchups": metadata.get("ambiguous_poll_matchups") or [],
            "poll_exclusions": metadata.get("poll_exclusions") or [],
            "score_exclusions": metadata.get("score_exclusions") or [],
            "status": "ready" if not missing else "incomplete_coverage",
            "publication_eligible": not missing,
        }

    production_eligible = all(row["publication_eligible"] for row in coverage.values())
    timeline_manifest = {
        "schema_version": "candidate-timeline-v2",
        "parser_version": OFFICIAL_DOCUMENT_PARSER_VERSION,
        "source_receipt_sha256": _sha256(receipt_path.read_bytes()),
        "normalized_semantic_sha256": semantic_hash,
        "normalized_byte_sha256": _sha256(timeline_path.read_bytes()),
        "normalized_path": "data/normalized/candidate_timeline.parquet",
        "n_events": len(timeline),
        "election_ids": sorted(timeline["election_id"].astype(str).unique()),
        "source_objects": document_receipts,
        "source_traceability_required": True,
        "candidate_identity_requirement": "conditional_on_model_input_or_score_semantics",
        "production_eligible": production_eligible,
        "status": "ready" if production_eligible else "partial_official_coverage",
    }
    timeline_manifest_path.write_text(
        json.dumps(timeline_manifest, indent=2), encoding="utf-8",
    )

    previous = {}
    if source_audit_path.is_file():
        try:
            previous = json.loads(source_audit_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    report = {
        "schema_version": SOURCE_AUDIT_SCHEMA_VERSION,
        "parser_version": OFFICIAL_DOCUMENT_PARSER_VERSION,
        "generated_at": receipt.get("generated_at"),
        "current_as_of": REQUIRED_CUTOFFS[2026][0],
        "receipts": {
            "official_candidate_documents": document_receipts,
            "official_primary_calendars": calendar_receipts,
            "late_fec_candidate_workbooks": (
                (previous.get("receipts") or {}).get("late_fec_candidate_workbooks")
                or previous.get("receipts") or {}
            ),
        },
        "timeline": {
            "path": timeline_path.name,
            "semantic_sha256": semantic_hash,
            "n_events": len(timeline),
        },
        "coverage": coverage,
        "production_eligible": production_eligible,
        "status": "ready" if production_eligible else "incomplete_coverage",
        "note": (
            "Exact identities come only from source-verified pre-cutoff official documents. "
            "Ordinary binary races may use the fingerprinted conditional side-only contract; "
            "identity-sensitive transitions fail closed and nonbinary cutoffs are score-excluded. "
            "Late FEC ballot workbooks and final results remain archival evidence only."
        ),
    }
    source_audit_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    gaps_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


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
