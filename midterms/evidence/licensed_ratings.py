"""Licensed expert-rating adapter (Cook-compatible) — no redistributed vendor data.

Blueprint §9.4: ratings enter as timestamped ablatable modules.
Cook / Inside Elections / Sabato content is **not** redistributed in this repo.
License holders drop a local CSV (gitignored); the model loads it when present.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR, ROOT
from midterms.model.overlays import RATING_MARGIN

PARSER_VERSION = "licensed-ratings-v1"

# Local-only paths (never commit vendor content)
LICENSED_DIR = ROOT / "data" / "licensed"
DEFAULT_COOK_CSV = LICENSED_DIR / "cook_senate_ratings.csv"
EXAMPLE_CSV = ROOT / "data" / "fixtures" / "licensed_ratings_example.csv"

# Map common Cook / IE labels → model rating vocabulary
_LABEL_MAP = {
    "solid democrat": "Solid D",
    "solid dem": "Solid D",
    "safe d": "Solid D",
    "safe dem": "Solid D",
    "likely democrat": "Likely D",
    "likely dem": "Likely D",
    "lean democrat": "Lean D",
    "lean dem": "Lean D",
    "toss-up": "Tossup",
    "toss up": "Tossup",
    "tossup": "Tossup",
    "tilt d": "Tilt D",
    "tilt dem": "Tilt D",
    "tilt r": "Tilt R",
    "tilt rep": "Tilt R",
    "lean republican": "Lean R",
    "lean rep": "Lean R",
    "likely republican": "Likely R",
    "likely rep": "Likely R",
    "solid republican": "Solid R",
    "solid rep": "Solid R",
    "safe r": "Solid R",
    "safe rep": "Solid R",
}


def licensed_csv_path() -> Path | None:
    """Resolve licensed CSV from env or default local path."""
    env = os.environ.get("COOK_RATINGS_CSV") or os.environ.get("LICENSED_RATINGS_CSV")
    if env:
        p = Path(env)
        return p if p.exists() else None
    if DEFAULT_COOK_CSV.exists():
        return DEFAULT_COOK_CSV
    return None


def normalize_rating_label(raw: str) -> str:
    s = str(raw or "").strip()
    if s in RATING_MARGIN:
        return s
    key = s.lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    if key in _LABEL_MAP:
        return _LABEL_MAP[key]
    # already "Likely D" style with odd spacing
    compact = s.replace("Democrat", "D").replace("Republican", "R")
    if compact in RATING_MARGIN:
        return compact
    raise ValueError(f"Unrecognized rating label: {raw!r}")


def load_licensed_ratings_csv(path: Path) -> pd.DataFrame:
    """
    Expected columns (flexible):
      state, rating [, available_at, election_id, source, race_id]
    """
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "state" not in cols or "rating" not in cols:
        raise ValueError("Licensed ratings CSV requires state,rating columns")
    rows = []
    for _, r in df.iterrows():
        st = str(r[cols["state"]]).upper().strip()
        rating = normalize_rating_label(str(r[cols["rating"]]))
        election_id = (
            str(r[cols["election_id"]])
            if "election_id" in cols and pd.notna(r[cols["election_id"]])
            else "senate-2026"
        )
        available_at = (
            str(r[cols["available_at"]])[:10]
            if "available_at" in cols and pd.notna(r[cols["available_at"]])
            else datetime.now(timezone.utc).date().isoformat()
        )
        source = (
            str(r[cols["source"]])
            if "source" in cols and pd.notna(r[cols["source"]])
            else "licensed:cook"
        )
        rows.append(
            {
                "election_id": election_id,
                "state": st,
                "race_id": f"{election_id}-{st}",
                "rating": rating,
                "implied_margin": float(RATING_MARGIN.get(rating, 0.0)),
                "source": source,
                "available_at": available_at,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "parser_version": PARSER_VERSION,
                "license_note": "Local licensed content — not redistributed by this repository",
            }
        )
    return pd.DataFrame(rows)


def write_example_licensed_csv() -> Path:
    """Schema example only — synthetic labels, not Cook content."""
    EXAMPLE_CSV.parent.mkdir(parents=True, exist_ok=True)
    EXAMPLE_CSV.write_text(
        "state,rating,available_at,election_id,source\n"
        "GA,Lean D,2026-09-01,senate-2026,example_schema_only\n"
        "NC,Tossup,2026-09-01,senate-2026,example_schema_only\n"
        "OH,Likely R,2026-09-01,senate-2026,example_schema_only\n"
    )
    return EXAMPLE_CSV


def try_ingest_licensed_ratings(
    election_id: str = "senate-2026",
    *,
    available_at: str | None = None,
) -> dict[str, Any]:
    """
    If a licensed CSV is present, merge into expert_ratings store with source=licensed:*.
    Returns status; never fabricates Cook ratings.
    """
    from midterms.evidence.expert_ratings import write_expert_ratings_store

    LICENSED_DIR.mkdir(parents=True, exist_ok=True)
    write_example_licensed_csv()
    path = licensed_csv_path()
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    if path is None:
        man = {
            "ok": False,
            "licensed_present": False,
            "hint": (
                f"Place a licensed CSV at {DEFAULT_COOK_CSV} or set COOK_RATINGS_CSV. "
                f"See schema example at {EXAMPLE_CSV}. Do not commit vendor files."
            ),
            "parser_version": PARSER_VERSION,
        }
        (MANIFESTS_DIR / "licensed_ratings.json").write_text(json.dumps(man, indent=2))
        return man

    lic = load_licensed_ratings_csv(path)
    if available_at:
        lic["available_at"] = available_at
    # Persist licensed slice separately (still local raw; gitignored under data/licensed)
    raw = RAW_DIR / "external" / "licensed_ratings_active.json"
    # Only write normalized merge metadata publicly — strip vendor rows from git-tracked raw if under licensed
    meta_public = {
        "ok": True,
        "licensed_present": True,
        "n": int(len(lic)),
        "states": sorted(lic["state"].unique()),
        "source_file": str(path),
        "sources": sorted(lic["source"].unique()),
        "parser_version": PARSER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Ratings content remains local; only provenance metadata is tracked.",
    }
    (MANIFESTS_DIR / "licensed_ratings.json").write_text(json.dumps(meta_public, indent=2))

    # Merge into expert store via CSV temp under licensed dir
    tmp = LICENSED_DIR / "_active_ingest.csv"
    lic[["state", "rating", "available_at", "election_id", "source"]].to_csv(tmp, index=False)
    write_expert_ratings_store(
        election_id=election_id,
        available_at=available_at or str(lic["available_at"].iloc[0]),
        csv_path=tmp,
    )
    # Also keep parquet of licensed-only for diagnostics (under licensed/)
    lic.to_parquet(LICENSED_DIR / "ratings_normalized.parquet", index=False)
    meta_public["normalized"] = str(LICENSED_DIR / "ratings_normalized.parquet")
    return meta_public
