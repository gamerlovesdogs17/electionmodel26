"""Cheap audits for the v0.9.23 probability-integrity repair (no PyMC / OOF)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.evidence.fec import (
    REQUIRED_FINANCE_CUTOFFS,
    current_registry_tickets,
    historical_nominee_tickets,
    report_level_fundraising_shares_as_of,
)
from midterms.evidence.historical_personal_incumbency import (
    audit_historical_personal_incumbency,
)
from midterms.evidence.senate_seat_incumbents import resolve_2026_personal_incumbency
from midterms.evidence.warehouse import Warehouse
from midterms.model.fundamentals import feature_row
from midterms.model.personal_incumbency import personal_incumbency_signed

ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def audit_2026_structural_metadata() -> dict[str, Any]:
    """Flag seat-party vs personal-incumbency inconsistencies across 2026 races."""
    registry = load_current_candidate_registry()
    wh = Warehouse(ensure_fixtures=False)
    races = wh.races[wh.races["election_id"].astype(str).eq("senate-2026")].copy()
    races = races[~races["not_up"].fillna(False).astype(bool)].copy()
    by_state = {str(r["state"]): r for r in registry["races"]}
    records: list[dict[str, Any]] = []
    for _, race in races.iterrows():
        st = str(race["state"])
        reg = by_state.get(st) or {}
        row = {**race.to_dict(), **reg}
        personal = personal_incumbency_signed(row)
        old_party = 0.0
        if not bool(race.get("is_open")):
            inc = str(race.get("incumbent_party") or "")
            if inc in {"D", "I"}:
                old_party = 1.0
            elif inc == "R":
                old_party = -1.0
        flags: list[str] = []
        if bool(race.get("held_by")) and personal == 0.0 and not bool(race.get("is_open")):
            if not reg.get("modeled_candidate_is_incumbent") and not reg.get(
                "opposing_candidate_is_incumbent"
            ):
                flags.append("party_held_seat_but_nominee_is_not_incumbent")
        if (
            race.get("is_open") is False
            and not reg.get("modeled_candidate_is_incumbent")
            and not reg.get("opposing_candidate_is_incumbent")
        ):
            flags.append("is_open_false_without_personal_incumbent_on_ticket")
        if old_party != 0.0 and personal == 0.0:
            flags.append("party_incumbency_would_have_differed_from_personal")
        if st == "TX" and personal != 0.0:
            flags.append("tx_paxton_must_not_receive_cornyn_bonus")
        if st == "LA" and personal != 0.0:
            flags.append("la_letlow_must_not_receive_cassidy_bonus")
        if st == "NE" and not reg.get("opposing_candidate_is_incumbent"):
            flags.append("ne_ricketts_must_be_personal_incumbent")
        if st == "NM" and not reg.get("modeled_candidate_is_incumbent"):
            flags.append("nm_lujan_must_be_personal_incumbent")
        ordinary = reg.get("ordinary_binary_target_supported")
        exceptional = reg.get("exceptional_probability_model_supported")
        support = str(reg.get("probability_model_support_status") or "")
        if ordinary is False and exceptional is True and support == "limited_supported":
            # Expected for non-major / RCV adapters — not a contradiction.
            pass
        elif (
            reg.get("statistical_target_supported") is False
            and support.startswith("limited_supported")
            and exceptional is not True
        ):
            flags.append("registry_statistical_target_false_but_exception_adapter_supported")
        if bool(race.get("vacancy_reason")) and personal != 0.0:
            flags.append("appointed_or_special_seat_with_personal_incumbency")
        records.append({
            "race_id": str(race["race_id"]),
            "state": st,
            "seat_class": race.get("seat_class"),
            "seat_identity": reg.get("seat_identity"),
            "held_by": race.get("held_by"),
            "incumbent_party": race.get("incumbent_party"),
            "is_open": bool(race.get("is_open")),
            "vacancy_reason": race.get("vacancy_reason"),
            "sitting_senator_name": reg.get("sitting_senator_name"),
            "modeled_candidate_name": reg.get("modeled_candidate_name"),
            "opposing_candidate_name": reg.get("opposing_candidate_name"),
            "modeled_candidate_is_incumbent": reg.get("modeled_candidate_is_incumbent"),
            "opposing_candidate_is_incumbent": reg.get("opposing_candidate_is_incumbent"),
            "legacy_party_incumbency_feature": old_party,
            "personal_incumbency_feature": personal,
            "incumbency_feature_changed": old_party != personal,
            "ordinary_binary_target_supported": reg.get("ordinary_binary_target_supported"),
            "exceptional_probability_model_supported": reg.get(
                "exceptional_probability_model_supported"
            ),
            "statistical_target_supported": reg.get("statistical_target_supported"),
            "probability_model_support_status": reg.get("probability_model_support_status"),
            "personal_incumbency_identity_status": reg.get(
                "personal_incumbency_identity_status"
            ),
            "provenance": "seat_officeholders_2026_v1",
            "flags": flags,
            "status": "flagged" if flags else "ok",
        })
    records.sort(key=lambda r: r["race_id"])
    payload = {
        "schema_version": "structural-metadata-consistency-v0923.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "n_races": len(records),
        "n_flagged": sum(1 for r in records if r["status"] == "flagged"),
        "n_incumbency_feature_changed": sum(
            1 for r in records if r["incumbency_feature_changed"]
        ),
        "texas": next(r for r in records if r["state"] == "TX"),
        "nebraska": next(r for r in records if r["state"] == "NE"),
        "new_mexico": next(r for r in records if r["state"] == "NM"),
        "louisiana": next(r for r in records if r["state"] == "LA"),
        "races": records,
    }
    path = ARTIFACTS_DIR / "structural_metadata_consistency_v0923.json"
    payload["path"] = str(path)
    _write(path, payload)
    return payload


def _classify_finance_unmatched(
    *,
    dem_name: Any,
    rep_name: Any,
    dem_id: Any,
    rep_id: Any,
    match_status: str,
) -> str:
    if match_status == "candidate_matched":
        return "matched"
    if dem_name is None or rep_name is None or (
        isinstance(dem_name, float) and pd.isna(dem_name)
    ) or (isinstance(rep_name, float) and pd.isna(rep_name)):
        return "one_major_party_nominee_absent"
    if dem_id is None and rep_id is None:
        return "fec_candidate_absent_or_name_normalization_failure"
    if dem_id is None or rep_id is None:
        return "partial_fec_match_one_side_missing"
    return "ambiguous_or_other"


def audit_historical_finance_input_diff() -> dict[str, Any]:
    """Compare finance inputs only for actual EvidenceSnapshot race IDs."""
    links = pd.read_parquet(NORMALIZED_DIR / "fec_candidate_committees.parquet")
    reports = pd.read_parquet(NORMALIZED_DIR / "fec_form3_reports.parquet")
    old_shares = pd.read_parquet(NORMALIZED_DIR / "fundraising_shares.parquet")
    wh = Warehouse(ensure_fixtures=False)
    diffs: list[dict[str, Any]] = []
    cutoff_summaries: list[dict[str, Any]] = []
    new_frames: list[pd.DataFrame] = []
    dual_race_states: list[dict[str, Any]] = []

    for year, cutoffs in REQUIRED_FINANCE_CUTOFFS.items():
        election_id = f"senate-{year}"
        tickets = historical_nominee_tickets(election_id)
        for idx, cutoff in enumerate(cutoffs):
            lead = 60 if idx == 0 else 30
            label = f"{election_id}-lead-{lead}"
            snap = wh.build_as_of(cutoff, election_id)
            active = snap.races[~snap.races["not_up"].fillna(False).astype(bool)].copy()
            active_ids = sorted(active["race_id"].astype(str).unique())
            # Dual-seat detection for this cycle (once per year on first cutoff).
            if idx == 0:
                by_state: dict[str, list[str]] = {}
                for rid in active_ids:
                    st = rid.split("-")[2] if "-" in rid else ""
                    by_state.setdefault(st, []).append(rid)
                for st, rids in sorted(by_state.items()):
                    if len(rids) > 1:
                        dual_race_states.append({
                            "year": year,
                            "state": st,
                            "race_ids": sorted(rids),
                        })

            candidate = report_level_fundraising_shares_as_of(
                reports[reports["cycle"].eq(int(year))],
                links[links["cycle"].eq(int(year))],
                election_id=election_id,
                as_of=cutoff,
                tickets=tickets,
                require_candidate_match=True,
            )
            if len(candidate):
                candidate = candidate[candidate["race_id"].astype(str).isin(active_ids)].copy()
            candidate["feature_as_of"] = cutoff
            candidate["cutoff_label"] = label
            new_frames.append(candidate)

            sealed = old_shares[old_shares["cutoff_label"].astype(str).eq(label)].copy()
            sealed_by_race = {
                str(r["race_id"]): r
                for _, r in sealed.iterrows()
            } if len(sealed) and "race_id" in sealed.columns else {}
            sealed_by_state = {
                str(r["state"]): r
                for _, r in sealed.iterrows()
            } if len(sealed) else {}

            changed = 0
            n_matched = 0
            n_neutral = 0
            n_ambiguous = 0
            race_rows: list[dict[str, Any]] = []
            by_race_new = {
                str(r["race_id"]): r for _, r in candidate.iterrows()
            } if len(candidate) else {}

            for race_id in active_ids:
                state = race_id.split("-")[2] if "-" in race_id else ""
                new_row = by_race_new.get(race_id)
                ticket = tickets.get(race_id) or {}
                if new_row is None:
                    # No finance row for this active race → neutral missing policy.
                    new_val = 0.5
                    match_status = "missing_race_specific_finance_neutral"
                    dem_id = rep_id = None
                    dem_name = ticket.get("dem_name")
                    rep_name = ticket.get("rep_name")
                    classif = "race_id_seat_mapping_or_ticket_gap"
                else:
                    new_val = float(new_row["fundraising_share"])
                    match_status = str(new_row.get("match_status") or "")
                    dem_id = new_row.get("dem_fec_candidate_id")
                    rep_id = new_row.get("rep_fec_candidate_id")
                    dem_name = new_row.get("modeled_candidate_name")
                    rep_name = new_row.get("opposing_candidate_name")
                    classif = _classify_finance_unmatched(
                        dem_name=dem_name,
                        rep_name=rep_name,
                        dem_id=dem_id,
                        rep_id=rep_id,
                        match_status=match_status,
                    )

                if match_status == "candidate_matched":
                    n_matched += 1
                elif match_status in {
                    "candidate_unmatched_neutral",
                    "missing_race_specific_finance_neutral",
                }:
                    n_neutral += 1
                    if classif.startswith("ambiguous"):
                        n_ambiguous += 1
                else:
                    n_neutral += 1

                sealed_row = sealed_by_race.get(race_id)
                if sealed_row is None:
                    # Sealed store historically used state-collapsed race_ids.
                    sealed_row = sealed_by_state.get(state)
                old_val = (
                    float(sealed_row["fundraising_share"])
                    if sealed_row is not None and "fundraising_share" in sealed_row
                    else None
                )
                baseline = old_val if old_val is not None else 0.5
                is_changed = abs(baseline - new_val) > 1e-9
                if is_changed:
                    changed += 1
                    diffs.append({
                        "cutoff_label": label,
                        "year": year,
                        "lead_days": lead,
                        "state": state,
                        "race_id": race_id,
                        "old_fundraising_share": baseline,
                        "new_fundraising_share": new_val,
                        "match_status": match_status,
                        "unmatched_class": classif,
                        "dem_fec_candidate_id": dem_id,
                        "rep_fec_candidate_id": rep_id,
                        "modeled_candidate_name": dem_name,
                        "opposing_candidate_name": rep_name,
                    })
                race_rows.append({
                    "race_id": race_id,
                    "state": state,
                    "old_fundraising_share": baseline,
                    "new_fundraising_share": new_val,
                    "changed": is_changed,
                    "match_status": match_status,
                    "unmatched_class": classif,
                    "modeled_candidate_name": dem_name,
                    "opposing_candidate_name": rep_name,
                    "dem_fec_candidate_id": dem_id,
                    "rep_fec_candidate_id": rep_id,
                })

            cutoff_summaries.append({
                "cutoff_label": label,
                "as_of": cutoff,
                "n_model_races": len(active_ids),
                "n_candidate_matched": n_matched,
                "n_neutral_unmatched": n_neutral,
                "n_ambiguous": n_ambiguous,
                "n_changed": changed,
                "races": race_rows,
            })

    if new_frames:
        new_path = NORMALIZED_DIR / "fundraising_shares_candidate_specific.parquet"
        pd.concat(new_frames, ignore_index=True).to_parquet(new_path, index=False)
    else:
        new_path = None

    # Current 2026 race-specific comparison.
    current_cutoff = "2026-10-05"
    cur_tickets = current_registry_tickets()
    registry = load_current_candidate_registry()
    cur_new = report_level_fundraising_shares_as_of(
        reports[reports["cycle"].eq(2026)],
        links[links["cycle"].eq(2026)],
        election_id="senate-2026",
        as_of=current_cutoff,
        tickets=cur_tickets,
        require_candidate_match=True,
    )
    sealed_cur = old_shares[
        old_shares["cutoff_label"].astype(str).eq("senate-2026-current")
    ]
    sealed_cur_by_race = {
        str(r["race_id"]): r for _, r in sealed_cur.iterrows()
    } if len(sealed_cur) and "race_id" in sealed_cur.columns else {}
    sealed_cur_by_state = {
        str(r["state"]): r for _, r in sealed_cur.iterrows()
    } if len(sealed_cur) else {}
    current_rows: list[dict[str, Any]] = []
    current_changed: list[dict[str, Any]] = []
    n_cur_matched = n_cur_na = n_cur_neutral = 0
    by_new = {str(r["race_id"]): r for _, r in cur_new.iterrows()} if len(cur_new) else {}
    for reg in registry["races"]:
        race_id = str(reg["race_id"])
        state = str(reg["state"])
        structure = str(reg.get("contest_structure") or "")
        if structure != "binary_dem_vs_rep":
            n_cur_na += 1
            current_rows.append({
                "race_id": race_id,
                "state": state,
                "match_status": "not_applicable_to_model_path",
                "contest_structure": structure,
                "modeled_candidate_name": reg.get("modeled_candidate_name"),
                "opposing_candidate_name": reg.get("opposing_candidate_name"),
            })
            continue
        new_row = by_new.get(race_id)
        if new_row is None:
            n_cur_neutral += 1
            current_rows.append({
                "race_id": race_id,
                "state": state,
                "match_status": "missing_race_specific_finance_neutral",
                "fundraising_share": 0.5,
            })
            continue
        new_val = float(new_row["fundraising_share"])
        match_status = str(new_row.get("match_status") or "")
        if match_status == "candidate_matched":
            n_cur_matched += 1
        else:
            n_cur_neutral += 1
        sealed_row = sealed_cur_by_race.get(race_id)
        if sealed_row is None:
            sealed_row = sealed_cur_by_state.get(state)
        old_val = (
            float(sealed_row["fundraising_share"])
            if sealed_row is not None and "fundraising_share" in sealed_row
            else None
        )
        row_out = {
            "race_id": race_id,
            "state": state,
            "modeled_candidate_name": new_row.get("modeled_candidate_name"),
            "opposing_candidate_name": new_row.get("opposing_candidate_name"),
            "dem_fec_candidate_id": new_row.get("dem_fec_candidate_id"),
            "rep_fec_candidate_id": new_row.get("rep_fec_candidate_id"),
            "dem_committee_ids": new_row.get("dem_committee_ids"),
            "rep_committee_ids": new_row.get("rep_committee_ids"),
            "fundraising_share": new_val,
            "old_fundraising_share": old_val,
            "match_status": match_status,
            "available_at": new_row.get("available_at"),
            "latest_eligible_receipt_date": new_row.get("available_at"),
        }
        current_rows.append(row_out)
        if old_val is not None and abs(old_val - new_val) > 1e-9:
            current_changed.append(row_out)

    status = (
        "historically_equivalent" if not diffs else "historical_inputs_changed"
    )
    payload = {
        "schema_version": "historical-finance-input-diff-v0923",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "status": status,
        "formal_cutoffs": [
            {k: v for k, v in s.items() if k != "races"} for s in cutoff_summaries
        ],
        "formal_cutoff_race_details": cutoff_summaries,
        "n_changed_formal_inputs": len(diffs),
        "changed_formal_inputs": diffs,
        "dual_race_states": dual_race_states,
        "current_2026": {
            "n_matched": n_cur_matched,
            "n_not_applicable": n_cur_na,
            "n_neutral_unmatched": n_cur_neutral,
            "n_changed": len(current_changed),
            "changed": current_changed,
            "races": current_rows,
        },
        "candidate_specific_shares_path": str(new_path) if new_path else None,
        "oos_rebuild_required": status == "historical_inputs_changed",
        "supersedes_misleading_state_level_count": {
            "prior_reported_n_changed_formal_inputs": 258,
            "note": (
                "Prior 258 counted FEC state rows, including non-contested states "
                "and collapsing dual races. This artifact compares only active "
                "EvidenceSnapshot race_ids."
            ),
        },
        "note": (
            "Candidate-specific finance is race_id-keyed. No state fallback is used "
            "for formal/publication attach. No historical OOF was run."
        ),
    }
    path = ARTIFACTS_DIR / "historical_finance_input_diff_v0923.json"
    payload["path"] = str(path)
    _write(path, payload)
    # Keep legacy filename as a pointer so older docs resolve.
    alias = {
        "schema_version": "historical-finance-equivalence-v0923.1",
        "replaced_by": str(path),
        "n_changed_formal_inputs": len(diffs),
        "status": status,
        "note": "See historical_finance_input_diff_v0923.json for race_id-scoped audit.",
    }
    _write(ARTIFACTS_DIR / "historical_finance_equivalence_v0923.json", alias)
    return payload


def write_probability_integrity_audit() -> dict[str, Any]:
    """Document poll-weighting concerns and v0.9.24 production repairs."""
    payload = {
        "schema_version": "probability-integrity-audit-v0924",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "concerns": [
            {
                "id": "sample_size_quality_double_influence",
                "confirmed_behavior": (
                    "v0.9.24: sample size and quality no longer enter influence_weight. "
                    "They inflate observation precision once via "
                    "poll_se = 100/sqrt(n)/sqrt(qw)/sqrt(iw) in pymc_model._prepare "
                    "and obs_var = (100/sqrt(n))^2 / (qw*iw) in state_space.fit_state_space, "
                    "where iw carries absolute recency / clustering only."
                ),
                "production_change_recommended": False,
                "oos_challenger_required": True,
                "changed_in_this_repair": True,
            },
            {
                "id": "absolute_recency_race_normalization",
                "confirmed_behavior": (
                    "v0.9.24: within-race renormalization applies only to non-recency "
                    "structural factors; absolute calendar recency "
                    "exp(-ln2 * age / half_life) is multiplied back afterward."
                ),
                "production_change_recommended": False,
                "oos_challenger_required": True,
                "changed_in_this_repair": True,
            },
            {
                "id": "state_space_process_variance_per_poll",
                "confirmed_behavior": (
                    "v0.9.24: fit_state_space accumulates process variance as "
                    "(0.8 pp/√day)^2 × calendar Δt between successive polls; "
                    "same-day Δt = 0 (no fixed +0.8^2 per poll)."
                ),
                "production_change_recommended": False,
                "oos_challenger_required": True,
                "changed_in_this_repair": True,
            },
        ],
        "repairs_in_this_task": [
            "poll_weight_no_n_quality_double_count",
            "absolute_recency_preserved_across_races",
            "state_space_calendar_delta_t_process_variance",
        ],
        "prior_v0923_repairs_retained": [
            "seat_specific_personal_incumbency",
            "historical_personal_incumbency_cutoff_safe",
            "race_id_candidate_specific_finance",
            "no_publication_state_finance_fallback",
            "alaska_pairwise_directional",
            "fail_closed_nonfinite_margins",
            "support_status_semantics_clarification",
        ],
    }
    path = ARTIFACTS_DIR / "probability_integrity_audit_v0924.json"
    payload["path"] = str(path)
    _write(path, payload)
    # Pointer so older docs still resolve.
    _write(
        ARTIFACTS_DIR / "probability_integrity_audit_v0923.json",
        {
            "schema_version": "probability-integrity-audit-v0923.1",
            "replaced_by": str(path),
            "note": "See probability_integrity_audit_v0924.json for current status.",
        },
    )
    return payload


def incumbency_before_after_2026() -> dict[str, Any]:
    """Enumerate 2026 races whose incumbency feature changed under the repair."""
    registry = load_current_candidate_registry()
    wh = Warehouse(ensure_fixtures=False)
    races = wh.races[wh.races["election_id"].astype(str).eq("senate-2026")].copy()
    races = races[~races["not_up"].fillna(False).astype(bool)].copy()
    by_state = {str(r["state"]): r for r in registry["races"]}
    changed = []
    for _, race in races.iterrows():
        reg = by_state.get(str(race["state"])) or {}
        merged = {**race.to_dict(), **reg}
        new = float(feature_row(pd.Series(merged))["incumbency"])
        old = 0.0
        if not bool(race.get("is_open")):
            inc = str(race.get("incumbent_party") or "")
            if inc in {"D", "I"}:
                old = 1.0
            elif inc == "R":
                old = -1.0
        if old != new:
            changed.append({
                "race_id": str(race["race_id"]),
                "state": str(race["state"]),
                "old_incumbency": old,
                "new_incumbency": new,
                "modeled_candidate_name": reg.get("modeled_candidate_name"),
                "opposing_candidate_name": reg.get("opposing_candidate_name"),
                "sitting_senator_name": reg.get("sitting_senator_name"),
                "seat_identity": reg.get("seat_identity"),
            })
    return {
        "n_changed": len(changed),
        "changed": changed,
        "nebraska": by_state.get("NE"),
        "new_mexico": by_state.get("NM"),
        "texas": by_state.get("TX"),
        "louisiana": by_state.get("LA"),
    }


def main() -> None:
    struct = audit_2026_structural_metadata()
    finance = audit_historical_finance_input_diff()
    hist_inc = audit_historical_personal_incumbency()
    poll = write_probability_integrity_audit()
    incumbency = incumbency_before_after_2026()
    hist_inc_changed = int(hist_inc.get("n_changed_formal_inputs") or 0)
    finance_changed = int(finance.get("n_changed_formal_inputs") or 0)
    decision = (
        "HISTORICAL MODEL INPUTS CHANGED — FULL ORDINARY OOF REVALIDATION REQUIRED"
        if (hist_inc_changed > 0 or finance_changed > 0)
        else "HISTORICAL MODEL INPUTS EQUIVALENT — ORDINARY OOF REBUILD NOT REQUIRED"
    )
    summary = {
        "schema_version": "probability-integrity-repair-summary-v0923.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "structural_metadata": struct["path"],
        "finance_input_diff": finance["path"],
        "finance_status": finance["status"],
        "finance_n_changed_formal_inputs": finance_changed,
        "historical_personal_incumbency": hist_inc.get("path"),
        "historical_incumbency_n_changed": hist_inc_changed,
        "probability_integrity_audit": poll["path"],
        "incumbency_changed_2026": {
            "n_changed": incumbency["n_changed"],
            "changed_race_ids": [c["race_id"] for c in incumbency["changed"]],
        },
        "decision_gate": decision,
        "note": (
            "Supersedes any prior '258 changed formal inputs' figure. "
            "Counts are race_id-scoped against EvidenceSnapshot active races only. "
            "No PyMC OOF / stack / calibration / 50k chamber / release seal was run."
        ),
    }
    out = ARTIFACTS_DIR / "probability_integrity_repair_summary_v0923.json"
    _write(out, summary)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
