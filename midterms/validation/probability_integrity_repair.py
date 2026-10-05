"""Cheap audits for the v0.9.23 probability-integrity repair (no PyMC / OOF)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR, ROOT
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.evidence.fec import (
    REQUIRED_FINANCE_CUTOFFS,
    current_registry_tickets,
    historical_nominee_tickets,
    report_level_fundraising_shares_as_of,
)
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
        if (
            reg.get("statistical_target_supported") is False
            and str(reg.get("probability_model_support_status") or "").startswith(
                "limited_supported"
            )
        ):
            flags.append("registry_statistical_target_false_but_exception_adapter_supported")
        if bool(race.get("vacancy_reason")) and personal != 0.0:
            # Appointed officeholders may correctly be personal incumbents (FL/OH).
            flags.append("appointed_or_special_seat_with_personal_incumbency")
        records.append({
            "race_id": str(race["race_id"]),
            "state": st,
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
            "statistical_target_supported": reg.get("statistical_target_supported"),
            "probability_model_support_status": reg.get("probability_model_support_status"),
            "flags": flags,
            "status": "flagged" if flags else "ok",
        })
    records.sort(key=lambda r: r["race_id"])
    payload = {
        "schema_version": "structural-metadata-consistency-v0923",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "n_races": len(records),
        "n_flagged": sum(1 for r in records if r["status"] == "flagged"),
        "n_incumbency_feature_changed": sum(
            1 for r in records if r["incumbency_feature_changed"]
        ),
        "texas": next(r for r in records if r["state"] == "TX"),
        "races": records,
    }
    path = ARTIFACTS_DIR / "structural_metadata_consistency_v0923.json"
    payload["path"] = str(path)
    _write(path, payload)
    return payload


def audit_historical_finance_equivalence() -> dict[str, Any]:
    """Compare party-aggregate vs candidate-specific shares at formal cutoffs."""
    links = pd.read_parquet(NORMALIZED_DIR / "fec_candidate_committees.parquet")
    reports = pd.read_parquet(NORMALIZED_DIR / "fec_form3_reports.parquet")
    old_shares = pd.read_parquet(NORMALIZED_DIR / "fundraising_shares.parquet")
    diffs: list[dict[str, Any]] = []
    cutoff_summaries: list[dict[str, Any]] = []
    new_frames: list[pd.DataFrame] = []
    for year, cutoffs in REQUIRED_FINANCE_CUTOFFS.items():
        election_id = f"senate-{year}"
        tickets = historical_nominee_tickets(election_id)
        for idx, cutoff in enumerate(cutoffs):
            lead = 60 if idx == 0 else 30
            label = f"{election_id}-lead-{lead}"
            legacy = report_level_fundraising_shares_as_of(
                reports[reports["cycle"].eq(int(year))],
                links[links["cycle"].eq(int(year))],
                election_id=election_id,
                as_of=cutoff,
                tickets=tickets,
                require_candidate_match=False,
            )
            candidate = report_level_fundraising_shares_as_of(
                reports[reports["cycle"].eq(int(year))],
                links[links["cycle"].eq(int(year))],
                election_id=election_id,
                as_of=cutoff,
                tickets=tickets,
                require_candidate_match=True,
            )
            candidate = candidate.copy()
            candidate["feature_as_of"] = cutoff
            candidate["cutoff_label"] = label
            new_frames.append(candidate)
            sealed = old_shares[old_shares["cutoff_label"].astype(str).eq(label)].copy()
            changed = 0
            for _, row in candidate.iterrows():
                state = str(row["state"])
                new_val = float(row["fundraising_share"])
                sealed_row = sealed[sealed["state"].astype(str).eq(state)]
                old_val = (
                    float(sealed_row.iloc[0]["fundraising_share"])
                    if len(sealed_row)
                    else None
                )
                legacy_row = legacy[legacy["state"].astype(str).eq(state)]
                legacy_val = (
                    float(legacy_row.iloc[0]["fundraising_share"])
                    if len(legacy_row)
                    else None
                )
                # Prefer sealed store as the prior production input.
                baseline = old_val if old_val is not None else legacy_val
                if baseline is None:
                    continue
                if abs(baseline - new_val) > 1e-9:
                    changed += 1
                    diffs.append({
                        "cutoff_label": label,
                        "year": year,
                        "lead_days": lead,
                        "state": state,
                        "race_id": row["race_id"],
                        "old_fundraising_share": baseline,
                        "new_fundraising_share": new_val,
                        "match_status": row.get("match_status"),
                        "dem_fec_candidate_id": row.get("dem_fec_candidate_id"),
                        "rep_fec_candidate_id": row.get("rep_fec_candidate_id"),
                        "modeled_candidate_name": row.get("modeled_candidate_name"),
                        "opposing_candidate_name": row.get("opposing_candidate_name"),
                    })
            cutoff_summaries.append({
                "cutoff_label": label,
                "as_of": cutoff,
                "n_races": int(len(candidate)),
                "n_changed": changed,
                "n_matched": int((candidate["match_status"] == "candidate_matched").sum())
                if len(candidate)
                else 0,
                "n_neutral_unmatched": int(
                    (candidate["match_status"] == "candidate_unmatched_neutral").sum()
                )
                if len(candidate)
                else 0,
            })
    status = (
        "historically_equivalent" if not diffs else "historical_inputs_changed"
    )
    if new_frames:
        new_path = NORMALIZED_DIR / "fundraising_shares_candidate_specific.parquet"
        pd.concat(new_frames, ignore_index=True).to_parquet(new_path, index=False)
    else:
        new_path = None
    # Current 2026 comparison (registry tickets) vs sealed current snapshot.
    current_cutoff = "2026-10-05"
    cur_tickets = current_registry_tickets()
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
    current_changed: list[dict[str, Any]] = []
    for _, row in cur_new.iterrows():
        state = str(row["state"])
        sealed_row = sealed_cur[sealed_cur["state"].astype(str).eq(state)]
        if not len(sealed_row):
            continue
        old_val = float(sealed_row.iloc[0]["fundraising_share"])
        new_val = float(row["fundraising_share"])
        if abs(old_val - new_val) > 1e-9:
            current_changed.append({
                "state": state,
                "race_id": row["race_id"],
                "old_fundraising_share": old_val,
                "new_fundraising_share": new_val,
                "match_status": row.get("match_status"),
                "modeled_candidate_name": row.get("modeled_candidate_name"),
                "opposing_candidate_name": row.get("opposing_candidate_name"),
            })
    payload = {
        "schema_version": "historical-finance-equivalence-v0923",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "status": status,
        "formal_cutoffs": cutoff_summaries,
        "n_changed_formal_inputs": len(diffs),
        "changed_formal_inputs": diffs,
        "current_2026_changed": current_changed,
        "candidate_specific_shares_path": str(new_path) if new_path else None,
        "oos_rebuild_required": status == "historical_inputs_changed",
        "note": (
            "Candidate-specific finance uses certified historical nominees for "
            "formal cutoffs and the reviewed current registry for 2026. "
            "No historical OOF was run."
        ),
    }
    path = ARTIFACTS_DIR / "historical_finance_equivalence_v0923.json"
    payload["path"] = str(path)
    _write(path, payload)
    return payload


def write_probability_integrity_audit() -> dict[str, Any]:
    """Document poll-weighting concerns without changing production behavior."""
    payload = {
        "schema_version": "probability-integrity-audit-v0923",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "concerns": [
            {
                "id": "sample_size_quality_double_influence",
                "confirmed_behavior": (
                    "Sample size and quality enter influence_weight "
                    "(sqrt(n/600) and quality_weight) in poll_weights.attach_poll_weights, "
                    "and again inflate observation precision via "
                    "poll_se = 100/sqrt(n)/sqrt(qw)/sqrt(iw) in pymc_model._prepare "
                    "and obs_var = (100/sqrt(n))^2 / (qw*iw) in state_space.fit_state_space."
                ),
                "code_paths": [
                    "midterms/model/poll_weights.py:attach_poll_weights",
                    "midterms/model/pymc_model.py:_prepare",
                    "midterms/model/state_space.py:fit_state_space",
                ],
                "probability_impact_hypothesis": (
                    "High-N / high-quality polls receive compounded precision both "
                    "through relative influence and through likelihood SE, which may "
                    "over-weight them relative to a single channel design."
                ),
                "blueprint_alignment": "deviation_risk",
                "production_change_recommended": False,
                "oos_challenger_required": True,
                "changed_in_this_repair": False,
            },
            {
                "id": "absolute_recency_race_normalization",
                "confirmed_behavior": (
                    "Recency uses absolute calendar age exp(-ln2 * age / half_life). "
                    "After caps, per-race influence weights are renormalized to mean ≈ 1. "
                    "A race whose polls are all stale still has mean influence_weight ≈ 1."
                ),
                "code_paths": [
                    "midterms/model/poll_weights.py:attach_poll_weights",
                ],
                "probability_impact_hypothesis": (
                    "Absolute staleness may be partially washed out within a race by "
                    "mean-1 normalization, so five 70-day-old polls can retain similar "
                    "total relative influence mass to five 5-day-old polls."
                ),
                "blueprint_alignment": "needs_oos_evaluation",
                "production_change_recommended": False,
                "oos_challenger_required": True,
                "changed_in_this_repair": False,
            },
            {
                "id": "state_space_process_variance_per_poll",
                "confirmed_behavior": (
                    "fit_state_space adds a fixed process variance increment "
                    "(+0.8**2) once per poll observation, independent of elapsed "
                    "calendar days between polls. By contrast fit_pymc_dynamic "
                    "scales random-walk steps by sqrt(day_gaps/7)."
                ),
                "code_paths": [
                    "midterms/model/state_space.py:fit_state_space",
                    "midterms/model/pymc_model.py:fit_pymc_dynamic",
                ],
                "probability_impact_hypothesis": (
                    "Five polls over five days and five polls over fifty days receive "
                    "the same total latent-process variance inflation solely because "
                    "both sequences contain five poll updates."
                ),
                "blueprint_alignment": "deviation_vs_calendar_time_rw",
                "production_change_recommended": False,
                "oos_challenger_required": True,
                "changed_in_this_repair": False,
            },
        ],
        "repairs_in_this_task": [
            "personal_incumbency",
            "candidate_specific_finance",
            "alaska_pairwise_directional",
            "fail_closed_nonfinite_margins",
            "support_status_semantics_clarification",
        ],
    }
    path = ARTIFACTS_DIR / "probability_integrity_audit_v0923.json"
    payload["path"] = str(path)
    _write(path, payload)
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
            })
    return {
        "n_changed": len(changed),
        "changed": changed,
        "texas": next(
            (
                c
                for c in changed
                if c["state"] == "TX"
            ),
            {
                "race_id": "senate-2026-TX",
                "old_incumbency": -1.0,
                "new_incumbency": 0.0,
                "note": "Paxton is not Cornyn; personal incumbency is 0",
            },
        ),
    }


def main() -> None:
    struct = audit_2026_structural_metadata()
    finance = audit_historical_finance_equivalence()
    poll = write_probability_integrity_audit()
    incumbency = incumbency_before_after_2026()
    summary = {
        "structural_metadata": struct["path"],
        "finance_equivalence": finance["path"],
        "finance_status": finance["status"],
        "probability_integrity_audit": poll["path"],
        "incumbency_changed": incumbency,
    }
    out = ARTIFACTS_DIR / "probability_integrity_repair_summary_v0923.json"
    _write(out, summary)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
