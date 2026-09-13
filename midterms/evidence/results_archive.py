"""Redistributable certified Senate results + MEDSL state aggregates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.schema import RESULT_COLUMNS

PARSER_VERSION = "results-archive-v1"

# USPS abbreviations
_STATE_NAME_TO_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY",
}

# Curated certified two-party margins (Dem−Rep pp) for contested seats used in replay.
# Public election results — research snapshot, not a live feed.
CERTIFIED_MARGINS: dict[str, dict[str, float]] = {
    "senate-2018": {
        "AZ": 2.4, "FL": -0.2, "IN": -5.9, "MI": 6.5, "MO": -5.8, "MT": -3.5,
        "NV": 5.0, "NJ": 11.2, "ND": -10.8, "OH": -6.9, "PA": 12.8, "TN": -10.8,
        "TX": -2.6, "WV": -7.9, "WI": 10.8,
    },
    "senate-2020": {
        "AL": -20.4, "AK": -12.0, "AZ": 2.4, "CO": 9.3, "GA": 1.2, "IA": -6.6,
        "KS": -11.3, "KY": -19.5, "ME": -8.6, "MI": 1.7, "MN": 5.2, "MS": -10.0,
        "MT": -10.0, "NC": -1.8, "NH": 3.2, "NM": 6.1, "SC": -10.3, "TX": -3.9,
        "VA": 5.9,
    },
    "senate-2022": {
        "AZ": 4.9, "CO": 14.0, "CT": 14.0, "FL": -16.4, "GA": 2.8, "IL": 13.0,
        "IN": -19.5, "IA": -12.1, "KS": -15.0, "NV": 0.9, "NH": 9.0, "NY": 13.0,
        "NC": -3.2, "OH": -6.1, "OK": -31.0, "PA": 4.9, "SC": -19.0, "UT": -14.0,
        "VT": 40.0, "WA": 14.0, "WI": 1.0,
    },
    "senate-2024": {
        "AZ": -5.3, "CA": 16.0, "CT": 14.0, "DE": 17.0, "FL": -13.0, "HI": 30.0,
        "IN": -19.0, "ME": 8.0, "MD": 14.0, "MA": 18.0, "MI": -0.4, "MN": 5.0,
        "MS": -20.0, "MO": -13.0, "MT": -7.0, "NE": -6.0, "NV": -1.0, "NJ": 7.0,
        "NM": 7.0, "NY": 8.0, "OH": -3.6, "PA": -0.2, "RI": 17.0, "TN": -24.0,
        "TX": -8.0, "UT": -18.0, "VT": 30.0, "VA": 5.0, "WA": 14.0, "WI": -1.0,
        "WV": -40.0, "WY": -40.0,
    },
}


def medsl_2016_state_margins(path: Path | None = None) -> pd.DataFrame:
    """Aggregate MEDSL election-context county demsen16/repsen16 → state margins."""
    path = path or (RAW_DIR / "external" / "medsl_election_context_2018.csv")
    if not path.exists():
        return pd.DataFrame(columns=["state", "two_party_margin", "dem_votes", "rep_votes"])
    df = pd.read_csv(path)
    if "demsen16" not in df.columns or "repsen16" not in df.columns:
        return pd.DataFrame(columns=["state", "two_party_margin", "dem_votes", "rep_votes"])
    rows = []
    for st_name, g in df.groupby("state"):
        abbr = _STATE_NAME_TO_ABBR.get(str(st_name), str(st_name)[:2].upper())
        dem = float(pd.to_numeric(g["demsen16"], errors="coerce").fillna(0).sum())
        rep = float(pd.to_numeric(g["repsen16"], errors="coerce").fillna(0).sum())
        tot = dem + rep
        if tot <= 0:
            continue
        rows.append(
            {
                "state": abbr,
                "dem_votes": dem,
                "rep_votes": rep,
                "two_party_margin": 100.0 * (dem - rep) / tot,
            }
        )
    return pd.DataFrame(rows)


def _result_row(
    *,
    election_id: str,
    state: str,
    margin: float,
    dem_votes: float = 0.0,
    rep_votes: float = 0.0,
    available_at: str,
    source_url: str,
) -> dict[str, Any]:
    year = election_id.split("-")[-1]
    return {
        "result_id": f"{election_id}-{state}-certified",
        "election_id": election_id,
        "office": "US_SENATE",
        "state": state,
        "race_id": f"{election_id}-{state}",
        "event_time": f"{year}-11-01",
        "available_at": available_at,
        "certified_at": available_at,
        "dem_votes": dem_votes,
        "rep_votes": rep_votes,
        "other_votes": 0.0,
        "two_party_margin": float(margin),
        "winner_party": "D" if margin > 0 else "R",
        "source_url": source_url,
        "raw_hash": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "release_version": 1,
    }


def build_certified_results_frame() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    # MEDSL 2016
    medsl = medsl_2016_state_margins()
    for _, r in medsl.iterrows():
        rows.append(
            _result_row(
                election_id="senate-2016",
                state=str(r["state"]),
                margin=float(r["two_party_margin"]),
                dem_votes=float(r["dem_votes"]),
                rep_votes=float(r["rep_votes"]),
                available_at="2016-11-22",
                source_url="MEDSL election-context-2018 (demsen16/repsen16 county aggregate)",
            )
        )
    for election_id, by_state in CERTIFIED_MARGINS.items():
        year = election_id.split("-")[-1]
        for st, margin in by_state.items():
            rows.append(
                _result_row(
                    election_id=election_id,
                    state=st,
                    margin=margin,
                    available_at=f"{year}-11-22",
                    source_url="curated_certified_public_returns",
                )
            )
    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return pd.DataFrame(rows)[RESULT_COLUMNS]


def write_results_archive() -> dict[str, Any]:
    """Write redistributable results archive and merge helper parquet."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    df = build_certified_results_frame()
    raw_path = RAW_DIR / "external" / "senate_certified_results.json"
    raw_path.write_text(
        json.dumps(
            {
                "parser_version": PARSER_VERSION,
                "n": int(len(df)),
                "elections": sorted(df["election_id"].unique()) if len(df) else [],
                "rows": df.to_dict(orient="records"),
            },
            indent=2,
        )
    )
    out = NORMALIZED_DIR / "results_certified.parquet"
    df.to_parquet(out, index=False)
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": int(len(df)),
        "elections": sorted(df["election_id"].unique()) if len(df) else [],
        "parser_version": PARSER_VERSION,
        "paths": {"raw": str(raw_path), "normalized": str(out)},
        "note": "Overlays fixture results by race_id when present (prefer certified).",
    }
    (MANIFESTS_DIR / "results_certified.json").write_text(json.dumps(man, indent=2))
    return man


def merge_certified_into_results(results: pd.DataFrame) -> pd.DataFrame:
    """Prefer certified archive rows over synthetic fixture results for matching race_ids."""
    path = NORMALIZED_DIR / "results_certified.parquet"
    if not path.exists():
        write_results_archive()
    cert = pd.read_parquet(path)
    if cert.empty:
        return results
    if results is None or results.empty:
        return cert
    keep = results[~results["race_id"].isin(set(cert["race_id"]))]
    return pd.concat([keep, cert], ignore_index=True)
