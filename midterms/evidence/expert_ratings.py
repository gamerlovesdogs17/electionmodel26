"""Timestamped expert Senate race ratings (ablatable overlay layer)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.model.overlays import RATING_MARGIN, rating_from_probability

PARSER_VERSION = "expert-ratings-v1"

# Research snapshot: public-facing consensus-style categories for Class II 2026.
# Not affiliated with Cook/IE/Sabato; maintained as an auditable, dated fixture
# that can be replaced by an external CSV via fetch/write.
DEFAULT_RATINGS_2026: list[dict[str, Any]] = [
    # Solid / Likely D
    {"state": "CA", "rating": "Solid D"},  # not Class II — ignored if absent
    {"state": "CO", "rating": "Likely D"},
    {"state": "DE", "rating": "Solid D"},
    {"state": "IL", "rating": "Solid D"},
    {"state": "MA", "rating": "Solid D"},
    {"state": "NJ", "rating": "Solid D"},
    {"state": "NM", "rating": "Likely D"},
    {"state": "OR", "rating": "Solid D"},
    {"state": "RI", "rating": "Solid D"},
    {"state": "VA", "rating": "Likely D"},
    {"state": "GA", "rating": "Lean D"},
    {"state": "MI", "rating": "Lean D"},
    {"state": "MN", "rating": "Lean D"},
    {"state": "NH", "rating": "Tossup"},
    {"state": "NC", "rating": "Lean D"},
    # Competitive / R-leaning Class II
    {"state": "ME", "rating": "Lean R"},
    {"state": "TX", "rating": "Tossup"},
    {"state": "AK", "rating": "Likely R"},
    {"state": "IA", "rating": "Likely R"},
    {"state": "OH", "rating": "Likely R"},
    {"state": "FL", "rating": "Likely R"},
    # Solid / Likely R
    {"state": "AL", "rating": "Solid R"},
    {"state": "AR", "rating": "Solid R"},
    {"state": "ID", "rating": "Solid R"},
    {"state": "KS", "rating": "Solid R"},
    {"state": "KY", "rating": "Solid R"},
    {"state": "LA", "rating": "Solid R"},
    {"state": "MS", "rating": "Solid R"},
    {"state": "MT", "rating": "Likely R"},
    {"state": "NE", "rating": "Solid R"},
    {"state": "OK", "rating": "Solid R"},
    {"state": "SC", "rating": "Solid R"},
    {"state": "SD", "rating": "Solid R"},
    {"state": "TN", "rating": "Solid R"},
    {"state": "WV", "rating": "Solid R"},
    {"state": "WY", "rating": "Solid R"},
]


def _stamp_rows(
    rows: list[dict[str, Any]],
    *,
    election_id: str,
    available_at: str,
    source: str,
) -> pd.DataFrame:
    out = []
    for r in rows:
        st = str(r["state"]).upper()
        rating = str(r["rating"])
        out.append(
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
            }
        )
    return pd.DataFrame(out)


def write_expert_ratings_store(
    election_id: str = "senate-2026",
    *,
    available_at: str = "2026-09-01",
    csv_path: Path | None = None,
) -> dict[str, Any]:
    """Persist timestamped expert ratings (CSV override or curated defaults)."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    source = "curated_research_snapshot"
    if csv_path and Path(csv_path).exists():
        df_in = pd.read_csv(csv_path)
        rows = df_in.to_dict(orient="records")
        source = f"csv:{Path(csv_path).name}"
    else:
        rows = DEFAULT_RATINGS_2026

    df = _stamp_rows(rows, election_id=election_id, available_at=available_at, source=source)
    raw_path = RAW_DIR / "external" / "expert_ratings_senate.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(df.to_json(orient="records", indent=2))
    norm_path = NORMALIZED_DIR / "expert_ratings.parquet"
    df.to_parquet(norm_path, index=False)
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "election_id": election_id,
        "available_at": available_at,
        "n": int(len(df)),
        "source": source,
        "parser_version": PARSER_VERSION,
        "note": (
            "Curated research ratings for ablation overlays — replace via CSV. "
            "Not Cook/IE/Sabato redistribution."
        ),
    }
    man_path = MANIFESTS_DIR / "expert_ratings.json"
    man_path.write_text(json.dumps(man, indent=2))
    return {**man, "path": str(norm_path), "raw": str(raw_path)}


def load_expert_ratings(as_of: str | None = None, election_id: str | None = None) -> pd.DataFrame:
    path = NORMALIZED_DIR / "expert_ratings.parquet"
    if not path.exists():
        write_expert_ratings_store()
    df = pd.read_parquet(path)
    if election_id:
        df = df[df["election_id"] == election_id]
    if as_of and "available_at" in df.columns and len(df):
        df = df[pd.to_datetime(df["available_at"]).dt.date <= pd.Timestamp(as_of).date()]
    return df.reset_index(drop=True)


def ratings_for_races(
    race_ids: list[str],
    states: list[str],
    *,
    as_of: str | None = None,
    election_id: str = "senate-2026",
    fallback_probs: list[float] | None = None,
) -> pd.DataFrame:
    """Join expert ratings onto races; fall back to probability-derived labels."""
    expert = load_expert_ratings(as_of=as_of, election_id=election_id)
    by_state = expert.set_index("state")["rating"].to_dict() if len(expert) else {}
    rows = []
    for i, (rid, st) in enumerate(zip(race_ids, states)):
        if st in by_state:
            rating = by_state[st]
            source = "expert"
        elif fallback_probs is not None:
            rating = rating_from_probability(float(fallback_probs[i]))
            source = "model_derived"
        else:
            rating = "Tossup"
            source = "default"
        rows.append(
            {
                "race_id": rid,
                "state": st,
                "rating": rating,
                "source": source,
            }
        )
    return pd.DataFrame(rows)
