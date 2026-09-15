"""Presidential approval vintages for as-of fundamentals (blueprint §5.1)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR

PARSER_VERSION = "approval-v1"

# Curated research snapshots of net presidential approval (Approve−Disapprove).
# Public polling aggregates — not a licensed commercial feed.
APPROVAL_VINTAGES: list[dict[str, Any]] = [
    # year, as_of, white_house_party, net_approval
    {"year": 2014, "available_at": "2014-07-01", "white_house_party": "D", "net_approval": -8.0},
    {"year": 2014, "available_at": "2014-09-01", "white_house_party": "D", "net_approval": -10.0},
    {"year": 2014, "available_at": "2014-10-15", "white_house_party": "D", "net_approval": -11.0},
    {"year": 2016, "available_at": "2016-07-01", "white_house_party": "D", "net_approval": 2.0},
    {"year": 2016, "available_at": "2016-09-01", "white_house_party": "D", "net_approval": 1.0},
    {"year": 2016, "available_at": "2016-10-15", "white_house_party": "D", "net_approval": 0.0},
    {"year": 2018, "available_at": "2018-07-01", "white_house_party": "R", "net_approval": -8.0},
    {"year": 2018, "available_at": "2018-09-01", "white_house_party": "R", "net_approval": -10.0},
    {"year": 2018, "available_at": "2018-10-15", "white_house_party": "R", "net_approval": -9.0},
    {"year": 2020, "available_at": "2020-07-01", "white_house_party": "R", "net_approval": -12.0},
    {"year": 2020, "available_at": "2020-09-01", "white_house_party": "R", "net_approval": -10.0},
    {"year": 2020, "available_at": "2020-10-15", "white_house_party": "R", "net_approval": -8.0},
    {"year": 2022, "available_at": "2022-07-01", "white_house_party": "D", "net_approval": -14.0},
    {"year": 2022, "available_at": "2022-09-01", "white_house_party": "D", "net_approval": -12.0},
    {"year": 2022, "available_at": "2022-10-15", "white_house_party": "D", "net_approval": -11.0},
    {"year": 2024, "available_at": "2024-07-01", "white_house_party": "D", "net_approval": -16.0},
    {"year": 2024, "available_at": "2024-09-01", "white_house_party": "D", "net_approval": -15.0},
    {"year": 2024, "available_at": "2024-10-15", "white_house_party": "D", "net_approval": -14.0},
    {"year": 2026, "available_at": "2026-06-01", "white_house_party": "R", "net_approval": -4.0},
    {"year": 2026, "available_at": "2026-08-01", "white_house_party": "R", "net_approval": -6.0},
    {"year": 2026, "available_at": "2026-09-01", "white_house_party": "R", "net_approval": -5.0},
]


def write_approval_store() -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(APPROVAL_VINTAGES)
    df["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    df["parser_version"] = PARSER_VERSION
    df["source"] = "curated_public_aggregate_research_snapshot"
    raw = RAW_DIR / "external" / "pres_approval_vintages.json"
    raw.write_text(json.dumps({"rows": APPROVAL_VINTAGES, "parser_version": PARSER_VERSION}, indent=2))
    out = NORMALIZED_DIR / "pres_approval.parquet"
    df.to_parquet(out, index=False)
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": int(len(df)),
        "parser_version": PARSER_VERSION,
        "source_url": "https://projects.fivethirtyeight.com/trump-approval-ratings/",
        "tier": "curated",
        "note": "Curated public-aggregate net approval vintages for as-of fundamentals.",
        "paths": {"raw": str(raw), "normalized": str(out)},
    }
    (MANIFESTS_DIR / "pres_approval.json").write_text(json.dumps(man, indent=2))
    return man


def approval_as_of(as_of: str | date, *, election_year: int | None = None) -> dict[str, Any]:
    """Latest approval vintage available at `as_of` (optionally restricted to election year)."""
    path = NORMALIZED_DIR / "pres_approval.parquet"
    if not path.exists():
        write_approval_store()
    df = pd.read_parquet(path)
    as_of_d = date.fromisoformat(str(as_of)[:10])
    avail = pd.to_datetime(df["available_at"]).dt.date
    mask = avail <= as_of_d
    if election_year is not None:
        mask = mask & (df["year"].astype(int) == int(election_year))
    sub = df[mask]
    if sub.empty:
        # Fall back to nearest earlier year
        sub = df[avail <= as_of_d]
    if sub.empty:
        return {"net_approval": 0.0, "white_house_party": "R", "available_at": None, "source": "default"}
    row = sub.sort_values("available_at").iloc[-1]
    return {
        "net_approval": float(row["net_approval"]),
        "white_house_party": str(row["white_house_party"]),
        "available_at": str(row["available_at"]),
        "source": str(row.get("source") or "curated"),
        "year": int(row["year"]),
    }
