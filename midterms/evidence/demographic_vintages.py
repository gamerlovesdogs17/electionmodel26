"""Point-in-time state demographic source contract.

The adapter seals supplied Census/ACS-like state rows.  It does not infer a
release date: a vintage without an official release date is ineligible for
historical replay.
"""

from __future__ import annotations

import csv
import hashlib
import json
import struct
import time
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR, ROOT

DEMOGRAPHIC_SCHEMA_VERSION = "demographic-vintage-source-v1"
DEMOGRAPHIC_PARSER_VERSION = "demographic-vintage-ingest-v1"
OFFICIAL_CENSUS_PARSER_VERSION = "census-acs-urban-v1"
DEMOGRAPHIC_FEATURE_VERSION = "senate-demographics-v1"

# Predeclared election-cycle policy. Dates are the official public releases,
# not retrieval times or forecast cutoffs.
ACS_VINTAGE_POLICY: dict[int, dict[str, Any]] = {
    2018: {"acs_end_year": 2016, "acs_label": "2012-2016", "release_date": "2017-12-07", "urban_year": 2010},
    2020: {"acs_end_year": 2018, "acs_label": "2014-2018", "release_date": "2019-12-19", "urban_year": 2010},
    2022: {"acs_end_year": 2020, "acs_label": "2016-2020", "release_date": "2022-03-17", "urban_year": 2010},
    2024: {"acs_end_year": 2022, "acs_label": "2018-2022", "release_date": "2023-12-07", "urban_year": 2020},
    2026: {"acs_end_year": 2024, "acs_label": "2020-2024", "release_date": "2026-01-29", "urban_year": 2020},
}
URBAN_RELEASE_DATES = {2010: "2012-09-27", 2020: "2022-12-29"}
ACS_API_TEMPLATE = "https://api.census.gov/data/{year}/acs/acs5"
ACS_SUMMARY_ROOT = "https://www2.census.gov/programs-surveys/acs/summary_file"
URBAN_SOURCE_URL = "https://www2.census.gov/geo/docs/reference/ua/State_Urban_Rural_Pop_2020_2010.xlsx"
TABLE_PREFIX_BYTES = 1_048_576
LEGACY_PREFIX_BYTES = 65_536
# The sequence layout changed in 2020. These positions come from the matching
# official Summary File template archive and are covered by parser tests.
LEGACY_SEQUENCE_LAYOUT = {
    2016: {"B01001": (2, 6), "B03002": (5, 37), "B15003": (43, 124)},
    2018: {"B01001": (2, 6), "B03002": (5, 37), "B15003": (43, 124)},
    2020: {"B01001": (1, 6), "B03002": (4, 37), "B15003": (42, 124)},
}
FIPS_STATE_NAMES = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas",
    "06": "California", "08": "Colorado", "09": "Connecticut", "10": "Delaware",
    "11": "District of Columbia", "12": "Florida", "13": "Georgia", "15": "Hawaii",
    "16": "Idaho", "17": "Illinois", "18": "Indiana", "19": "Iowa", "20": "Kansas",
    "21": "Kentucky", "22": "Louisiana", "23": "Maine", "24": "Maryland",
    "25": "Massachusetts", "26": "Michigan", "27": "Minnesota", "28": "Mississippi",
    "29": "Missouri", "30": "Montana", "31": "Nebraska", "32": "Nevada",
    "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico", "36": "New York",
    "37": "North Carolina", "38": "North Dakota", "39": "Ohio", "40": "Oklahoma",
    "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina",
    "46": "South Dakota", "47": "Tennessee", "48": "Texas", "49": "Utah",
    "50": "Vermont", "51": "Virginia", "53": "Washington", "54": "West Virginia",
    "55": "Wisconsin", "56": "Wyoming", "72": "Puerto Rico",
}
FIPS_STATE_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}
ACS_VARIABLES = (
    "NAME", "B15003_001E", "B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E",
    "B03002_001E", "B03002_003E", "B01001_001E",
    *tuple(f"B01001_{number:03d}E" for number in (*range(20, 26), *range(44, 50))),
)
REQUIRED_COLUMNS = (
    "dataset_id", "dataset_version", "geographic_level", "source_vintage",
    "official_release_date", "retrieved_at", "source_url", "raw_object_sha256",
    "feature_definition_version", "state",
)
REQUIRED_FEATURE_COLUMNS = ("college", "nonwhite", "density", "age", "urban")


