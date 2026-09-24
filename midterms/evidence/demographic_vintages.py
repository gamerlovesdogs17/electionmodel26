"""Point-in-time state demographic source contract.

The adapter seals supplied Census/ACS-like state rows.  It does not infer a
release date: a vintage without an official release date is ineligible for
historical replay.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, ROOT

DEMOGRAPHIC_SCHEMA_VERSION = "demographic-vintage-source-v1"
DEMOGRAPHIC_PARSER_VERSION = "demographic-vintage-ingest-v1"
REQUIRED_COLUMNS = (
    "dataset_id", "dataset_version", "geographic_level", "source_vintage",
    "official_release_date", "retrieved_at", "source_url", "raw_object_sha256",
    "feature_definition_version", "state",
)
REQUIRED_FEATURE_COLUMNS = ("college", "nonwhite", "density", "age", "urban")


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
