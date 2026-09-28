"""Point-in-time state demographic source contract.

The adapter seals supplied Census/ACS-like state rows.  It does not infer a
release date: a vintage without an official release date is ineligible for
historical replay.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
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
URBAN_SOURCE_URL = "https://www2.census.gov/geo/docs/reference/ua/State_Urban_Rural_Pop_2020_2010.xlsx"
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


def _download_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "electionmodel26/0.9.22 source preparation"})
    with urlopen(request, timeout=90) as response:
        return response.read()


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
    retrieved_at = datetime.now(timezone.utc).isoformat()
    sources: list[dict[str, Any]] = []
    urban_path = external / "census_state_urban_rural_2020_2010.xlsx"
    if not urban_path.is_file():
        if not fetch_missing:
            raise FileNotFoundError(urban_path)
        urban_path.write_bytes(_download_bytes(URBAN_SOURCE_URL))
    urban_sha = hashlib.sha256(urban_path.read_bytes()).hexdigest()
    urban_rows = parse_census_urban_workbook(urban_path)
    sources.append({
        "kind": "urban_rural_classification", "url": URBAN_SOURCE_URL,
        "path": urban_path.name, "sha256": urban_sha, "retrieved_at": retrieved_at,
        "release_dates": URBAN_RELEASE_DATES,
    })
    frames: list[pd.DataFrame] = []
    for election_year, policy in sorted(ACS_VINTAGE_POLICY.items()):
        end_year = int(policy["acs_end_year"])
        source_url = ACS_API_TEMPLATE.format(year=end_year) + "?" + urlencode({
            "get": ",".join(ACS_VARIABLES), "for": "state:*",
        })
        request_url = source_url
        if os.environ.get("CENSUS_API_KEY"):
            request_url += "&" + urlencode({"key": os.environ["CENSUS_API_KEY"]})
        source_path = external / f"census_acs5_{end_year}_state.json"
        if not source_path.is_file():
            if not fetch_missing:
                raise FileNotFoundError(source_path)
            raw = _download_bytes(request_url)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Census ACS API did not return JSON; current endpoint may require "
                    "CENSUS_API_KEY and no unverified substitute was written"
                ) from exc
        else:
            raw = source_path.read_bytes()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"sealed Census source is not valid JSON: {source_path.name}") from exc
        if not isinstance(payload, list) or len(payload) < 2:
            raise ValueError(f"Census ACS {end_year} response is empty or malformed")
        if not source_path.is_file():
            source_path.write_bytes(raw)
        acs_rows = pd.DataFrame(payload[1:], columns=payload[0])
        acs_sha = hashlib.sha256(raw).hexdigest()
        built = build_official_demographic_rows(
            election_year=election_year, acs_rows=acs_rows, urban_rows=urban_rows,
            acs_raw_sha256=acs_sha, urban_raw_sha256=urban_sha,
            retrieved_at=retrieved_at,
        )
        if len(built) != 50:
            raise ValueError(f"Census ACS {end_year} covers {len(built)}/50 Senate states")
        frames.append(built)
        sources.append({
            "kind": "acs_5_year_state", "election_year": election_year,
            "content_vintage": policy["acs_label"], "official_release_date": policy["release_date"],
            "url": source_url, "path": source_path.name, "sha256": acs_sha,
            "retrieved_at": retrieved_at,
        })
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
        "n_rows": int(len(combined)),
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
        "n_rows": int(len(frame)),
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
