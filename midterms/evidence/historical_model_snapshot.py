"""Prepare historical (and current) model-ready snapshots with structural features.

``Warehouse.build_as_of`` returns point-in-time certified evidence. Formal OOF and
publication forecasting must then attach *derived* structural features — race-id
candidate finance and seat-specific personal incumbency — through this module so
every model component that consumes ``snap.races`` sees the same values that the
cheap historical audits report.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR
from midterms.evidence.fec import (
    REQUIRED_FINANCE_CUTOFFS,
    attach_fundraising_to_races,
    current_registry_tickets,
    historical_nominee_tickets,
    report_level_fundraising_shares_as_of,
)
from midterms.evidence.historical_personal_incumbency import (
    _ledger_nominee_sides,
    resolve_historical_personal_incumbency,
)
from midterms.evidence.warehouse import EvidenceSnapshot, Warehouse
from midterms.model.personal_incumbency import personal_incumbency_signed

STRUCTURAL_FEATURE_SCHEMA = "historical-structural-features-v1"
FORMAL_YEARS = (2018, 2020, 2022, 2024)
FORMAL_LEADS = (60, 30)


def _as_of_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _canonical_sha256(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cutoff_label_for(*, election_id: str, lead_days: int) -> str:
    return f"{election_id}-lead-{int(lead_days)}"


def load_formal_finance_shares(
    *,
    election_id: str,
    as_of: str | date,
    require_candidate_match: bool = True,
) -> pd.DataFrame:
    """Compute race-id candidate-specific finance shares as of a cutoff."""
    year = int(str(election_id).replace("senate-", ""))
    links_path = NORMALIZED_DIR / "fec_candidate_committees.parquet"
    reports_path = NORMALIZED_DIR / "fec_form3_reports.parquet"
    if not links_path.is_file() or not reports_path.is_file():
        return pd.DataFrame()
    links = pd.read_parquet(links_path)
    reports = pd.read_parquet(reports_path)
    if str(election_id) == "senate-2026":
        tickets = current_registry_tickets()
    else:
        tickets = historical_nominee_tickets(election_id)
    return report_level_fundraising_shares_as_of(
        reports[reports["cycle"].eq(year)],
        links[links["cycle"].eq(year)],
        election_id=election_id,
        as_of=as_of,
        tickets=tickets,
        require_candidate_match=require_candidate_match,
    )


def attach_race_specific_finance(
    races: pd.DataFrame,
    *,
    election_id: str,
    as_of: str | date,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Attach race-id finance without state fallback. Returns (frame, by_race meta)."""
    out = races.copy()
    shares = load_formal_finance_shares(election_id=election_id, as_of=as_of)
    by_race: dict[str, dict[str, Any]] = {}
    if len(shares):
        for _, row in shares.iterrows():
            by_race[str(row["race_id"])] = {
                "fundraising_share": float(row["fundraising_share"]),
                "match_status": str(row.get("match_status") or ""),
                "dem_fec_candidate_id": row.get("dem_fec_candidate_id"),
                "rep_fec_candidate_id": row.get("rep_fec_candidate_id"),
                "modeled_candidate_name": row.get("modeled_candidate_name"),
                "opposing_candidate_name": row.get("opposing_candidate_name"),
            }
    for col in (
        "finance_match_status",
        "dem_fec_candidate_id",
        "rep_fec_candidate_id",
    ):
        if col not in out.columns:
            out[col] = None
    if "fundraising_share" not in out.columns:
        out["fundraising_share"] = 0.5

    shares_vals: list[float] = []
    match_vals: list[str] = []
    dem_ids: list[Any] = []
    rep_ids: list[Any] = []
    for _, race in out.iterrows():
        race_id = str(race["race_id"])
        meta = by_race.get(race_id)
        if meta is None:
            shares_vals.append(0.5)
            match_vals.append("missing_race_specific_finance_neutral")
            dem_ids.append(None)
            rep_ids.append(None)
        else:
            shares_vals.append(float(meta["fundraising_share"]))
            match_vals.append(str(meta["match_status"]))
            dem_ids.append(meta.get("dem_fec_candidate_id"))
            rep_ids.append(meta.get("rep_fec_candidate_id"))
    out["fundraising_share"] = shares_vals
    out["finance_match_status"] = match_vals
    out["dem_fec_candidate_id"] = dem_ids
    out["rep_fec_candidate_id"] = rep_ids
    return out, by_race


