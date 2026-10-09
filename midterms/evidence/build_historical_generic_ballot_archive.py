"""Build sealed historical generic-ballot archive from FiveThirtyEight poll lists.

Sources (same FTE poll-level schema; not Senate residuals or topline averages):
  - markjrieke/electiondata mirror of FTE generic_ballot_polls_historical.csv
    (cycles 2018, 2020)
  - Internet Archive snapshots of FTE projects.fivethirtyeight.com poll CSVs
    (cycles 2022, 2024)

Formal OOF consumes only rows with available_at <= cutoff via the shared
aggregate_generic_ballot summarizer.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import RAW_DIR, ROOT
from midterms.evidence.federal_election_day import federal_election_day
from midterms.evidence.generic_ballot_aggregate import AGGREGATION_CONFIG_ID

ARCHIVE_SCHEMA = "historical-generic-ballot-archive-v1"
ARCHIVE_PATH = RAW_DIR / "external" / "historical_generic_ballot_archive.json"

SOURCE_SPECS: list[dict[str, Any]] = [
    {
        "provider": "fivethirtyeight",
        "label": "fte_generic_ballot_polls_historical_markjrieke",
        "path": RAW_DIR / "external" / "fte_generic_ballot_polls_historical.csv",
        "cycles": (2018, 2020),
        "source_url": (
            "https://raw.githubusercontent.com/markjrieke/electiondata/main/"
            "data/polls/src/fte/generic_ballot_polls_historical.csv"
        ),
        "acquisition_method": "github_raw_mirror_of_fte_polls_page_historical_csv",
    },
    {
        "provider": "fivethirtyeight",
        "label": "fte_generic_ballot_polls_2022_wayback",
        "path": RAW_DIR / "external" / "fte_wayback" / "wayback_gb_2022_nov.csv",
        "cycles": (2022,),
        "source_url": (
            "https://web.archive.org/web/20221110000000id_/"
            "https://projects.fivethirtyeight.com/polls-page/data/generic_ballot_polls.csv"
        ),
        "acquisition_method": "internet_archive_id_snapshot_of_fte_polls_page_csv",
    },
    {
        "provider": "fivethirtyeight",
        "label": "fte_generic_ballot_polls_2024_wayback",
        "path": RAW_DIR / "external" / "fte_wayback" / "wayback_gb_2024_nov.csv",
        "cycles": (2024,),
        "source_url": (
            "https://web.archive.org/web/20241110000000id_/"
            "https://projects.fivethirtyeight.com/polls-page/data/generic_ballot_polls.csv"
        ),
        "acquisition_method": "internet_archive_id_snapshot_of_fte_polls_page_csv",
    },
]


def _repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_fte_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return None
    for fmt in (
        "%m/%d/%y %H:%M",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            # FTE timestamps are naive Eastern wall-clock dates; treat as UTC dates.
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_fte_date(value: object) -> str | None:
    dt = _parse_fte_datetime(value)
    return dt.date().isoformat() if dt is not None else None


def _row_from_fte(
    row: pd.Series,
    *,
    cycle: int,
    source_label: str,
    source_url: str,
    source_file_sha256: str,
    retrieved_at: str,
) -> dict[str, Any] | None:
    dem = row.get("dem")
    rep = row.get("rep")
    if pd.isna(dem) or pd.isna(rep):
        return None
    field_start = _parse_fte_date(row.get("start_date"))
    field_end = _parse_fte_date(row.get("end_date"))
    created = _parse_fte_datetime(row.get("created_at"))
    published_at = created.date().isoformat() if created is not None else None
    # Prefer FTE created_at as availability; never silently backdate to field_end.
    if published_at is None:
        return None
    available_at = published_at
    poll_id_raw = row.get("poll_id")
    question_id = row.get("question_id")
    poll_id = f"fte-gb-{cycle}-{poll_id_raw}-{question_id}"
    sample = row.get("sample_size")
    try:
        sample_size = int(sample) if pd.notna(sample) else None
    except (TypeError, ValueError):
        sample_size = None
    ed = federal_election_day(cycle)
    return {
        "poll_id": poll_id,
        "source_id": f"fte-poll-{poll_id_raw}",
        "question_id": None if pd.isna(question_id) else str(question_id),
        "election_id": f"senate-{cycle}",
        "election_year": cycle,
        "election_day": ed.isoformat(),
        "pollster": str(row.get("display_name") or row.get("pollster") or ""),
        "pollster_id": None if pd.isna(row.get("pollster_id")) else str(row.get("pollster_id")),
        "partisan": None if pd.isna(row.get("partisan")) else str(row.get("partisan")),
        "population": None if pd.isna(row.get("population")) else str(row.get("population")),
        "sample_size": sample_size,
        "field_start": field_start,
        "field_end": field_end,
        "published_at": published_at,
        "available_at": available_at,
        "available_at_precision": "fte_created_at_date",
        "dem": float(dem),
        "rep": float(rep),
        "ind": None if pd.isna(row.get("ind")) else float(row.get("ind")),
        "margin": float(dem) - float(rep),
        "url": None if pd.isna(row.get("url")) else str(row.get("url")),
        "source_provider": "fivethirtyeight",
        "source_label": source_label,
        "source_url": source_url,
        "source_file_sha256": source_file_sha256,
        "retrieved_at": retrieved_at,
        "lineage": {
            "provider": "fivethirtyeight",
            "source_label": source_label,
            "source_file_sha256": source_file_sha256,
        },
    }


def build_historical_generic_ballot_archive(
    *,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    retrieved = retrieved_at or datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    source_manifest: list[dict[str, Any]] = []
    missing: list[str] = []

    for spec in SOURCE_SPECS:
        path: Path = spec["path"]
        if not path.is_file():
            missing.append(_repo_rel(path))
            continue
        file_sha = _sha256_file(path)
        df = pd.read_csv(path)
        n_before = len(rows)
        for cycle in spec["cycles"]:
            part = df[df["cycle"] == cycle] if "cycle" in df.columns else df
            for _, row in part.iterrows():
                built = _row_from_fte(
                    row,
                    cycle=int(cycle),
                    source_label=str(spec["label"]),
                    source_url=str(spec["source_url"]),
                    source_file_sha256=file_sha,
                    retrieved_at=retrieved,
                )
                if built is not None:
                    rows.append(built)
        source_manifest.append(
            {
                "label": spec["label"],
                "provider": spec["provider"],
                "path": _repo_rel(path),
                "source_url": spec["source_url"],
                "acquisition_method": spec["acquisition_method"],
                "cycles": list(spec["cycles"]),
                "sha256": file_sha,
                "n_rows_added": len(rows) - n_before,
            }
        )

    # Stable identity: dedupe by poll_id keeping earliest available_at.
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = row["poll_id"]
        prev = by_id.get(pid)
        if prev is None or str(row["available_at"]) < str(prev["available_at"]):
            by_id[pid] = row
    deduped = sorted(
        by_id.values(),
        key=lambda r: (int(r["election_year"]), str(r["available_at"]), str(r["poll_id"])),
    )

    covered = sorted({int(r["election_year"]) for r in deduped})
    body = {
        "schema_version": ARCHIVE_SCHEMA,
        "source_provider": "fivethirtyeight",
        "retrieval_timestamp": retrieved,
        "query_acquisition_method": (
            "fte_poll_level_generic_ballot_csv_mirrors; "
            "point-in-time created_at as available_at; "
            "no senate residuals; no election-day averages copied backward"
        ),
        "covered_cycles": covered,
        "source_urls": [s["source_url"] for s in source_manifest],
        "source_files": source_manifest,
        "source_hashes": {s["path"]: s["sha256"] for s in source_manifest},
        "aggregation_config_id": AGGREGATION_CONFIG_ID,
        "missing_source_files": missing,
        "n_rows": len(deduped),
        "rows": deduped,
        "note": (
            "Poll-level national congressional generic ballot. Formal OOF may "
            "use a row only when available_at <= cutoff."
        ),
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    body["archive_semantic_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body


def write_historical_generic_ballot_archive(
    *,
    path: Path | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    payload = build_historical_generic_ballot_archive(retrieved_at=retrieved_at)
    dest = path or ARCHIVE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = _repo_rel(dest)
    return payload


if __name__ == "__main__":
    out = write_historical_generic_ballot_archive()
    print(json.dumps({
        "path": out["path"],
        "n_rows": out["n_rows"],
        "covered_cycles": out["covered_cycles"],
        "archive_semantic_hash": out["archive_semantic_hash"],
        "missing_source_files": out["missing_source_files"],
    }, indent=2))