def census_demographic_policy(election_year: int) -> dict[str, Any]:
    try:
        return dict(ACS_VINTAGE_POLICY[int(election_year)])
    except KeyError as exc:
        raise ValueError(f"no predeclared Census vintage for election year {election_year}") from exc


def transform_acs_state_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the production feature definitions to official ACS state rows."""
    required = set(ACS_VARIABLES) | {"state"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"ACS state source missing variables: {missing}")
    out = frame.copy()
    numeric = sorted(required - {"NAME", "state"})
    for column in numeric:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    college_n = out[[f"B15003_{number:03d}E" for number in range(22, 26)]].sum(axis=1)
    college = college_n / out["B15003_001E"]
    nonwhite = 1.0 - out["B03002_003E"] / out["B03002_001E"]
    age65_n = out[[f"B01001_{number:03d}E" for number in (*range(20, 26), *range(44, 50))]].sum(axis=1)
    age65_pct = 100.0 * age65_n / out["B01001_001E"]
    from midterms.evidence.wiki_ratings import STATE_NAME_TO_ABBR

    state_names = out["NAME"].astype(str).str.replace(r"^.*? of ", "", regex=True)
    state_abbr = state_names.map(STATE_NAME_TO_ABBR)
    result = pd.DataFrame({
        "state": state_abbr,
        "state_name": out["NAME"].astype(str),
        "college": college,
        "nonwhite": nonwhite,
        "age65_pct": age65_pct,
        "age": ((age65_pct - 16.0) / 10.0).clip(-1.5, 1.5),
    })
    result = result[result["state"].notna()].copy()
    if result[["college", "nonwhite", "age"]].isna().any().any():
        raise ValueError("ACS state source has missing or invalid feature denominators")
    return result


def attach_official_urban_features(
    acs_features: pd.DataFrame,
    urban_rows: pd.DataFrame,
    *,
    classification_year: int,
) -> pd.DataFrame:
    """Attach Census urban share while preserving the legacy density transform."""
    required = {"state", "classification_year", "urban_population", "total_population"}
    missing = sorted(required - set(urban_rows.columns))
    if missing:
        raise ValueError(f"Census urban source missing columns: {missing}")
    urban = urban_rows[
        pd.to_numeric(urban_rows["classification_year"], errors="coerce").eq(classification_year)
    ].copy()
    urban["urban_population"] = pd.to_numeric(urban["urban_population"], errors="coerce")
    urban["total_population"] = pd.to_numeric(urban["total_population"], errors="coerce")
    urban["urban"] = urban["urban_population"] / urban["total_population"]
    urban["rural_pct"] = 100.0 * (1.0 - urban["urban"])
    # Legacy model feature: log1p of urban percentage, historically named density.
    urban["density"] = (100.0 - urban["rural_pct"]).clip(lower=0.0).map(
        lambda value: float(__import__("numpy").log1p(value))
    ).clip(0.1, 5.0)
    merged = acs_features.merge(
        urban[["state", "urban", "density"]].assign(
            state=lambda x: x["state"].astype(str).str.upper()
        ), on="state", how="left", validate="one_to_one",
    )
    if merged[["urban", "density"]].isna().any().any():
        raise ValueError("official urban classification does not cover every ACS state")
    return merged


def build_official_demographic_rows(
    *,
    election_year: int,
    acs_rows: pd.DataFrame,
    urban_rows: pd.DataFrame,
    acs_raw_sha256: str,
    urban_raw_sha256: str,
    retrieved_at: str,
) -> pd.DataFrame:
    """Create a sealed vintage from already-downloaded official Census rows."""
    policy = census_demographic_policy(election_year)
    acs = transform_acs_state_features(acs_rows)
    combined = attach_official_urban_features(
        acs, urban_rows, classification_year=int(policy["urban_year"]),
    )
    combined = combined[~combined["state"].eq("DC")].copy()  # DC has no Senate seat.
    combined["dataset_id"] = "Census ACS 5-year + Census urban/rural classification"
    combined["dataset_version"] = f"ACS-{policy['acs_label']}+urban-{policy['urban_year']}"
    combined["geographic_level"] = "state"
    combined["source_vintage"] = policy["acs_label"]
    combined["official_release_date"] = policy["release_date"]
    combined["urban_classification_year"] = int(policy["urban_year"])
    combined["urban_official_release_date"] = URBAN_RELEASE_DATES[int(policy["urban_year"])]
    combined["retrieved_at"] = retrieved_at
    combined["source_url"] = ACS_API_TEMPLATE.format(year=policy["acs_end_year"])
    combined["urban_source_url"] = URBAN_SOURCE_URL
    combined["raw_object_sha256"] = acs_raw_sha256
    combined["urban_raw_object_sha256"] = urban_raw_sha256
    combined["feature_definition_version"] = DEMOGRAPHIC_FEATURE_VERSION
    combined["parser_version"] = OFFICIAL_CENSUS_PARSER_VERSION
    combined["election_year"] = int(election_year)
    return combined


def parse_census_urban_workbook(path: str | Path) -> pd.DataFrame:
    frame = pd.read_excel(path, sheet_name="States")
    required = {
        "STATE ABBREV", "2020 TOTAL POP", "2020 \nURBAN POP",
        "2010 \nTOTAL POP", "2010 \nURBAN POP",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Census urban workbook schema changed: {missing}")
    rows = []
    for _, row in frame.iterrows():
        for year in (2010, 2020):
            total_column = "2010 \nTOTAL POP" if year == 2010 else "2020 TOTAL POP"
            rows.append({
                "state": str(row["STATE ABBREV"]).strip(),
                "classification_year": year,
                "urban_population": row[f"{year} \nURBAN POP"],
                "total_population": row[total_column],
            })
    return pd.DataFrame(rows)


def _download_bytes(url: str, *, byte_range: tuple[int, int] | None = None) -> tuple[bytes, dict[str, str]]:
    headers = {"User-Agent": "electionmodel26/0.9.22 source preparation"}
    if byte_range is not None:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=90) as response:
        metadata = {
            "content_range": str(response.headers.get("Content-Range") or ""),
            "content_length": str(response.headers.get("Content-Length") or ""),
            "last_modified": str(response.headers.get("Last-Modified") or ""),
            "etag": str(response.headers.get("ETag") or ""),
        }
        return response.read(), metadata


def _legacy_source_url(year: int, state_name: str, filename: str) -> str:
    directory = state_name.replace(" of ", " Of ").replace(" ", "")
    return (
        f"{ACS_SUMMARY_ROOT}/{year}/data/5_year_seq_by_state/{directory}/"
        f"All_Geographies_Not_Tracts_Block_Groups/{filename}?download=1"
    )


def _legacy_filename(year: int, sequence: int) -> str:
    return f"{year}5us{sequence:04d}000.zip"


def _first_line_from_zip_prefix(raw: bytes) -> list[str]:
    if len(raw) < 30 or raw[:4] != b"PK\x03\x04":
        raise ValueError("ACS sequence capture does not start with a ZIP local header")
    fields = struct.unpack("<IHHHHHIIIHH", raw[:30])
    method, name_length, extra_length = fields[3], fields[9], fields[10]
    compressed = raw[30 + name_length + extra_length:]
    if method == 8:
        decoded = zlib.decompressobj(-15).decompress(compressed)
    elif method == 0:
        decoded = compressed
    else:
        raise ValueError(f"unsupported ZIP compression method {method}")
    first = decoded.splitlines()[0] if decoded.splitlines() else b""
    if not first:
        raise ValueError("ACS sequence prefix does not contain a complete first estimate row")
    return next(csv.reader([first.decode("utf-8")]))


def _parse_legacy_summary_rows(*, year: int, bundle_path: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    required_numbers = {
        "B01001": (1, *range(20, 26), *range(44, 50)),
        "B03002": (1, 3),
        "B15003": (1, *range(22, 26)),
    }
    with zipfile.ZipFile(bundle_path) as bundle:
        for fips, state_name in FIPS_STATE_NAMES.items():
            abbreviation = FIPS_STATE_ABBR[fips]
            row: dict[str, Any] = {"state": fips, "NAME": state_name}
            for table, numbers in required_numbers.items():
                sequence, first_column = LEGACY_SEQUENCE_LAYOUT[year][table]
                values = _first_line_from_zip_prefix(
                    bundle.read(f"{abbreviation.lower()}/seq{sequence:04d}.bin")
                )
                if values[5] != "0000001":
                    raise ValueError(
                        f"ACS {year} {abbreviation} sequence {sequence} first row is not state total"
                    )
                for number in numbers:
                    index = first_column + number - 1
                    row[f"{table}_{number:03d}E"] = values[index]
            rows.append(row)
    rows = pd.DataFrame(rows)
    required = set(ACS_VARIABLES) | {"state"}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"ACS {year} summary rows are missing variables: {missing}")
    return rows


def _capture_legacy_state_prefix_bundle(
    *, year: int, bundle_path: Path, receipt_path: Path, retrieved_at: str,
) -> dict[str, Any]:
    cache_dir = bundle_path.parent / f".{bundle_path.stem}_parts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, int, str]] = []
    for fips, state_name in sorted(FIPS_STATE_NAMES.items()):
        abbreviation = FIPS_STATE_ABBR[fips].lower()
        for sequence in sorted({item[0] for item in LEGACY_SEQUENCE_LAYOUT[year].values()}):
            filename = f"{year}5{abbreviation}{sequence:04d}000.zip"
            tasks.append((abbreviation, state_name, sequence, _legacy_source_url(year, state_name, filename)))

    def fetch(task: tuple[str, str, int, str]) -> tuple[str, bytes, dict[str, Any]]:
        abbreviation, state_name, sequence, url = task
        entry = f"{abbreviation}/seq{sequence:04d}.bin"
        cache_path = cache_dir / entry
        if cache_path.is_file():
            raw = cache_path.read_bytes()
            _first_line_from_zip_prefix(raw)
            headers: dict[str, str] = {}
        else:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            last_error: Exception | None = None
            for attempt in range(8):
                try:
                    raw, headers = _download_bytes(url, byte_range=(0, LEGACY_PREFIX_BYTES - 1))
                    if not raw or len(raw) > LEGACY_PREFIX_BYTES:
                        raise ValueError("range length is invalid")
                    _first_line_from_zip_prefix(raw)
                    cache_path.write_bytes(raw)
                    break
                except Exception as exc:  # Census sometimes returns a 200 HTML rejection page.
                    last_error = exc
                    if attempt == 7:
                        raise RuntimeError(f"failed to capture official ACS source {url}") from exc
                    time.sleep(1.0 + attempt)
            else:  # pragma: no cover - loop either breaks or raises
                raise RuntimeError(f"failed to capture official ACS source {url}") from last_error
        metadata = {
            "state": abbreviation.upper(), "state_name": state_name, "sequence": sequence,
            "url": url, "byte_range": [0, LEGACY_PREFIX_BYTES - 1],
            "captured_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "http_metadata": headers,
        }
        return entry, raw, metadata

    with ThreadPoolExecutor(max_workers=2) as pool:
        captures = list(pool.map(fetch, tasks))
    captures.sort(key=lambda item: item[0])
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as bundle:
        for entry, raw, _ in captures:
            info = zipfile.ZipInfo(entry, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, raw)
    receipt = {
        "schema_version": "census-acs-state-sequence-prefix-bundle-v1",
        "year": year, "retrieved_at": retrieved_at,
        "capture_bytes_per_source": LEGACY_PREFIX_BYTES,
        "sources": [metadata for _, _, metadata in captures],
        "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def _parse_table_prefix_rows(*, year: int, external: Path) -> pd.DataFrame:
    selected: dict[str, dict[str, Any]] = {}
    for table in ("B01001", "B03002", "B15003"):
        path = external / f"census_acs5_{year}_{table.lower()}_state_prefix.dat"
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="|")
            for record in reader:
                geo_id = str(record.get("GEO_ID") or "")
                if not geo_id.startswith("0400000US"):
                    continue
                fips = geo_id[-2:]
                if fips not in FIPS_STATE_NAMES:
                    continue
                row = selected.setdefault(
                    fips, {"state": fips, "NAME": FIPS_STATE_NAMES[fips]},
                )
                for source_name, value in record.items():
                    if source_name and source_name.startswith(f"{table}_E"):
                        number = source_name.rsplit("E", 1)[-1]
                        row[f"{table}_{number}E"] = value
    rows = pd.DataFrame(selected.values())
    if len(rows) != 52:
        raise ValueError(f"ACS {year} table prefixes cover {len(rows)}/52 state geographies")
    required = set(ACS_VARIABLES) | {"state"}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"ACS {year} table prefixes are missing variables: {missing}")
    return rows


def _bundle_sha256(records: list[dict[str, Any]]) -> str:
    identity = [
        {key: row.get(key) for key in ("url", "sha256", "byte_range", "remote_size")}
        for row in records
    ]
    blob = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def prepare_official_census_demographic_vintages(
    *,
    raw_dir: Path = RAW_DIR,
    normalized_path: Path | None = None,
    manifest_path: Path | None = None,
    fetch_missing: bool = True,
) -> dict[str, Any]:
    """Fetch/seal official ACS and urban files, then build every declared vintage.

    The function is atomic with respect to the normalized store: if any source
    is missing, malformed, or incomplete, no replacement demographic store is
    written.
    """
    external = raw_dir / "external"
    external.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(UTC).isoformat()
    sources: list[dict[str, Any]] = []
    urban_path = external / "census_state_urban_rural_2020_2010.xlsx"
    if not urban_path.is_file():
        if not fetch_missing:
            raise FileNotFoundError(urban_path)
        urban_raw, urban_headers = _download_bytes(URBAN_SOURCE_URL)
        urban_path.write_bytes(urban_raw)
    else:
        urban_headers = {}
    urban_sha = hashlib.sha256(urban_path.read_bytes()).hexdigest()
    urban_rows = parse_census_urban_workbook(urban_path)
    sources.append({
        "kind": "urban_rural_classification", "url": URBAN_SOURCE_URL,
        "path": urban_path.name, "sha256": urban_sha, "retrieved_at": retrieved_at,
        "release_dates": URBAN_RELEASE_DATES, "http_metadata": urban_headers,
    })
    frames: list[pd.DataFrame] = []
    for election_year, policy in sorted(ACS_VINTAGE_POLICY.items()):
        end_year = int(policy["acs_end_year"])
        vintage_sources: list[dict[str, Any]] = []
        if end_year in LEGACY_SEQUENCE_LAYOUT:
            bundle_path = external / f"census_acs5_{end_year}_state_sequence_prefixes.zip"
            receipt_path = external / f"census_acs5_{end_year}_state_sequence_prefixes_receipt.json"
            if not bundle_path.is_file() or not receipt_path.is_file():
                if not fetch_missing:
                    raise FileNotFoundError(bundle_path if not bundle_path.is_file() else receipt_path)
                receipt = _capture_legacy_state_prefix_bundle(
                    year=end_year, bundle_path=bundle_path, receipt_path=receipt_path,
                    retrieved_at=retrieved_at,
                )
            else:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            if bundle_sha != receipt.get("bundle_sha256"):
                raise ValueError(f"ACS {end_year} prefix bundle hash changed")
            with zipfile.ZipFile(bundle_path) as bundle:
                for item in receipt.get("sources", []):
                    entry = f"{str(item['state']).lower()}/seq{int(item['sequence']):04d}.bin"
                    if hashlib.sha256(bundle.read(entry)).hexdigest() != item.get("sha256"):
                        raise ValueError(f"ACS {end_year} bundle member hash changed: {entry}")
            record = {
                "kind": "acs_5_year_state_sequence_prefix_bundle",
                "election_year": election_year, "content_vintage": policy["acs_label"],
                "official_release_date": policy["release_date"],
                "path": bundle_path.name, "sha256": bundle_sha,
                "receipt_path": receipt_path.name,
                "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                "retrieved_at": receipt["retrieved_at"], "capture": "http_byte_ranges",
                "capture_bytes_per_source": LEGACY_PREFIX_BYTES,
                "n_source_archives": len(receipt.get("sources", [])),
                "source_records": receipt.get("sources", []),
            }
            vintage_sources.append(record)
            sources.append(record)
            acs_rows = _parse_legacy_summary_rows(year=end_year, bundle_path=bundle_path)
            source_url = f"{ACS_SUMMARY_ROOT}/{end_year}/data/5_year_seq_by_state/"
        else:
            for table in ("b01001", "b03002", "b15003"):
                source_url = (
                    f"{ACS_SUMMARY_ROOT}/{end_year}/table-based-SF/data/5YRData/"
                    f"acsdt5y{end_year}-{table}.dat?download=1"
                )
                source_path = external / f"census_acs5_{end_year}_{table}_state_prefix.dat"
                headers: dict[str, str] = {}
                if not source_path.is_file():
                    if not fetch_missing:
                        raise FileNotFoundError(source_path)
                    last_error: Exception | None = None
                    for attempt in range(8):
                        try:
                            raw, headers = _download_bytes(
                                source_url, byte_range=(0, TABLE_PREFIX_BYTES - 1),
                            )
                            if not raw.startswith(b"GEO_ID|"):
                                raise ValueError("table capture does not start with its header")
                            break
                        except Exception as exc:
                            last_error = exc
                            if attempt == 7:
                                raise RuntimeError(
                                    f"failed to capture official ACS source {source_url}"
                                ) from exc
                            time.sleep(2.0 + attempt)
                    else:  # pragma: no cover
                        raise RuntimeError(
                            f"failed to capture official ACS source {source_url}"
                        ) from last_error
                    if len(raw) != TABLE_PREFIX_BYTES:
                        raise ValueError(
                            f"ACS {end_year} {table} byte-range capture returned {len(raw)} bytes"
                        )
                    source_path.write_bytes(raw)
                raw = source_path.read_bytes()
                if len(raw) != TABLE_PREFIX_BYTES:
                    raise ValueError(f"sealed ACS prefix has wrong size: {source_path.name}")
                content_range = headers.get("content_range", "")
                remote_size = content_range.rsplit("/", 1)[-1] if "/" in content_range else None
                record = {
                    "kind": "acs_5_year_table_state_prefix", "election_year": election_year,
                    "content_vintage": policy["acs_label"],
                    "official_release_date": policy["release_date"], "url": source_url,
                    "path": source_path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                    "retrieved_at": retrieved_at, "capture": "http_byte_range",
                    "byte_range": [0, TABLE_PREFIX_BYTES - 1], "remote_size": remote_size,
                    "http_metadata": headers,
                }
                vintage_sources.append(record)
                sources.append(record)
            acs_rows = _parse_table_prefix_rows(year=end_year, external=external)
            source_url = f"{ACS_SUMMARY_ROOT}/{end_year}/table-based-SF/data/5YRData/"
        acs_sha = _bundle_sha256(vintage_sources)
        built = build_official_demographic_rows(
            election_year=election_year, acs_rows=acs_rows, urban_rows=urban_rows,
            acs_raw_sha256=acs_sha, urban_raw_sha256=urban_sha,
            retrieved_at=retrieved_at,
        )
        built["source_url"] = source_url
        if len(built) != 50:
            raise ValueError(f"Census ACS {end_year} covers {len(built)}/50 Senate states")
        frames.append(built)
    combined = validate_demographic_vintages(pd.concat(frames, ignore_index=True))
    normalized_path = normalized_path or (NORMALIZED_DIR / "demographic_vintages.parquet")
    manifest_path = manifest_path or (MANIFESTS_DIR / "demographic_vintages.json")
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(normalized_path, index=False)
    semantic = demographic_semantic_sha256(combined)
    manifest = {
        "schema_version": "official-census-demographic-vintages-v1",
        "parser_version": OFFICIAL_CENSUS_PARSER_VERSION,
        "feature_definition_version": DEMOGRAPHIC_FEATURE_VERSION,
        "normalized_semantic_sha256": semantic,
        "sources": sources,
        "vintage_policy": ACS_VINTAGE_POLICY,
        "urban_release_dates": URBAN_RELEASE_DATES,
        "n_rows": len(combined),
        "coverage_states": sorted(combined["state"].unique()),
        "production_eligible": len(combined) == 250,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (external / "census_demographic_sources.json").write_text(
        json.dumps({"sources": sources, "parser_version": OFFICIAL_CENSUS_PARSER_VERSION}, indent=2),
        encoding="utf-8",
    )
    return manifest


def _canonical_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    columns = sorted(frame.columns.astype(str))
    records = []
    for row in frame.to_dict(orient="records"):
        clean = {}
        for column in columns:
            value = row.get(column)
            clean[column] = None if pd.isna(value) else value
        records.append(clean)
    records.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"), default=str))
    return records


def demographic_semantic_sha256(frame: pd.DataFrame) -> str:
    blob = json.dumps(
        _canonical_records(frame), sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("rows")
        if not isinstance(payload, list):
            raise ValueError("demographic JSON must be a list or {'rows': [...]} object")
        return pd.DataFrame(payload)
    raise ValueError("demographic input must be CSV, JSON, or Parquet")


def validate_demographic_vintages(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(REQUIRED_COLUMNS + REQUIRED_FEATURE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"demographic vintages missing required columns: {missing}")
    out = frame.copy()
    for column in REQUIRED_COLUMNS:
        blank = out[column].isna() | out[column].astype(str).str.strip().eq("")
        if blank.any():
            raise ValueError(f"demographic vintages have {int(blank.sum())} blank {column} values")
    releases = pd.to_datetime(out["official_release_date"], errors="coerce", utc=True)
    retrieved = pd.to_datetime(out["retrieved_at"], errors="coerce", utc=True)
    if releases.isna().any():
        raise ValueError("demographic official_release_date is invalid or unknown")
    if retrieved.isna().any():
        raise ValueError("demographic retrieved_at is invalid")
    out["official_release_date"] = releases.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["retrieved_at"] = retrieved.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not out["raw_object_sha256"].astype(str).str.lower().str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("demographic raw_object_sha256 must be a SHA-256 hex digest")
    if not out["geographic_level"].astype(str).str.lower().eq("state").all():
        raise ValueError("Senate demographic vintages must use statewide geography")
    return out.sort_values(
        ["official_release_date", "source_vintage", "state"], kind="stable",
    ).reset_index(drop=True)


def ingest_demographic_vintages(
    input_path: str | Path,
    *,
    normalized_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    path = Path(input_path)
    raw_input_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    frame = validate_demographic_vintages(_read(path))
    semantic_sha256 = demographic_semantic_sha256(frame)
    normalized_path = normalized_path or (NORMALIZED_DIR / "demographic_vintages.parquet")
    manifest_path = manifest_path or (MANIFESTS_DIR / "demographic_vintages.json")
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(normalized_path, index=False)
    manifest = {
        "schema_version": DEMOGRAPHIC_SCHEMA_VERSION,
        "parser_version": DEMOGRAPHIC_PARSER_VERSION,
        "raw_input_sha256": raw_input_sha256,
        "normalized_semantic_sha256": semantic_sha256,
        "datasets": sorted(frame["dataset_id"].astype(str).unique()),
        "vintages": sorted(frame["source_vintage"].astype(str).unique()),
        "release_dates": sorted(frame["official_release_date"].astype(str).unique()),
        "coverage_states": sorted(frame["state"].astype(str).unique()),
        "n_rows": len(frame),
        "normalized_path": (
            normalized_path.resolve().relative_to(ROOT.resolve()).as_posix()
            if normalized_path.resolve().is_relative_to(ROOT.resolve()) else normalized_path.name
        ),
        "production_eligible": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def select_demographic_vintage(
    frame: pd.DataFrame,
    *,
    as_of: str | date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select the latest fully declared vintage released by the cutoff."""
    cutoff = pd.Timestamp(as_of).date()
    validated = validate_demographic_vintages(frame)
    releases = pd.to_datetime(validated["official_release_date"], utc=True).dt.date
    usable = validated[releases <= cutoff].copy()
    if usable.empty:
        return usable, {
            "status": "unavailable",
            "as_of": cutoff.isoformat(),
            "production_eligible": False,
            "reason": "no demographic vintage has a verified release date on or before cutoff",
        }
    latest_release = pd.to_datetime(usable["official_release_date"], utc=True).max()
    selected = usable[pd.to_datetime(usable["official_release_date"], utc=True).eq(latest_release)].copy()
    coverage = sorted(selected["state"].astype(str).unique())
    return selected, {
        "status": "ready",
        "as_of": cutoff.isoformat(),
        "selected_release_date": latest_release.isoformat(),
        "selected_vintages": sorted(selected["source_vintage"].astype(str).unique()),
        "coverage_states": coverage,
        "n_states": len(coverage),
        "semantic_sha256": demographic_semantic_sha256(selected),
        "production_eligible": True,
    }