def attach_historical_personal_incumbency(
    races: pd.DataFrame,
    *,
    election_id: str,
    as_of: str | date,
    lead_days: int,
) -> pd.DataFrame:
    """Populate personal-incumbency identity columns for historical folds."""
    out = races.copy()
    label = cutoff_label_for(election_id=election_id, lead_days=lead_days)
    tickets = historical_nominee_tickets(election_id)
    sides = _ledger_nominee_sides(election_id)
    as_of_s = _as_of_date(as_of).isoformat()

    for col, default in (
        ("seat_identity", None),
        ("sitting_senator_name", None),
        ("modeled_candidate_name", None),
        ("opposing_candidate_name", None),
        ("modeled_candidate_is_incumbent", False),
        ("opposing_candidate_is_incumbent", False),
        ("personal_incumbency_identity_status", None),
    ):
        if col not in out.columns:
            out[col] = default

    for idx, race in out.iterrows():
        if bool(race.get("not_up")):
            continue
        race_id = str(race["race_id"])
        ticket = tickets.get(race_id) or {}
        side = sides.get(race_id) or {}
        dem = (
            ticket.get("dem_name")
            or ticket.get("modeled_candidate_name")
            or side.get("dem_name")
        )
        rep = (
            ticket.get("rep_name")
            or ticket.get("opposing_candidate_name")
            or side.get("rep_name")
        )
        resolved = resolve_historical_personal_incumbency(
            race_id=race_id,
            as_of=as_of_s,
            cutoff_label=label,
            modeled_candidate_name=None if dem is None else str(dem),
            opposing_candidate_name=None if rep is None else str(rep),
            state=str(race.get("state") or side.get("state") or ""),
            seat_class=str(race.get("seat_class") or side.get("seat_class") or "") or None,
        )
        out.at[idx, "seat_identity"] = resolved.get("seat_identity")
        out.at[idx, "sitting_senator_name"] = resolved.get("sitting_senator_name")
        out.at[idx, "modeled_candidate_name"] = dem
        out.at[idx, "opposing_candidate_name"] = rep
        out.at[idx, "modeled_candidate_is_incumbent"] = bool(
            resolved["modeled_candidate_is_incumbent"]
        )
        out.at[idx, "opposing_candidate_is_incumbent"] = bool(
            resolved["opposing_candidate_is_incumbent"]
        )
        out.at[idx, "personal_incumbency_identity_status"] = resolved.get(
            "identity_status"
        )
    return out


def structural_feature_records(races: pd.DataFrame) -> list[dict[str, Any]]:
    """Deterministic per-race structural feature rows for active contests."""
    active = races[~races.get("not_up", pd.Series(False, index=races.index)).fillna(False)]
    records: list[dict[str, Any]] = []
    for _, race in active.sort_values("race_id").iterrows():
        personal = float(personal_incumbency_signed(race))
        records.append({
            "race_id": str(race["race_id"]),
            "fundraising_share": float(
                race["fundraising_share"] if pd.notna(race.get("fundraising_share")) else 0.5
            ),
            "finance_match_status": str(
                race.get("finance_match_status") or "unspecified"
            ),
            "dem_fec_candidate_id": (
                None if pd.isna(race.get("dem_fec_candidate_id"))
                else str(race.get("dem_fec_candidate_id"))
            ),
            "rep_fec_candidate_id": (
                None if pd.isna(race.get("rep_fec_candidate_id"))
                else str(race.get("rep_fec_candidate_id"))
            ),
            "personal_incumbency": personal,
            "modeled_candidate_is_incumbent": bool(
                race.get("modeled_candidate_is_incumbent")
            ),
            "opposing_candidate_is_incumbent": bool(
                race.get("opposing_candidate_is_incumbent")
            ),
            "seat_identity": (
                None if pd.isna(race.get("seat_identity"))
                else str(race.get("seat_identity"))
            ),
            "identity_status": (
                None if pd.isna(race.get("personal_incumbency_identity_status"))
                else str(race.get("personal_incumbency_identity_status"))
            ),
        })
    return records


