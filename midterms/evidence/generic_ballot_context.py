"""Unified generic-ballot context for live forecasting and historical OOF.

Historical formal OOF must NOT derive a national generic ballot from the Senate
polls being scored. Live forecasting uses VoteHub national generic-ballot polls.
When a reconstructable historical series is unavailable, formal OOF fails closed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, RAW_DIR, ROOT

GB_CONTEXT_SCHEMA = "generic-ballot-context-v1"
FORMAL_OOF_CYCLES = (2018, 2020, 2022, 2024)
FORMAL_OOF_LEADS = (60, 30)
HISTORICAL_GB_STORE = RAW_DIR / "external" / "historical_generic_ballot_archive.json"


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _election_day(election_id: str) -> date:
    year = int(str(election_id).split("-")[-1])
    # Senate Class II / general midterms / presidential: first Tuesday after Nov 1.
    from datetime import timedelta

    nov1 = date(year, 11, 1)
    # weekday: Mon=0 ... Sun=6; Tuesday=1
    delta = (1 - nov1.weekday()) % 7
    return nov1 + timedelta(days=delta)


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
                weighting_method="trailing_weighted_headline",
                source_version_hash="",
                historically_reconstructable=True,
                fallback_used=True,
                fallback_reason="votehub_generic_ballot_unavailable_at_cutoff",
                formal_oof_eligible=False,
                path="unavailable",
            )
        gb_path = path or (RAW_DIR / "external" / "votehub_generic_ballot_2026.json")
        blob = gb_path.read_bytes() if gb_path.is_file() else b""
        return GenericBallotContext(
            schema_version=GB_CONTEXT_SCHEMA,
            election_id=election_id,
            as_of=as_of_s,
            margin=float(agg["margin"]),
            n_polls=int(agg["n_polls"]),
            source_ids=("votehub_generic_ballot_2026",),
            field_dates=(str(agg.get("ref_date") or as_of_s),),
            available_at_dates=(as_of_s,),
            weighting_method=str(agg.get("method") or "trailing_weighted_headline"),
            source_version_hash=_sha256_text(blob.decode("utf-8", errors="replace")[:200000]),
            historically_reconstructable=True,
            fallback_used=False,
            fallback_reason=None,
            formal_oof_eligible=True,
            path="votehub_live",
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
        )

    payload = json.loads(archive.read_text(encoding="utf-8"))
    rows = list(payload.get("rows") or [])
    eligible = []
    for row in rows:
        avail = str(row.get("available_at") or row.get("published_at") or row.get("field_end") or "")[:10]
        if not avail:
            continue
        if require_point_in_time and date.fromisoformat(avail) > cutoff:
            continue
        row_eid = str(row.get("election_id") or "")
        if row_eid and row_eid != election_id and str(row.get("election_year")) != str(year):
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
            source_version_hash=_sha256_text(archive.read_text(encoding="utf-8")[:200000]),
            historically_reconstructable=True,
            fallback_used=True,
            fallback_reason="no_historical_gb_polls_on_or_before_as_of",
            formal_oof_eligible=False,
            path="unavailable",
        )
    margins = [float(r["margin"]) for r in eligible if r.get("margin") is not None]
    margin = float(sum(margins) / len(margins)) if margins else None
    return GenericBallotContext(
        schema_version=GB_CONTEXT_SCHEMA,
        election_id=election_id,
        as_of=as_of_s,
        margin=margin,
        n_polls=len(eligible),
        source_ids=tuple(str(r.get("poll_id") or r.get("source_id") or "") for r in eligible[:50]),
        field_dates=tuple(str(r.get("field_end") or "")[:10] for r in eligible[:50]),
        available_at_dates=tuple(
            str(r.get("available_at") or r.get("published_at") or "")[:10] for r in eligible[:50]
        ),
        weighting_method=str(payload.get("weighting_method") or "historical_archive_equal_weight"),
        source_version_hash=_sha256_text(archive.read_text(encoding="utf-8")[:200000]),
        historically_reconstructable=True,
        fallback_used=False,
        fallback_reason=None,
        formal_oof_eligible=margin is not None,
        path="historical_archive",
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
    for year in FORMAL_OOF_CYCLES:
        ed = _election_day(f"senate-{year}")
        for lead in FORMAL_OOF_LEADS:
            as_of = (ed.toordinal() - lead)
            from datetime import date as _date

            cutoff = _date.fromordinal(as_of)
            ctx = resolve_generic_ballot_context(f"senate-{year}", cutoff)
            rows.append(
                {
                    "election_id": f"senate-{year}",
                    "lead_days": lead,
                    "election_day": ed.isoformat(),
                    "as_of": cutoff.isoformat(),
                    "context": ctx.to_dict(),
                }
            )
    n_ok = sum(1 for r in rows if r["context"]["formal_oof_eligible"])
    payload = {
        "schema_version": "generic-ballot-parity-v0925",
        "model_contract": GB_CONTEXT_SCHEMA,
        "formal_cycles": list(FORMAL_OOF_CYCLES),
        "formal_leads": list(FORMAL_OOF_LEADS),
        "n_cutoffs": len(rows),
        "n_formal_eligible": n_ok,
        "historical_archive_path": str(HISTORICAL_GB_STORE.relative_to(ROOT))
        if HISTORICAL_GB_STORE.is_file()
        else None,
        "senate_poll_residual_forbidden": True,
        "status": "ok" if n_ok == len(rows) else "source_gap",
        "rows": rows,
        "note": (
            "Formal OOF must use reconstructable national generic-ballot evidence. "
            "Deriving GB from Senate poll residuals is forbidden."
        ),
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "generic_ballot_parity_v0924.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
