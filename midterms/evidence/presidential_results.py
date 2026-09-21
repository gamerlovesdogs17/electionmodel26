"""Immutable FEC presidential vote-count sources and point-in-time source selection.

This layer stores certified vote counts and source metadata. It does not derive
partisan leans or run a forecast.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
from pypdf import PdfReader

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.official_ballot import STATE_CLASSES

PARSER_VERSION = "fec-presidential-vote-counts-v1"
SOURCE_CAPTURE_DATE = "2026-09-20"
# Predeclared for a future derived-prior builder; this vote-count ingest does
# not itself assign relative values to states or parties.
RECENCY_WEIGHTS_NEWEST_FIRST = (2 / 3, 1 / 3)
RAW_SOURCE_DIR = RAW_DIR / "external" / "presidential"
VOTE_STORE_PATH = NORMALIZED_DIR / "presidential_vote_counts.parquet"
SOURCE_MANIFEST_PATH = MANIFESTS_DIR / "presidential_vote_sources.json"
STATE_SET = frozenset(STATE_CLASSES)
JURISDICTIONS = STATE_SET | {"DC"}


def _portable_path(path: Path) -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


@dataclass(frozen=True)
class OfficialVoteSource:
    year: int
    filename: str
    url: str
    title: str
    election_date: date
    available_at: date
    sha256: str
    provider: str = "Federal Election Commission"


# Dates use documented FEC publication notices, conservatively taking the
# dated notice (or the end of its stated week) rather than election night.
SOURCES = (
    OfficialVoteSource(
        2012, "fec_2012.xls", "https://www.fec.gov/documents/1684/2012pres.xls",
        "Federal Elections 2012: President", date(2012, 11, 6), date(2013, 8, 21),
        "8b3cb324ef9d14b69bd643681f753c3c3d5aa1c4b72eadbf03e010046ecf8067",
    ),
    OfficialVoteSource(
        2016, "fec_2016.xlsx", "https://www.fec.gov/documents/1890/federalelections2016.xlsx",
        "Federal Elections 2016", date(2016, 11, 8), date(2018, 1, 9),
        "b4a1d1383602bc388cfbdf1fbea2476476d32e0b44b44236d3a3910fa9782eb6",
    ),
    OfficialVoteSource(
        2020, "fec_2020.pdf", "https://www.fec.gov/resources/cms-content/documents/2020presgeresults.pdf",
        "Official 2020 Presidential General Election Results",
        date(2020, 11, 3), date(2021, 2, 1),
        "3665d4523618b9c86a6f26fa9b924dbb4ffb757d2b3c59a43c768e980821b0d7",
    ),
    OfficialVoteSource(
        2024, "fec_2024.xlsx", "https://www.fec.gov/documents/5645/2024presgeresults.xlsx",
        "Official 2024 Presidential General Election Results",
        date(2024, 11, 5), date(2025, 1, 24),
        "68acdee2924d771b92a05cd950dec850b462c633c05563207ac7e206116e7366",
    ),
)


def _verified_bytes(source: OfficialVoteSource, raw_dir: Path, *, fetch_missing: bool) -> bytes:
    path = raw_dir / source.filename
    if not path.exists():
        if not fetch_missing:
            raise FileNotFoundError(f"immutable FEC source missing: {path}")
        raw_dir.mkdir(parents=True, exist_ok=True)
        body = urlopen(Request(source.url, headers={"User-Agent": "midterms-source-ingest/1.0"}), timeout=45).read()
        digest = hashlib.sha256(body).hexdigest()
        if digest != source.sha256:
            raise ValueError(f"download hash differs from pinned FEC source: {source.filename}")
        path.write_bytes(body)
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != source.sha256:
        raise ValueError(f"raw FEC source changed: {path}")
    return body


def _int_votes(value: object) -> int:
    if pd.isna(value):
        raise ValueError("missing major-party vote count")
    numeric = float(str(value).replace(",", ""))
    if not numeric.is_integer():
        raise ValueError("nonintegral vote count")
    number = int(numeric)
    if number <= 0:
        raise ValueError("major-party vote count must be positive")
    return number


def _parse_excel(path: Path, source: OfficialVoteSource) -> dict[str, tuple[int, int, int | None]]:
    if source.year in {2012, 2016}:
        frame = pd.read_excel(path, sheet_name="Table 2. Electoral &  Pop Vote", header=None)
        dem_col, rep_col = (3, 4) if source.year == 2012 else (4, 3)
        header = frame.iloc[4 if source.year == 2012 else 3]
        if "(D)" not in str(header.iloc[dem_col]) or "(R)" not in str(header.iloc[rep_col]):
            raise ValueError(f"FEC major-party header mapping changed: {source.filename}")
    elif source.year == 2024:
        frame = pd.read_excel(path, sheet_name="OFFICIAL 2024 PRES GE RESULTS")
        dem_col, rep_col = frame.columns.get_loc("HARRIS"), frame.columns.get_loc("TRUMP")
    else:
        raise ValueError(f"unsupported Excel source year: {source.year}")
    rows: dict[str, tuple[int, int, int | None]] = {}
    for _, row in frame.iterrows():
        state = str(row.iloc[0]).strip().upper()
        if state not in JURISDICTIONS:
            continue
        if state in rows:
            raise ValueError(f"duplicate statewide row in {source.filename}: {state}")
        rows[state] = (_int_votes(row.iloc[dem_col]), _int_votes(row.iloc[rep_col]), None)
    return rows


def _parse_2020_pdf(path: Path) -> dict[str, tuple[int, int, int | None]]:
    reader = PdfReader(path)
    if len(reader.pages) != 12:
        raise ValueError("unexpected FEC 2020 source page count")
    pattern = re.compile(r"^\s*([A-Z]{2})\s+([\d,]+)(?:\s|$)")
    sides: list[dict[str, int]] = []
    for page_index, required_header in ((1, "STATE BIDEN"), (7, "STATE TRUMP")):
        text = reader.pages[page_index].extract_text(extraction_mode="layout")
        if not re.search(r"\s+".join(required_header.split()), text):
            raise ValueError("FEC 2020 source layout or candidate header changed")
        votes: dict[str, int] = {}
        for line in text.splitlines():
            match = pattern.match(line)
            if match and match.group(1) in JURISDICTIONS:
                state = match.group(1)
                if state in votes:
                    raise ValueError(f"duplicate statewide PDF row: {state}")
                votes[state] = _int_votes(match.group(2))
        sides.append(votes)
    if any(set(side) != JURISDICTIONS for side in sides):
        raise ValueError("FEC 2020 PDF is missing a statewide vote row")
    return {state: (sides[0][state], sides[1][state], None) for state in sorted(JURISDICTIONS)}


def select_source_years(as_of: date, *, sources: tuple[OfficialVoteSource, ...] = SOURCES) -> tuple[int, ...]:
    """Return at most two most recent source years known at the snapshot date."""
    available = sorted(
        (source.year for source in sources if source.election_date <= as_of and source.available_at <= as_of),
        reverse=True,
    )
    if len(available) != len(set(available)):
        raise ValueError("duplicate presidential source year")
    return tuple(available[:2])


def verified_source_set_sha256(
    *, raw_dir: Path = RAW_SOURCE_DIR, manifest_path: Path = SOURCE_MANIFEST_PATH,
    vote_store_path: Path = VOTE_STORE_PATH,
) -> str:
    """Verify the source manifest and pinned raw bytes before lineage use."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"presidential source manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("parser_version") != PARSER_VERSION:
        raise ValueError("presidential source parser version is stale")
    expected = [
        {"year": source.year, "url": source.url, "sha256": source.sha256,
         "election_date": source.election_date.isoformat(),
         "available_at": source.available_at.isoformat()}
        for source in SOURCES
    ]
    recorded = [
        {key: row.get(key) for key in expected[0]}
        for row in manifest.get("source_blocks", [])
    ]
    if recorded != expected:
        raise ValueError("presidential source manifest differs from pinned sources")
    expected_digest = hashlib.sha256(
        json.dumps({"parser_version": PARSER_VERSION, "sources": expected},
                   sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if manifest.get("source_set_sha256") != expected_digest:
        raise ValueError("presidential source-set fingerprint is stale")
    for source in SOURCES:
        _verified_bytes(source, raw_dir, fetch_missing=False)
    if not vote_store_path.exists() or hashlib.sha256(vote_store_path.read_bytes()).hexdigest() != manifest.get("normalized_sha256"):
        raise ValueError("presidential normalized vote store is stale or missing")
    return expected_digest


def build_vote_count_store(
    *, raw_dir: Path = RAW_SOURCE_DIR, out_path: Path = VOTE_STORE_PATH,
    manifest_path: Path = SOURCE_MANIFEST_PATH, fetch_missing: bool = False,
) -> dict[str, object]:
    """Verify immutable FEC files and normalize *counts* only, without a derived lean."""
    rows: list[dict[str, object]] = []
    source_blocks: list[dict[str, object]] = []
    for source in SOURCES:
        _verified_bytes(source, raw_dir, fetch_missing=fetch_missing)
        path = raw_dir / source.filename
        parsed = _parse_2020_pdf(path) if source.year == 2020 else _parse_excel(path, source)
        if set(parsed) != JURISDICTIONS:
            raise ValueError(f"FEC source lacks exactly 50 states plus DC: {source.year}")
        retrieved_at = SOURCE_CAPTURE_DATE
        for state, (dem, rep, other) in sorted(parsed.items()):
            rows.append({
                "election_year": source.year, "state": state, "dem_votes": dem,
                "rep_votes": rep, "other_votes": other, "prior_eligible_state": state in STATE_SET,
                "source_url": source.url, "source_title": source.title,
                "source_provider": source.provider, "retrieved_at": retrieved_at,
                "available_at": source.available_at.isoformat(),
                "election_date": source.election_date.isoformat(),
                "source_sha256": source.sha256, "parser_version": PARSER_VERSION,
            })
        block = asdict(source)
        block["election_date"] = source.election_date.isoformat()
        block["available_at"] = source.available_at.isoformat()
        block["retrieved_at"] = retrieved_at
        block["raw_path"] = _portable_path(path)
        source_blocks.append(block)
    frame = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_path, index=False)
    manifest = {
        "parser_version": PARSER_VERSION, "source_kind": "official_certified_vote_counts",
        "source_blocks": source_blocks, "n_rows": len(frame), "normalized_path": _portable_path(out_path),
        "derived_prior_status": "not_computed",
        "normalized_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
    }
    stable_sources = [
        {"year": block["year"], "url": block["url"], "sha256": block["sha256"],
         "election_date": block["election_date"], "available_at": block["available_at"]}
        for block in source_blocks
    ]
    manifest["source_set_sha256"] = hashlib.sha256(
        json.dumps({"parser_version": PARSER_VERSION, "sources": stable_sources},
                   sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