def historical_structural_feature_sha256(records: list[dict[str, Any]]) -> str:
    """Hash the exact structural features consumed by model fitting."""
    payload = {
        "schema_version": STRUCTURAL_FEATURE_SCHEMA,
        "records": sorted(records, key=lambda r: str(r["race_id"])),
    }
    return _canonical_sha256(payload)


def prepare_historical_model_snapshot(
    snap: EvidenceSnapshot,
    *,
    election_id: str | None = None,
    as_of: str | date | None = None,
    lead_days: int | None = None,
) -> EvidenceSnapshot:
    """Return a model-ready copy of ``snap`` with derived structural features.

    The input snapshot is not mutated. ``snapshot_id`` remains the certified
    evidence fingerprint from ``Warehouse.build_as_of``.
    """
    election_id = str(election_id or snap.election_id)
    as_of_val = _as_of_date(as_of or snap.as_of)
    if lead_days is None:
        # Infer lead from election_day when possible.
        contested = snap.races[~snap.races["not_up"].fillna(False)]
        if len(contested):
            ed = date.fromisoformat(str(contested["election_day"].iloc[0])[:10])
            lead_days = (ed - as_of_val).days
        else:
            lead_days = 0

    races = snap.races.copy()
    if election_id == "senate-2026":
        races, _ = attach_race_specific_finance(
            races, election_id=election_id, as_of=as_of_val,
        )
        # 2026 personal incumbency already arrives via registry attach in
        # build_as_of; ensure bool columns exist for fundamentals.
        for col in ("modeled_candidate_is_incumbent", "opposing_candidate_is_incumbent"):
            if col not in races.columns:
                races[col] = False
            else:
                races[col] = races[col].map(
                    lambda value: False if value is None or (isinstance(value, float) and pd.isna(value)) else bool(value)
                )
        if "finance_match_status" not in races.columns:
            races["finance_match_status"] = "unspecified"
    else:
        races, _ = attach_race_specific_finance(
            races, election_id=election_id, as_of=as_of_val,
        )
        races = attach_historical_personal_incumbency(
            races,
            election_id=election_id,
            as_of=as_of_val,
            lead_days=int(lead_days),
        )

    records = structural_feature_records(races)
    feature_sha = historical_structural_feature_sha256(records)
    component_hashes = dict(snap.component_hashes or {})
    component_hashes["historical_structural_feature_sha256"] = feature_sha
    component_hashes["historical_structural_feature_schema"] = STRUCTURAL_FEATURE_SCHEMA

    prepared = replace(
        snap,
        races=races,
        component_hashes=component_hashes,
        historical_structural_feature_sha256=feature_sha,
    )
    return prepared


def prepare_formal_fold_snapshot(
    warehouse: Warehouse,
    *,
    year: int,
    lead_days: int,
) -> EvidenceSnapshot:
    """Build certified evidence then attach derived structural features."""
    election_id = f"senate-{year}"
    as_of = REQUIRED_FINANCE_CUTOFFS[int(year)][0 if int(lead_days) == 60 else 1]
    raw = warehouse.build_as_of(as_of, election_id)
    return prepare_historical_model_snapshot(
        raw,
        election_id=election_id,
        as_of=as_of,
        lead_days=int(lead_days),
    )


def summarize_structural_features(races: pd.DataFrame) -> dict[str, Any]:
    records = structural_feature_records(races)
    n_matched = sum(1 for r in records if r["finance_match_status"] == "candidate_matched")
    n_neutral = len(records) - n_matched
    n_inc_nonzero = sum(1 for r in records if abs(r["personal_incumbency"]) > 1e-12)
    n_unresolved = sum(
        1
        for r in records
        if str(r.get("identity_status") or "") in {
            "incumbency_identity_unresolved",
            "nomination_not_knowable_at_cutoff",
        }
    )
    n_open = sum(
        1
        for r in records
        if abs(r["personal_incumbency"]) <= 1e-12
        and str(r.get("identity_status") or "") not in {
            "incumbency_identity_unresolved",
            "nomination_not_knowable_at_cutoff",
        }
    )
    return {
        "n_active_races": len(records),
        "n_finance_candidate_matched": n_matched,
        "n_finance_neutral_unmatched": n_neutral,
        "n_personal_incumbency_nonzero": n_inc_nonzero,
        "n_open_or_non_incumbent": n_open,
        "n_unresolved_personal_incumbency": n_unresolved,
        "historical_structural_feature_sha256": historical_structural_feature_sha256(records),
        "races": records,
    }


