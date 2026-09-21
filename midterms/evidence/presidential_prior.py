"""Point-in-time statewide structural prior derived from verified presidential counts.

The source election is a statewide presidential result, including statewide
Maine and Nebraska totals. DC participates in the national reference but has
no Senate-state prior row. This module never reads Senate outcomes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import NORMALIZED_DIR
from midterms.evidence.presidential_results import (
    JURISDICTIONS,
    RECENCY_WEIGHTS_NEWEST_FIRST,
    SOURCES,
    STATE_SET,
    VOTE_STORE_PATH,
    select_source_years,
    verified_source_set_sha256,
)
from midterms.evidence.prior_sources import PriorSource, derive_relative_prior

PRIOR_METHOD = "observed_presidential_relative_v1"
PRIOR_STORE_VERSION = "presidential-relative-prior-v1"
PRIOR_SNAPSHOT_DIR = NORMALIZED_DIR / "prior_snapshots"


def _canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _validated_year_rows(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    rows = frame.loc[frame["election_year"].eq(year)].copy()
    if len(rows) != len(JURISDICTIONS) or set(rows["state"]) != JURISDICTIONS:
        raise ValueError(f"presidential source has missing or duplicate statewide rows: {year}")
    source = next(source for source in SOURCES if source.year == year)
    if not rows["source_sha256"].eq(source.sha256).all():
        raise ValueError(f"presidential source hash differs in normalized rows: {year}")
    if not rows["available_at"].eq(source.available_at.isoformat()).all():
        raise ValueError(f"presidential source vintage differs in normalized rows: {year}")
    for column in ("dem_votes", "rep_votes"):
        values = pd.to_numeric(rows[column], errors="coerce")
        if values.isna().any() or (values <= 0).any() or not (values % 1).eq(0).all():
            raise ValueError(f"invalid presidential vote counts: {year}/{column}")
    return rows.set_index("state")


def derive_state_prior_snapshot(
    vote_counts: pd.DataFrame, *, as_of: date, source_set_sha256: str,
) -> dict[str, Any]:
    """Pure, deterministic derivation; caller supplies a verified count frame."""
    years = select_source_years(as_of)
    if not years:
        raise ValueError(f"no published presidential source available at {as_of}")
    if len(source_set_sha256) != 64:
        raise ValueError("source-set SHA-256 is required")
    weights = RECENCY_WEIGHTS_NEWEST_FIRST if len(years) == 2 else (1.0,)
    year_rows = {year: _validated_year_rows(vote_counts, year) for year in years}
    national_margin: dict[int, float] = {}
    for year, rows in year_rows.items():
        dem = int(rows["dem_votes"].sum())
        rep = int(rows["rep_votes"].sum())
        national_margin[year] = 100.0 * (dem - rep) / (dem + rep)

    derived_rows: list[dict[str, Any]] = []
    for state in sorted(STATE_SET):
        sources: list[PriorSource] = []
        weight_map: dict[str, float] = {}
        for year, weight in zip(years, weights):
            source = next(source for source in SOURCES if source.year == year)
            row = year_rows[year].loc[state]
            dem, rep = int(row["dem_votes"]), int(row["rep_votes"])
            state_margin = 100.0 * (dem - rep) / (dem + rep)
            source_id = f"fec-president-{year}-{state}"
            sources.append(PriorSource(
                source_id=source_id, entity_id=state,
                observation_date=source.election_date, available_at=source.available_at,
                source_sha256=source.sha256, source_url=source.url,
                local_value=state_margin, national_reference=national_margin[year],
            ))
            weight_map[source_id] = float(weight)
        derived = derive_relative_prior(
            sources, entity_id=state, as_of=as_of, source_weights=weight_map,
        )
        derived_rows.append({
            "state": state,
            "prior_lean": derived["final_prior"],
            "prior_source": PRIOR_METHOD,
            "prior_production_eligible": len(years) == 2,
            "prior_provenance_sha256": derived["snapshot_sha256"],
            "provenance": derived,
        })
    payload: dict[str, Any] = {
        "version": PRIOR_STORE_VERSION,
        "method": PRIOR_METHOD,
        "as_of": as_of.isoformat(),
        "source_set_sha256": source_set_sha256,
        "source_years_newest_first": list(years),
        "recency_weights_newest_first": list(weights),
        "fallback": "one_published_source_non_production" if len(years) == 1 else None,
        "production_eligible": len(years) == 2,
        "rows": derived_rows,
    }
    payload["snapshot_sha256"] = _canonical_sha256(payload)
    return payload


def materialize_prior_snapshot(
    as_of: date, *, vote_store_path: Path = VOTE_STORE_PATH,
    out_dir: Path = PRIOR_SNAPSHOT_DIR,
) -> tuple[dict[str, Any], Path]:
    """Seal a derived side table; refuse to overwrite a changed snapshot."""
    source_sha = verified_source_set_sha256(vote_store_path=vote_store_path)
    frame = pd.read_parquet(vote_store_path)
    snapshot = derive_state_prior_snapshot(frame, as_of=as_of, source_set_sha256=source_sha)
    path = out_dir / f"{PRIOR_STORE_VERSION}_{as_of.isoformat()}.json"
    payload = json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False).encode()
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"sealed prior snapshot differs from current sources or code: {path}")
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return snapshot, path


def attach_prior_snapshot(races: pd.DataFrame, snapshot: dict[str, Any]) -> pd.DataFrame:
    """Attach the same side-table value and digest used by snapshot lineage."""
    if snapshot.get("snapshot_sha256") != _canonical_sha256({
        key: value for key, value in snapshot.items() if key != "snapshot_sha256"
    }):
        raise ValueError("prior snapshot fingerprint changed")
    by_state = {row["state"]: row for row in snapshot["rows"]}
    if len(by_state) != len(STATE_SET) or set(by_state) != STATE_SET:
        raise ValueError("prior snapshot does not cover 50 states")
    out = races.copy()
    if not set(out["state"].astype(str)).issubset(STATE_SET):
        raise ValueError("race row has no statewide prior source")
    out["prior_lean"] = out["state"].map(lambda state: by_state[state]["prior_lean"])
    for field in ("prior_source", "prior_production_eligible", "prior_provenance_sha256"):
        out[field] = out["state"].map(lambda state, name=field: by_state[state][name])
    out["prior_snapshot_sha256"] = snapshot["snapshot_sha256"]
    return out
