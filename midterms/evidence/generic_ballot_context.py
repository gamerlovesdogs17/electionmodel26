"""Unified generic-ballot context for live forecasting and historical OOF.

Historical formal OOF must NOT derive a national generic ballot from the Senate
polls being scored. Live forecasting uses VoteHub national generic-ballot polls.
When a reconstructable historical series is unavailable, formal OOF fails closed.

Live and historical paths share ``aggregate_generic_ballot``; only the eligible
row set differs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, RAW_DIR, ROOT
from midterms.evidence.federal_election_day import (
    federal_election_day,
    federal_election_day_from_election_id,
    formal_cutoff,
)
from midterms.evidence.generic_ballot_aggregate import (
    AGGREGATION_CONFIG_ID,
    aggregate_generic_ballot,
)

GB_CONTEXT_SCHEMA = "generic-ballot-context-v1"
FORMAL_OOF_CYCLES = (2018, 2020, 2022, 2024)
FORMAL_OOF_LEADS = (60, 30)
HISTORICAL_GB_STORE = RAW_DIR / "external" / "historical_generic_ballot_archive.json"
PARITY_ARTIFACT_NAME = "generic_ballot_parity_v0925.json"


@dataclass(frozen=True)
class GenericBallotContext:
    schema_version: str
    election_id: str
    as_of: str
    margin: float | None
    n_polls: int
    source_ids: tuple[str, ...]
    field_dates: tuple[str, ...]
    available_at_dates: tuple[str, ...]
    weighting_method: str
    source_version_hash: str
    historically_reconstructable: bool
    fallback_used: bool
    fallback_reason: str | None
    formal_oof_eligible: bool
    path: str  # "votehub_live" | "historical_archive" | "unavailable" | "forbidden_senate_residual"
    aggregation_config_id: str = AGGREGATION_CONFIG_ID
    election_day: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def historical_gb_archive_hash(path: Path | None = None) -> str:
    """Semantic / content hash of the sealed historical GB archive."""
    archive = path or HISTORICAL_GB_STORE
    if not archive.is_file():
        return ""
    payload = json.loads(archive.read_text(encoding="utf-8"))
    if payload.get("archive_semantic_hash"):
        return str(payload["archive_semantic_hash"])
    return _sha256_text(archive.read_text(encoding="utf-8"))


def formal_oof_gb_fold_identity(
    election_id: str,
    as_of: str | date,
    *,
    archive_hash: str | None = None,
) -> str:
    """Cache / fold identity bound to historical GB source + aggregation config."""
    cutoff = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    try:
        ed_s = federal_election_day_from_election_id(election_id).isoformat()
    except ValueError:
        ed_s = None
    payload = {
        "election_id": election_id,
        "as_of": cutoff.isoformat(),
        "election_day": ed_s,
        "aggregation_config_id": AGGREGATION_CONFIG_ID,
        "historical_gb_archive_hash": archive_hash
        if archive_hash is not None
        else historical_gb_archive_hash(),
        "gb_context_schema": GB_CONTEXT_SCHEMA,
    }
    return _sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def resolve_generic_ballot_context(
    election_id: str,
    as_of: str | date,
    *,
    require_point_in_time: bool = True,
    allow_senate_poll_residual: bool = False,
    path: Path | None = None,
) -> GenericBallotContext:
    """Resolve national generic ballot with explicit lineage.

    ``allow_senate_poll_residual`` is intentionally False by default and must
    remain False for formal OOF. The previous residual estimator is forbidden.
    """
    cutoff = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    as_of_s = cutoff.isoformat()
    try:
        ed = federal_election_day_from_election_id(election_id)
        ed_s = ed.isoformat()
    except (ValueError, TypeError):
        ed_s = None

    if allow_senate_poll_residual:
        return GenericBallotContext(
            schema_version=GB_CONTEXT_SCHEMA,
            election_id=election_id,
            as_of=as_of_s,
            margin=None,
            n_polls=0,
            source_ids=(),
            field_dates=(),
            available_at_dates=(),
            weighting_method="forbidden_senate_poll_residual",
            source_version_hash="",
            historically_reconstructable=False,
            fallback_used=True,
            fallback_reason="senate_poll_residual_is_forbidden_for_formal_oof",
            formal_oof_eligible=False,
            path="forbidden_senate_residual",
            election_day=ed_s,
        )

    year = int(str(election_id).split("-")[-1])
    # Live / current-cycle path: VoteHub national GB aggregate.
    if year >= 2026 or election_id.endswith("2026"):
        from midterms.evidence.ingest import generic_ballot_aggregate

        agg = generic_ballot_aggregate(path=path, as_of=as_of_s)
        if agg is None:
            return GenericBallotContext(
                schema_version=GB_CONTEXT_SCHEMA,
                election_id=election_id,
                as_of=as_of_s,
                margin=None,
                n_polls=0,
                source_ids=(),
                field_dates=(),
                available_at_dates=(),
                weighting_method=AGGREGATION_CONFIG_ID,
                source_version_hash="",
                historically_reconstructable=True,
                fallback_used=True,
                fallback_reason="votehub_generic_ballot_unavailable_at_cutoff",
                formal_oof_eligible=False,
                path="unavailable",
                election_day=ed_s,
            )
        gb_path = path or (RAW_DIR / "external" / "votehub_generic_ballot_2026.json")
        blob = gb_path.read_bytes() if gb_path.is_file() else b""
        return GenericBallotContext(
            schema_version=GB_CONTEXT_SCHEMA,
            election_id=election_id,
            as_of=as_of_s,
            margin=float(agg["margin"]),
            n_polls=int(agg["n_polls"]),
            source_ids=tuple(agg.get("poll_ids") or ("votehub_generic_ballot_2026",)),
            field_dates=tuple(agg.get("field_dates") or (str(agg.get("ref_date") or as_of_s),)),
            available_at_dates=tuple(agg.get("available_at_dates") or (as_of_s,)),
            weighting_method=str(agg.get("method") or AGGREGATION_CONFIG_ID),
            source_version_hash=_sha256_text(blob.decode("utf-8", errors="replace")[:200000]),
            historically_reconstructable=True,
            fallback_used=False,
            fallback_reason=None,
            formal_oof_eligible=True,
            path="votehub_live",
            election_day=ed_s,
        )

    # Historical formal path: sealed archive only (no Senate residual).
    archive = HISTORICAL_GB_STORE
    if not archive.is_file():
        return GenericBallotContext(
            schema_version=GB_CONTEXT_SCHEMA,
            election_id=election_id,
            as_of=as_of_s,
            margin=None,
            n_polls=0,
            source_ids=(),
            field_dates=(),
            available_at_dates=(),
            weighting_method="historical_archive_missing",
            source_version_hash="",
            historically_reconstructable=False,
            fallback_used=True,
            fallback_reason="historical_generic_ballot_archive_absent",
            formal_oof_eligible=False,
            path="unavailable",
            election_day=ed_s,
        )

    payload = json.loads(archive.read_text(encoding="utf-8"))
    archive_hash = str(payload.get("archive_semantic_hash") or historical_gb_archive_hash(archive))
    rows = list(payload.get("rows") or [])
    eligible: list[dict[str, Any]] = []
    for row in rows:
        avail = str(row.get("available_at") or "")[:10]
        if not avail:
            # Do not fall back to field_end for eligibility.
            continue
        if require_point_in_time and date.fromisoformat(avail) > cutoff:
            continue
        row_year = row.get("election_year")
        row_eid = str(row.get("election_id") or "")
        if row_eid and row_eid != election_id and str(row_year) != str(year):
            continue
        if row_year is not None and int(row_year) != year:
            continue
        eligible.append(row)

    if not eligible:
        return GenericBallotContext(
            schema_version=GB_CONTEXT_SCHEMA,
            election_id=election_id,
            as_of=as_of_s,
            margin=None,
            n_polls=0,
            source_ids=(),
            field_dates=(),
            available_at_dates=(),
            weighting_method="historical_archive_empty_at_cutoff",
            source_version_hash=archive_hash,
            historically_reconstructable=True,
            fallback_used=True,
            fallback_reason="no_historical_gb_polls_on_or_before_as_of",
            formal_oof_eligible=False,
            path="unavailable",
            election_day=ed_s,
        )

    agg = aggregate_generic_ballot(eligible, as_of=cutoff)
    if agg is None or agg.get("margin") is None:
        return GenericBallotContext(
            schema_version=GB_CONTEXT_SCHEMA,
            election_id=election_id,
            as_of=as_of_s,
            margin=None,
            n_polls=0,
            source_ids=(),
            field_dates=(),
            available_at_dates=(),
            weighting_method=AGGREGATION_CONFIG_ID,
            source_version_hash=archive_hash,
            historically_reconstructable=True,
            fallback_used=True,
            fallback_reason="historical_gb_aggregation_failed",
            formal_oof_eligible=False,
            path="unavailable",
            election_day=ed_s,
        )

    return GenericBallotContext(
        schema_version=GB_CONTEXT_SCHEMA,
        election_id=election_id,
        as_of=as_of_s,
        margin=float(agg["margin"]),
        n_polls=int(agg["n_polls"]),
        source_ids=tuple(agg.get("poll_ids") or ()),
        field_dates=tuple(agg.get("field_dates") or ()),
        available_at_dates=tuple(agg.get("available_at_dates") or ()),
        weighting_method=str(agg.get("method") or AGGREGATION_CONFIG_ID),
        source_version_hash=archive_hash,
        historically_reconstructable=True,
        fallback_used=False,
        fallback_reason=None,
        formal_oof_eligible=True,
        path="historical_archive",
        election_day=ed_s,
    )


def require_formal_generic_ballot(
    election_id: str,
    as_of: str | date,
) -> GenericBallotContext:
    """Fail closed unless a reconstructable national GB exists at the cutoff."""
    ctx = resolve_generic_ballot_context(
        election_id, as_of, require_point_in_time=True, allow_senate_poll_residual=False
    )
    if not ctx.formal_oof_eligible or ctx.margin is None:
        raise ValueError(
            "formal OOF generic ballot unavailable: "
            f"{ctx.fallback_reason or ctx.path} (election={election_id}, as_of={ctx.as_of})"
        )
    return ctx


def write_generic_ballot_parity_artifact() -> dict[str, Any]:
    """Document the eight formal cutoffs and whether true GB exists."""
    rows = []
    archive_hash = historical_gb_archive_hash()
    for year in FORMAL_OOF_CYCLES:
        ed = federal_election_day(year)
        for lead in FORMAL_OOF_LEADS:
            cutoff = formal_cutoff(year, lead)
            ctx = resolve_generic_ballot_context(f"senate-{year}", cutoff)
            fold_id = formal_oof_gb_fold_identity(
                f"senate-{year}", cutoff, archive_hash=ctx.source_version_hash or archive_hash
            )
            rows.append(
                {
                    "election_id": f"senate-{year}",
                    "lead_days": lead,
                    "election_day": ed.isoformat(),
                    "as_of": cutoff.isoformat(),
                    "gb_margin": ctx.margin,
                    "n_eligible_polls": ctx.n_polls,
                    "poll_ids": list(ctx.source_ids),
                    "field_dates": list(ctx.field_dates),
                    "available_at_dates": list(ctx.available_at_dates),
                    "source_hash": ctx.source_version_hash,
                    "aggregation_method": ctx.weighting_method,
                    "aggregation_config_id": ctx.aggregation_config_id,
                    "formal_oof_eligible": ctx.formal_oof_eligible,
                    "fold_identity": fold_id,
                    "context": ctx.to_dict(),
                }
            )
    n_ok = sum(1 for r in rows if r["formal_oof_eligible"])
    missing = [
        f"{r['election_id']}-T{r['lead_days']}"
        for r in rows
        if not r["formal_oof_eligible"]
    ]
    payload = {
        "schema_version": "generic-ballot-parity-v0925",
        "model_contract": GB_CONTEXT_SCHEMA,
        "formal_cycles": list(FORMAL_OOF_CYCLES),
        "formal_leads": list(FORMAL_OOF_LEADS),
        "n_cutoffs": len(rows),
        "n_formal_eligible": n_ok,
        "missing_formal_folds": missing,
        "historical_archive_path": _repo_rel(HISTORICAL_GB_STORE)
        if HISTORICAL_GB_STORE.is_file()
        else None,
        "historical_archive_hash": archive_hash or None,
        "aggregation_config_id": AGGREGATION_CONFIG_ID,
        "senate_poll_residual_forbidden": True,
        "status": "ok" if n_ok == len(rows) else "source_gap",
        "rows": rows,
        "note": (
            "Formal OOF must use reconstructable national generic-ballot evidence. "
            "Deriving GB from Senate poll residuals is forbidden. "
            "Election Day uses federal_election_day (Tuesday after first Monday)."
        ),
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / PARITY_ARTIFACT_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = _repo_rel(path)
    return payload