def _audit_finance_by_fold() -> dict[str, dict[str, dict[str, Any]]]:
    path = ARTIFACTS_DIR / "historical_finance_input_diff_v0923.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing finance audit artifact: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for block in payload.get("formal_cutoff_race_details") or []:
        label = str(block["cutoff_label"])
        out[label] = {
            str(r["race_id"]): r for r in (block.get("races") or [])
        }
    return out


def _audit_incumbency_by_fold() -> dict[str, dict[str, dict[str, Any]]]:
    path = ARTIFACTS_DIR / "historical_personal_incumbency_v0923.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing incumbency audit artifact: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in payload.get("records") or []:
        label = str(row["cutoff_label"])
        out.setdefault(label, {})[str(row["race_id"])] = row
    return out


def verify_audit_model_parity(
    *,
    fold_summaries: list[dict[str, Any]],
    atol: float = 1e-9,
) -> list[dict[str, Any]]:
    """Fail closed when model-ready features disagree with sealed audits."""
    finance = _audit_finance_by_fold()
    incumbency = _audit_incumbency_by_fold()
    mismatches: list[dict[str, Any]] = []
    for fold in fold_summaries:
        label = str(fold["cutoff_label"])
        fin_map = finance.get(label) or {}
        inc_map = incumbency.get(label) or {}
        for race in fold.get("races") or []:
            race_id = str(race["race_id"])
            fin = fin_map.get(race_id)
            if fin is None:
                mismatches.append({
                    "cutoff_label": label,
                    "race_id": race_id,
                    "field": "finance",
                    "error": "missing_from_finance_audit",
                })
            else:
                expected = float(fin["new_fundraising_share"])
                actual = float(race["fundraising_share"])
                if abs(expected - actual) > atol:
                    mismatches.append({
                        "cutoff_label": label,
                        "race_id": race_id,
                        "field": "fundraising_share",
                        "audit": expected,
                        "model": actual,
                    })
                audit_status = str(fin.get("match_status") or "")
                model_status = str(race.get("finance_match_status") or "")
                status_differs = bool(audit_status and model_status and audit_status != model_status)
                both_neutral_half = (
                    "neutral" in audit_status
                    and "neutral" in model_status
                    and abs(actual - 0.5) <= atol
                )
                if status_differs and not both_neutral_half:
                    mismatches.append({
                        "cutoff_label": label,
                        "race_id": race_id,
                        "field": "finance_match_status",
                        "audit": audit_status,
                        "model": model_status,
                    })
            inc = inc_map.get(race_id)
            if inc is None:
                mismatches.append({
                    "cutoff_label": label,
                    "race_id": race_id,
                    "field": "incumbency",
                    "error": "missing_from_incumbency_audit",
                })
            else:
                expected_p = float(inc["personal_incumbency_feature"])
                actual_p = float(race["personal_incumbency"])
                if abs(expected_p - actual_p) > atol:
                    mismatches.append({
                        "cutoff_label": label,
                        "race_id": race_id,
                        "field": "personal_incumbency",
                        "audit": expected_p,
                        "model": actual_p,
                    })
                for flag in (
                    "modeled_candidate_is_incumbent",
                    "opposing_candidate_is_incumbent",
                ):
                    if bool(inc.get(flag)) != bool(race.get(flag)):
                        mismatches.append({
                            "cutoff_label": label,
                            "race_id": race_id,
                            "field": flag,
                            "audit": bool(inc.get(flag)),
                            "model": bool(race.get(flag)),
                        })
    return mismatches


