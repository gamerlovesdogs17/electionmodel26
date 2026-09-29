"""Presidential approval vintages for as-of fundamentals (blueprint §5.1).

Live path: VoteHub CC BY approval polls for the sitting president (``donald-trump``).
Historical replay prefers immutable Internet Archive captures of FiveThirtyEight
poll files.  Their ``createddate``/``created_at`` fields are the poll-record
publication timestamps used for point-in-time filtering; field end remains an
observation date and is never promoted to availability.  The older compiled
archive remains an explicitly non-production fallback.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR, ROOT

PARSER_VERSION = "approval-v3-point-in-time-archive"
VOTEHUB_SUBJECT = "donald-trump"
VOTEHUB_SOURCE_URL = "https://api.votehub.com/polls?subject=donald-trump&poll_type=approval"
HISTORICAL_ARCHIVE = RAW_DIR / "external" / "historical_presidential_approval_polls_1937_2024.csv"
HISTORICAL_ARCHIVE_SOURCE_URL = (
    "https://github.com/lorenzo-ruffino/approval_rate_usa_president/"
    "blob/cfb4d48b11513a9ff350247a92148b7a366a9963/historical_approval_polls.csv"
)
HISTORICAL_ARCHIVE_PROVIDER = "Lorenzo Ruffino compiled presidential approval poll archive"
HISTORICAL_WINDOW_DAYS = 30
HISTORICAL_ARCHIVE_REPOSITORY_ADDED_AT = "2026-09-24T23:21:53Z"
PRESIDENT_PARTY = {"Barack Obama": "D", "Donald Trump": "R", "Joe Biden": "D"}

SOURCE_BACKED_APPROVAL_PARSER_VERSION = "approval-v4-fte-created-at"
SOURCE_BACKED_APPROVAL_SOURCES: tuple[dict[str, str], ...] = (
    {
        "source_id": "fte-trump-approval-20210414",
        "path": "fte_trump_approval_polls_20210414.csv",
        "provider": "FiveThirtyEight",
        "original_url": (
            "https://projects.fivethirtyeight.com/trump-approval-data/"
            "approval_polllist.csv"
        ),
        "archive_url": (
            "https://web.archive.org/web/20210414015923id_/https://"
            "projects.fivethirtyeight.com/trump-approval-data/approval_polllist.csv"
        ),
        "archive_captured_at": "2021-04-14T01:59:23Z",
        "sha256": "6f116dae09aaf042d42b123c11284ae918b6bd14849e61b7282504037c8a15f3",
        "license": "CC BY 4.0",
        "availability_column": "createddate",
        "format": "fte_trump_approval_v1",
    },
    {
        "source_id": "fte-biden-approval-20241129",
        "path": "fte_biden_approval_polls_20241129.csv",
        "provider": "FiveThirtyEight",
        "original_url": (
            "https://projects.fivethirtyeight.com/polls-page/data/"
            "president_approval_polls.csv"
        ),
        "archive_url": (
            "https://web.archive.org/web/20241129175748id_/https://"
            "projects.fivethirtyeight.com/polls-page/data/president_approval_polls.csv"
        ),
        "archive_captured_at": "2024-11-29T17:57:48Z",
        "sha256": "e16909073b63bbbc7f1fcddfc276cd5606ad5c4c5adcdb86f9baf4c86991ec2c",
        "license": "CC BY 4.0",
        "availability_column": "created_at",
        "format": "fte_biden_approval_v1",
    },
)

# Historical curated snapshots (pre-VoteHub coverage) for midterm as-of runs.
HISTORICAL_APPROVAL_VINTAGES: list[dict[str, Any]] = [
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
]


def parse_historical_approval_archive(path: str | Path = HISTORICAL_ARCHIVE) -> pd.DataFrame:
    """Parse the vendored archive without altering or rewriting its raw bytes."""
    path = Path(path)
    frame = pd.read_csv(path)
    required = {
        "president", "poll_start", "poll_end", "polling_institute",
        "approval", "disapproval", "sample_size",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"historical approval archive missing columns: {missing}")
    out = frame.copy()
    out["poll_start"] = pd.to_datetime(out["poll_start"], errors="coerce").dt.date
    out["poll_end"] = pd.to_datetime(out["poll_end"], errors="coerce").dt.date
    out["approval"] = pd.to_numeric(out["approval"], errors="coerce")
    out["disapproval"] = pd.to_numeric(out["disapproval"], errors="coerce")
    out["sample_size"] = pd.to_numeric(out["sample_size"], errors="coerce")
    out = out.dropna(subset=["poll_start", "poll_end", "approval", "disapproval"])
    out = out[out["poll_end"] >= out["poll_start"]].copy()
    out["net_approval"] = out["approval"] - out["disapproval"]
    # The archive has no publication timestamp. Never silently upgrade poll_end
    # into proof of public availability.
    out["available_at"] = out["poll_end"]
    out["availability_basis"] = "field_end_proxy_missing_publication_timestamp"
    out["poll_id"] = out.apply(
        lambda row: hashlib.sha256("|".join([
            str(row["president"]), str(row["polling_institute"]),
            row["poll_start"].isoformat(), row["poll_end"].isoformat(),
            str(row["approval"]), str(row["disapproval"]),
        ]).encode("utf-8")).hexdigest()[:24], axis=1,
    )
    return out.sort_values(["poll_end", "poll_start", "poll_id"], kind="stable").reset_index(drop=True)


def aggregate_historical_approval_cutoffs(
    polls: pd.DataFrame,
    *,
    cutoffs: dict[str, str | date],
    window_days: int = HISTORICAL_WINDOW_DAYS,
    raw_sha256: str,
) -> pd.DataFrame:
    """Aggregate polls knowable by each cutoff using a fixed trailing window."""
    rows: list[dict[str, Any]] = []
    for label, cutoff_value in sorted(cutoffs.items()):
        cutoff = pd.Timestamp(cutoff_value).date()
        lower = cutoff - timedelta(days=window_days)
        window = polls[
            (polls["available_at"] <= cutoff)
            & (polls["poll_end"] >= lower)
            & (polls["poll_end"] <= cutoff)
        ].copy()
        if window.empty:
            continue
        weights = np.sqrt(window["sample_size"].fillna(800.0).clip(lower=100.0))
        net = float(np.average(window["net_approval"].to_numpy(float), weights=weights))
        presidents = sorted(window["president"].astype(str).unique())
        if len(presidents) != 1 or presidents[0] not in PRESIDENT_PARTY:
            raise ValueError(f"approval cutoff {label} has ambiguous president identity")
        rows.append({
            "cutoff_id": label,
            "year": int(label.split("-")[1]),
            "available_at": cutoff.isoformat(),
            "white_house_party": PRESIDENT_PARTY[presidents[0]],
            "net_approval": round(net, 6),
            "n_polls": len(window),
            "window_days": int(window_days),
            "source": "vendored_compiled_individual_approval_polls",
            "source_url": HISTORICAL_ARCHIVE_SOURCE_URL,
            "source_provider": HISTORICAL_ARCHIVE_PROVIDER,
            "source_sha256": raw_sha256,
            "retrieved_at": None,
            "repository_added_at": HISTORICAL_ARCHIVE_REPOSITORY_ADDED_AT,
            "parser_version": PARSER_VERSION,
            "availability_basis": "field_end_proxy_missing_publication_timestamp",
            "production_eligible": False,
            "production_ineligible_reason": (
                "archive lacks per-poll publication timestamps and sealed retrieval/license lineage"
            ),
            "poll_ids_sha256": hashlib.sha256(
                "\n".join(sorted(window["poll_id"].astype(str))).encode("utf-8")
            ).hexdigest(),
        })
    return pd.DataFrame(rows)


def parse_source_backed_approval_polls(
    *,
    raw_dir: Path = RAW_DIR,
    sources: tuple[dict[str, str], ...] = SOURCE_BACKED_APPROVAL_SOURCES,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Parse hash-locked FTE polls with row-level publication timestamps.

    ``createddate`` and ``created_at`` mean the date the poll record was added
    to the source data.  They are used as availability timestamps.  Field end
    is retained separately as the observation date.  A missing file, hash
    mismatch, unknown format, or invalid timestamp fails closed.
    """
    frames: list[pd.DataFrame] = []
    source_blocks: list[dict[str, Any]] = []
    external = raw_dir / "external"
    receipt_path = external / "fte_approval_sources_receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"approval source receipt is missing: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("schema_version") != "fte-approval-source-receipt-v1":
        raise ValueError("approval source receipt schema is unsupported")
    retrieved_at = pd.to_datetime(receipt.get("retrieved_at"), errors="coerce", utc=True)
    if pd.isna(retrieved_at):
        raise ValueError("approval source receipt has invalid retrieved_at")
    receipt_sources = {
        str(item.get("source_id")): item for item in receipt.get("sources") or []
    }
    receipt_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    for source in sources:
        path = external / source["path"]
        if not path.is_file():
            raise FileNotFoundError(f"approval source is missing: {path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != source["sha256"]:
            raise ValueError(
                f"approval source hash mismatch for {source['source_id']}: "
                f"expected {source['sha256']}, got {actual}"
            )
        receipt_source = receipt_sources.get(source["source_id"])
        if not receipt_source:
            raise ValueError(f"approval source receipt omits {source['source_id']}")
        for key in ("path", "original_url", "archive_url", "archive_captured_at", "sha256"):
            if str(receipt_source.get(key)) != str(source.get(key)):
                raise ValueError(
                    f"approval source receipt {key} mismatch for {source['source_id']}"
                )
        if int(receipt_source.get("bytes") or -1) != path.stat().st_size:
            raise ValueError(f"approval source byte length mismatch for {source['source_id']}")
        raw = pd.read_csv(path)
        source_format = source["format"]
        if source_format == "fte_trump_approval_v1":
            required = {
                "president", "subgroup", "startdate", "enddate", "pollster",
                "samplesize", "approve", "disapprove", "poll_id",
                "question_id", "createddate",
            }
            missing = sorted(required - set(raw.columns))
            if missing:
                raise ValueError(f"{source['source_id']} missing columns: {missing}")
            raw = raw[raw["subgroup"].astype(str).eq("All polls")].copy()
            frame = pd.DataFrame({
                "president": raw["president"],
                "poll_start": raw["startdate"],
                "poll_end": raw["enddate"],
                "available_at": raw["createddate"],
                "pollster": raw["pollster"],
                "sample_size": raw["samplesize"],
                "approval": raw["approve"],
                "disapproval": raw["disapprove"],
                "poll_id_source": raw["poll_id"],
                "question_id": raw["question_id"],
                "population": raw["population"],
                "state": None,
            })
        elif source_format == "fte_biden_approval_v1":
            required = {
                "politician", "start_date", "end_date", "pollster",
                "sample_size", "yes", "no", "poll_id", "question_id",
                "created_at", "state",
            }
            missing = sorted(required - set(raw.columns))
            if missing:
                raise ValueError(f"{source['source_id']} missing columns: {missing}")
            # The fundamentals covariate is national presidential approval.
            # State-specific approval questions are not interchangeable with it.
            raw = raw[raw["state"].isna()].copy()
            frame = pd.DataFrame({
                "president": raw["politician"],
                "poll_start": raw["start_date"],
                "poll_end": raw["end_date"],
                "available_at": raw["created_at"],
                "pollster": raw["pollster"],
                "sample_size": raw["sample_size"],
                "approval": raw["yes"],
                "disapproval": raw["no"],
                "poll_id_source": raw["poll_id"],
                "question_id": raw["question_id"],
                "population": raw["population"],
                "state": raw["state"],
            })
        else:
            raise ValueError(f"unsupported approval source format: {source_format}")

        for column in ("poll_start", "poll_end", "available_at"):
            frame[column] = pd.to_datetime(
                frame[column], errors="coerce", format="mixed"
            ).dt.date
        for column in ("sample_size", "approval", "disapproval"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(
            subset=["president", "poll_start", "poll_end", "available_at", "approval", "disapproval"]
        ).copy()
        capture_date = pd.Timestamp(source["archive_captured_at"]).date()
        if (frame["available_at"] > capture_date).any():
            raise ValueError(f"{source['source_id']} contains availability after archive capture")
        if (frame["poll_end"] < frame["poll_start"]).any():
            raise ValueError(f"{source['source_id']} contains inverted field dates")
        frame["net_approval"] = frame["approval"] - frame["disapproval"]
        frame["availability_basis"] = "fte_poll_record_created_at"
        frame["source_id"] = source["source_id"]
        frame["source_url"] = source["original_url"]
        frame["source_archive_url"] = source["archive_url"]
        frame["source_sha256"] = actual
        frame["source_provider"] = source["provider"]
        frame["source_license"] = source["license"]
        source_id = source["source_id"]
        frame["poll_id"] = frame.apply(
            lambda row, source_id=source_id: hashlib.sha256("|".join([
                source_id, str(row["poll_id_source"]),
                str(row["question_id"]), str(row["population"]),
            ]).encode("utf-8")).hexdigest()[:24],
            axis=1,
        )
        frames.append(frame)
        source_blocks.append({
            **source,
            "status": "verified",
            "actual_sha256": actual,
            "retrieved_at": retrieved_at.isoformat(),
            "receipt_path": receipt_path.name,
            "receipt_sha256": receipt_sha256,
            "n_parsed_rows": len(frame),
        })

    polls = pd.concat(frames, ignore_index=True)
    polls = polls.sort_values(
        ["available_at", "poll_end", "source_id", "poll_id"], kind="stable"
    ).reset_index(drop=True)
    return polls, source_blocks


def aggregate_source_backed_approval_cutoffs(
    polls: pd.DataFrame,
    *,
    cutoffs: dict[str, str | date],
    window_days: int = HISTORICAL_WINDOW_DAYS,
) -> pd.DataFrame:
    """Aggregate only poll records published by each formal replay cutoff."""
    rows: list[dict[str, Any]] = []
    for label, cutoff_value in sorted(cutoffs.items()):
        cutoff = pd.Timestamp(cutoff_value).date()
        lower = cutoff - timedelta(days=window_days)
        window = polls[
            (polls["available_at"] <= cutoff)
            & (polls["poll_end"] >= lower)
            & (polls["poll_end"] <= cutoff)
        ].copy()
        if window.empty:
            continue
        weights = np.sqrt(window["sample_size"].fillna(800.0).clip(lower=100.0))
        net = float(np.average(window["net_approval"].to_numpy(float), weights=weights))
        presidents = sorted(window["president"].astype(str).unique())
        if len(presidents) != 1 or presidents[0] not in PRESIDENT_PARTY:
            raise ValueError(f"approval cutoff {label} has ambiguous president identity: {presidents}")
        rows.append({
            "cutoff_id": label,
            "year": int(label.split("-")[1]),
            "available_at": cutoff.isoformat(),
            "max_poll_available_at": max(window["available_at"]).isoformat(),
            "white_house_party": PRESIDENT_PARTY[presidents[0]],
            "net_approval": round(net, 6),
            "n_polls": len(window),
            "window_days": int(window_days),
            "source": "fte_archived_individual_approval_polls",
            "source_url": sorted(window["source_archive_url"].astype(str).unique()),
            "source_provider": "FiveThirtyEight via Internet Archive capture",
            "source_sha256": sorted(window["source_sha256"].astype(str).unique()),
            "parser_version": SOURCE_BACKED_APPROVAL_PARSER_VERSION,
            "availability_basis": "fte_poll_record_created_at",
            "production_eligible": True,
            "production_ineligible_reason": None,
            "poll_ids_sha256": hashlib.sha256(
                "\n".join(sorted(window["poll_id"].astype(str))).encode("utf-8")
            ).hexdigest(),
        })
    return pd.DataFrame(rows)


def _net_from_answers(answers: list[dict[str, Any]] | None) -> float | None:
    if not answers:
        return None
    approve = disapprove = None
    for a in answers:
        choice = str(a.get("choice") or "").strip().lower()
        pct = a.get("pct")
        if pct is None:
            continue
        try:
            val = float(pct)
        except (TypeError, ValueError):
            continue
        if choice.startswith("approve") and "dis" not in choice:
            approve = val
        elif choice.startswith("disapprove") or choice == "disapprove":
            disapprove = val
    if approve is None or disapprove is None:
        return None
    return float(approve - disapprove)


def fetch_votehub_trump_approval_polls() -> list[dict[str, Any]]:
    """Pull VoteHub approval polls for Donald Trump (CC BY 4.0)."""
    from midterms.evidence.ingest import votehub_get

    payload = votehub_get(
        "/polls", {"subject": VOTEHUB_SUBJECT, "poll_type": "approval"}
    )
    if isinstance(payload, dict):
        polls = payload.get("polls") or payload.get("results") or []
    else:
        polls = payload or []
    return list(polls)


def aggregate_votehub_approval_vintages(
    polls: list[dict[str, Any]] | None = None,
    *,
    window_days: int = 14,
    white_house_party: str = "R",
) -> pd.DataFrame:
    """Build dated net-approval vintages from VoteHub approval polls."""
    polls = polls if polls is not None else fetch_votehub_trump_approval_polls()
    rows = []
    for p in polls:
        end = str(p.get("end_date") or p.get("created_at") or "")[:10]
        if len(end) < 10:
            continue
        net = _net_from_answers(p.get("answers"))
        if net is None:
            continue
        n = p.get("sample_size")
        try:
            n_f = float(n) if n is not None else 800.0
        except (TypeError, ValueError):
            n_f = 800.0
        rows.append(
            {
                "end_date": end,
                "net": net,
                "n": max(100.0, n_f),
                "pollster": p.get("pollster"),
                "poll_id": p.get("id"),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["end_date"] = pd.to_datetime(df["end_date"]).dt.date
    # Monthly vintages from first poll month through latest.
    start = df["end_date"].min()
    end = df["end_date"].max()
    # Also stamp mid-month and month-end markers commonly used as as-of dates.
    stamps: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        for day in (1, 15):
            try:
                stamps.append(date(y, m, day))
            except ValueError:
                pass
        # next month
        m += 1
        if m > 12:
            m = 1
            y += 1
    stamps.append(end)
    stamps = sorted(set(stamps))

    out_rows = []
    retrieved = datetime.now(UTC).isoformat()
    for as_of in stamps:
        lo = as_of.toordinal() - int(window_days)
        window = df[
            (df["end_date"].map(lambda d: d.toordinal()) >= lo)
            & (df["end_date"] <= as_of)
        ]
        if window.empty:
            continue
        w = np.sqrt(window["n"].to_numpy(dtype=float))
        net = float(np.average(window["net"].to_numpy(dtype=float), weights=w))
        out_rows.append(
            {
                "year": int(as_of.year),
                "available_at": as_of.isoformat(),
                "white_house_party": white_house_party,
                "net_approval": round(net, 2),
                "n_polls": len(window),
                "window_days": window_days,
                "source": "votehub_approval_aggregate",
                "retrieved_at": retrieved,
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(out_rows)


def write_approval_store(*, prefer_votehub: bool = True) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(UTC).isoformat()
    archive_error: str | None = None
    source_backed_error: str | None = None
    archive_sha256: str | None = (
        hashlib.sha256(HISTORICAL_ARCHIVE.read_bytes()).hexdigest()
        if HISTORICAL_ARCHIVE.is_file() else None
    )
    source_blocks: list[dict[str, Any]] = []
    try:
        source_polls, source_blocks = parse_source_backed_approval_polls()
        from midterms.evidence.source_readiness import required_historical_cutoffs

        hist = aggregate_source_backed_approval_cutoffs(
            source_polls,
            cutoffs=required_historical_cutoffs(),
        )
        required_years = {2018, 2020, 2022, 2024}
        if set(pd.to_numeric(hist.get("year"), errors="coerce").dropna().astype(int)) != required_years:
            raise ValueError("source-backed approval polls do not cover all historical cycles")
    except Exception as exc:  # noqa: BLE001
        source_backed_error = str(exc)
        hist = pd.DataFrame()

    # The compiled archive remains a non-production fallback only.  It is
    # consulted when the hash-locked, timestamped sources are unavailable.
    if hist.empty and HISTORICAL_ARCHIVE.is_file():
        try:
            from midterms.evidence.source_readiness import required_historical_cutoffs

            hist = aggregate_historical_approval_cutoffs(
                parse_historical_approval_archive(HISTORICAL_ARCHIVE),
                cutoffs=required_historical_cutoffs(),
                raw_sha256=archive_sha256,
            )
        except Exception as exc:  # noqa: BLE001
            archive_error = str(exc)
            hist = pd.DataFrame()
    else:
        if hist.empty:
            archive_error = "vendored historical approval archive is missing"
    if hist.empty:
        hist = pd.DataFrame(HISTORICAL_APPROVAL_VINTAGES)
        hist["source"] = "curated_nonproduction_fixture"
        hist["retrieved_at"] = None
        hist["parser_version"] = PARSER_VERSION
        hist["n_polls"] = None
        hist["window_days"] = None
        hist["production_eligible"] = False
        hist["production_ineligible_reason"] = archive_error or "archive produced no cutoff rows"

    live = pd.DataFrame()
    fetch_error: str | None = None
    if prefer_votehub:
        try:
            live = aggregate_votehub_approval_vintages()
        except Exception as exc:  # noqa: BLE001
            fetch_error = str(exc)

    frames = [hist]
    if len(live):
        frames.append(live)
    df = pd.concat(frames, ignore_index=True)
    # Prefer VoteHub rows when both exist for the same available_at.
    df = df.sort_values(["available_at", "source"]).drop_duplicates(
        subset=["available_at"], keep="last"
    )

    raw = RAW_DIR / "external" / "pres_approval_vintages.json"
    raw.write_text(
        json.dumps(
            {
                "rows": df.to_dict(orient="records"),
                "parser_version": SOURCE_BACKED_APPROVAL_PARSER_VERSION,
                "votehub_n": len(live),
                "historical_n": len(hist),
            },
            indent=2,
            default=str,
        )
    )
    out = NORMALIZED_DIR / "pres_approval.parquet"
    df.to_parquet(out, index=False)

    live_n = int((df["source"] == "votehub_approval_aggregate").sum())
    historical_eligible = bool(len(hist)) and bool(hist["production_eligible"].fillna(False).all())
    source_backed = bool(
        len(hist)
        and hist["source"].astype(str).eq("fte_archived_individual_approval_polls").all()
    )
    tier = "aggregator" if (live_n > 0 or source_backed) else "compiled_archive"
    man = {
        "generated_at": generated_at,
        "n": len(df),
        "n_votehub": live_n,
        "n_historical_archive_cutoffs": len(hist),
        "parser_version": (
            SOURCE_BACKED_APPROVAL_PARSER_VERSION if source_backed else PARSER_VERSION
        ),
        "historical_sources": source_blocks,
        "historical_source_url": (
            [block["archive_url"] for block in source_blocks]
            if source_backed else HISTORICAL_ARCHIVE_SOURCE_URL
        ),
        "historical_source_provider": (
            "FiveThirtyEight via immutable Internet Archive captures"
            if source_backed else HISTORICAL_ARCHIVE_PROVIDER
        ),
        "historical_raw_sha256": archive_sha256,
        "historical_raw_sha256s": {
            block["path"]: block["actual_sha256"] for block in source_blocks
        },
        "historical_window_days": HISTORICAL_WINDOW_DAYS,
        "historical_availability_basis": (
            "fte_poll_record_created_at"
            if source_backed else "field_end_proxy_missing_publication_timestamp"
        ),
        "historical_production_eligible": historical_eligible,
        "historical_ineligible_reason": (
            None if historical_eligible else
            "per-poll publication timestamps and sealed retrieval/license lineage are absent"
        ),
        "source_url": (
            VOTEHUB_SOURCE_URL if live_n
            else [block["archive_url"] for block in source_blocks]
            if source_backed else HISTORICAL_ARCHIVE_SOURCE_URL
        ),
        "tier": tier,
        "license": "CC BY 4.0" if (live_n or source_backed) else None,
        "attribution": (
            "Historical polling data from FiveThirtyEight; archived by the Internet Archive"
            if source_backed else
            "Polling data from VoteHub (https://votehub.com)" if live_n else None
        ),
        "fetch_error": fetch_error,
        "source_backed_error": source_backed_error,
        "archive_error": archive_error,
        "note": (
            "Historical cutoffs use hash-locked FiveThirtyEight poll records and filter on "
            "the source created_at timestamp; field end is observation timing only."
            if source_backed else
            "Historical compiled poll archive only; strict historical lineage is incomplete."
        ),
        "paths": {
            "raw": raw.relative_to(ROOT).as_posix(),
            "normalized": out.relative_to(ROOT).as_posix(),
        },
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