def write_historical_model_feature_parity(
    *,
    warehouse: Warehouse | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Prepare all eight formal folds (no inference) and verify audit parity."""
    wh = warehouse or Warehouse(ensure_fixtures=False)
    folds: list[dict[str, Any]] = []
    fold_hashes: list[dict[str, str]] = []
    for year in FORMAL_YEARS:
        for lead in FORMAL_LEADS:
            prepared = prepare_formal_fold_snapshot(wh, year=year, lead_days=lead)
            summary = summarize_structural_features(prepared.races)
            label = cutoff_label_for(election_id=f"senate-{year}", lead_days=lead)
            fold = {
                "cutoff_label": label,
                "year": year,
                "lead_days": lead,
                "as_of": prepared.as_of.isoformat(),
                "evidence_snapshot_id": prepared.snapshot_id,
                **summary,
            }
            folds.append(fold)
            fold_hashes.append({
                "cutoff_label": label,
                "historical_structural_feature_sha256": summary[
                    "historical_structural_feature_sha256"
                ],
            })

    mismatches = verify_audit_model_parity(fold_summaries=folds)
    aggregate_sha = _canonical_sha256({
        "schema_version": STRUCTURAL_FEATURE_SCHEMA,
        "folds": fold_hashes,
    })
    payload = {
        "schema_version": "historical-model-feature-parity-v0923",
        "structural_feature_schema": STRUCTURAL_FEATURE_SCHEMA,
        "model_version": MODEL_VERSION,
        "status": (
            "parity_ok" if not mismatches else "parity_failed"
        ),
        "decision_gate": (
            "HISTORICAL MODEL INPUTS CHANGED — FULL ORDINARY OOF REVALIDATION REQUIRED"
        ),
        "n_folds": len(folds),
        "aggregate_historical_structural_features_sha256": aggregate_sha,
        "folds": [
            {k: v for k, v in fold.items() if k != "races"} for fold in folds
        ],
        "fold_race_details": folds,
        "parity_mismatches": mismatches,
        "n_parity_mismatches": len(mismatches),
        "note": (
            "Cheap dry-run only — no PyMC OOF, stack, calibration, or forecast "
            "rebuild was executed. Model-ready structural features must match "
            "historical finance and incumbency audits."
        ),
    }
    path = out_path or (ARTIFACTS_DIR / "historical_model_feature_parity_v0923.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if mismatches:
        raise ValueError(
            f"historical model feature parity failed with {len(mismatches)} mismatches"
        )
    return payload


def prepare_current_2026_sanity(warehouse: Warehouse | None = None) -> dict[str, Any]:
    """Cheap current-cycle structural check (no forecast)."""
    from midterms.evidence.current_candidates import load_current_candidate_registry

    wh = warehouse or Warehouse(ensure_fixtures=False)
    as_of = "2026-10-05"
    raw = wh.build_as_of(as_of, "senate-2026")
    prepared = prepare_historical_model_snapshot(
        raw, election_id="senate-2026", as_of=as_of, lead_days=29,
    )
    registry = load_current_candidate_registry()
    by_state = {str(r["state"]): r for r in registry["races"]}
    active = prepared.races[~prepared.races["not_up"].fillna(False)].copy()
    checks: dict[str, Any] = {}
    for st in ("TX", "LA", "NE", "NM", "AK", "FL", "OH"):
        row = active[active["state"].astype(str).eq(st)]
        if not len(row):
            checks[st] = {"error": "missing"}
            continue
        r = row.iloc[0]
        personal = float(personal_incumbency_signed(r))
        checks[st] = {
            "race_id": str(r["race_id"]),
            "personal_incumbency": personal,
            "modeled_candidate_is_incumbent": bool(r.get("modeled_candidate_is_incumbent")),
            "opposing_candidate_is_incumbent": bool(r.get("opposing_candidate_is_incumbent")),
            "sitting_senator_name": r.get("sitting_senator_name") or by_state.get(st, {}).get(
                "sitting_senator_name"
            ),
            "seat_identity": r.get("seat_identity") or by_state.get(st, {}).get("seat_identity"),
            "fundraising_share": float(r.get("fundraising_share") or 0.5),
            "finance_match_status": r.get("finance_match_status"),
        }
    return {
        "schema_version": "current-2026-structural-sanity-v0923",
        "as_of": as_of,
        "evidence_snapshot_id": prepared.snapshot_id,
        "historical_structural_feature_sha256": getattr(
            prepared, "historical_structural_feature_sha256", None
        ),
        "checks": checks,
    }


# Back-compat alias used by publication path callers if desired.
def attach_publication_finance_no_state_fallback(
    races: pd.DataFrame, *, as_of: str | date,
) -> pd.DataFrame:
    return attach_fundraising_to_races(races, as_of=as_of, allow_state_fallback=False)
