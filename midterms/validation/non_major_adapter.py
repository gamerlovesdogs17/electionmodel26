"""Cheap, separate validation for the limited non-major-party adapter."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.outcome_identity import INDEPENDENT_DEM_CAUCUSES_BASIS
from midterms.evidence.warehouse import Warehouse
from midterms.model.non_major_adapter import (
    ADAPTER_SPEC_VERSION,
    COMMON_VARIANCE_SHARE,
    PRIOR_SD,
    ZERO_CENTERED_COMPARATOR_SD,
    fit_non_major_adapter,
)

VALIDATION_SCHEMA_VERSION = "non-major-adapter-validation-v2"
INCLUSION_RULE = (
    "U.S. Senate general election in which the modeled candidate appeared under "
    "an Independent/non-major label, the opposing candidate was Republican, no "
    "separate Democratic nominee appeared in the general contest, and any other "
    "ballot candidates together received less than five percent. The rule is "
    "declared before computing adapter scores."
)

ANALOGS = (
    {
        "race_id": "senate-2014-KS",
        "state": "KS",
        "election_day": "2014-11-04",
        "modeled_candidate": "Greg Orman",
        "opposing_candidate": "Pat Roberts",
        "source_url": "https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/federal-elections-2014/",
        "structure": "no Democratic nominee; Independent vs Republican plus minor Libertarian",
        "poll_source_status": "no_compatible_archived_polls_in_repository",
    },
    {
        "race_id": "senate-2020-AK",
        "state": "AK",
        "election_day": "2020-11-03",
        "modeled_candidate": "Al Gross",
        "opposing_candidate": "Dan Sullivan",
        "source_url": "https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/federal-elections-2020/",
        "structure": "FEC label N(D)/D vs Republican; no separate Democratic general nominee; AKI candidate below five percent",
        "poll_source_status": "scorable",
    },
    {
        "race_id": "senate-2024-NE",
        "state": "NE",
        "election_day": "2024-11-05",
        "modeled_candidate": "Dan Osborn",
        "opposing_candidate": "Deb Fischer",
        "source_url": "https://www.fec.gov/resources/cms-content/documents/2024congressgecands.pdf",
        "structure": "official two-candidate By Petition vs Republican general ballot",
        "poll_source_status": "scorable",
    },
)

# Predeclared before scoring.  The selected state-anchored 30 point deviation
# is the simplest conservative structural alternative; the 40 point form is a
# sensitivity comparator, not a parameter search.
PRIOR_SPECIFICATIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "zero_centered_weak",
        "prior_mode": "zero_centered",
        "prior_sd": ZERO_CENTERED_COMPARATOR_SD,
        "production_candidate": False,
    },
    {
        "id": "state_structural_very_large",
        "prior_mode": "state_structural",
        "prior_sd": PRIOR_SD,
        "production_candidate": True,
    },
    {
        "id": "state_structural_extra_large",
        "prior_mode": "state_structural",
        "prior_sd": 40.0,
        "production_candidate": False,
    },
)
SELECTED_PRIOR_SPEC_ID = "state_structural_very_large"


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _truth_by_race() -> dict[str, float]:
    data = json.loads((RAW_DIR / "external" / "official_senate_ledger.json").read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    cycles = data.get("cycles") or {}
    cycle_rows = cycles.values() if isinstance(cycles, dict) else cycles
    for cycle in cycle_rows:
        for row in cycle.get("contests") or []:
            race_id = str(row.get("race_id") or "")
            if race_id in {"senate-2020-AK", "senate-2024-NE"}:
                out[race_id] = float(row["margin_value"])
    return out


def _historical_poll_frame(analog: dict[str, Any]) -> pd.DataFrame:
    race_id = analog["race_id"]
    if race_id == "senate-2020-AK":
        frame = pd.read_parquet(NORMALIZED_DIR / "polls_fte_historical.parquet")
        frame = frame[frame["race_id"].astype(str).eq(race_id)].copy()
        frame["modeled_candidate_id"] = "historical:al-gross"
        frame["modeled_candidate_name"] = "Al Gross"
        frame["modeled_ballot_party"] = "I"
        frame["opposing_candidate_id"] = "historical:dan-sullivan"
        frame["opposing_candidate_name"] = "Dan Sullivan"
        frame["opposing_ballot_party"] = "R"
        frame["modeled_margin"] = frame["two_party_margin"]
        return frame
    if race_id != "senate-2024-NE":
        return pd.DataFrame()
    raw = pd.read_csv(RAW_DIR / "external" / "fte_senate_polls_historical.csv", low_memory=False)
    subset = raw[
        raw["cycle"].eq(2024)
        & raw["state"].astype(str).eq("Nebraska")
        & raw["seat_name"].astype(str).eq("Class I")
        & raw["stage"].astype(str).str.lower().eq("general")
        & ~raw["hypothetical"].fillna(False).astype(bool)
    ].copy()
    rows: list[dict[str, Any]] = []
    for question_id, group in subset.groupby("question_id", sort=False):
        names = group["candidate_name"].fillna("").astype(str)
        modeled = group[names.str.casefold().eq("dan osborn")]
        opposing = group[names.str.casefold().eq("deb fischer")]
        if modeled.empty or opposing.empty:
            continue
        m = modeled.iloc[0]
        r = opposing.iloc[0]
        total = float(m["pct"]) + float(r["pct"])
        if total <= 0:
            continue
        rows.append({
            "poll_id": f"fte-raw-{int(m['poll_id'])}-{int(question_id)}",
            "study_id": f"fte-study-{int(m['poll_id'])}",
            "pollster_id": str(m["pollster"]),
            "race_id": race_id,
            "field_start": pd.Timestamp(m["start_date"]).date().isoformat(),
            "field_end": pd.Timestamp(m["end_date"]).date().isoformat(),
            "available_at": pd.Timestamp(m["created_at"]).date().isoformat(),
            "sample_size": m["sample_size"],
            "population": str(m["population"]).upper(),
            "partisan": bool(m["partisan"]) if pd.notna(m["partisan"]) else False,
            "quality_weight": 1.0,
            "modeled_candidate_id": "historical:dan-osborn",
            "modeled_candidate_name": "Dan Osborn",
            "modeled_ballot_party": "I",
            "opposing_candidate_id": "historical:deb-fischer",
            "opposing_candidate_name": "Deb Fischer",
            "opposing_ballot_party": "R",
            "modeled_share": 100.0 * float(m["pct"]) / total,
            "opposing_share": 100.0 * float(r["pct"]) / total,
            "modeled_margin": 100.0 * (float(m["pct"]) - float(r["pct"])) / total,
            "margin_definition": "independent_minus_rep_two_candidate",
        })
    return pd.DataFrame(rows)


def _prior_snapshot(as_of: str) -> dict[str, Any]:
    path = (
        NORMALIZED_DIR / "prior_snapshots"
        / f"presidential-relative-prior-v1_{pd.Timestamp(as_of).date().isoformat()}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    semantic = {key: value for key, value in payload.items() if key != "snapshot_sha256"}
    if payload.get("snapshot_sha256") != _sha(semantic):
        raise ValueError(f"prior snapshot semantic fingerprint changed: {path}")
    return payload


def _state_prior(as_of: str, state: str) -> dict[str, Any]:
    snapshot = _prior_snapshot(as_of)
    row = next(item for item in snapshot["rows"] if item["state"] == state)
    return {
        "prior_lean": float(row["prior_lean"]),
        "prior_source": row["prior_source"],
        "prior_production_eligible": bool(row["prior_production_eligible"]),
        "prior_provenance_sha256": row["prior_provenance_sha256"],
        "prior_snapshot_sha256": snapshot["snapshot_sha256"],
        "prior_source_years": snapshot["source_years_newest_first"],
    }


def _race_row(analog: dict[str, Any], *, as_of: str) -> pd.DataFrame:
    prior = _state_prior(as_of, analog["state"])
    return pd.DataFrame([{
        "race_id": analog["race_id"],
        "state": analog["state"],
        "not_up": False,
        "contest_structure": "non_major_party_vs_republican",
        "current_matchup_status": "reviewed_current",
        "modeled_candidate_id": f"historical:{analog['modeled_candidate'].lower().replace(' ', '-')}",
        "modeled_candidate_name": analog["modeled_candidate"],
        "modeled_ballot_party": "I",
        "modeled_caucus": "D",
        "modeled_caucus_basis": INDEPENDENT_DEM_CAUCUSES_BASIS,
        "opposing_candidate_id": f"historical:{analog['opposing_candidate'].lower().replace(' ', '-')}",
        "opposing_candidate_name": analog["opposing_candidate"],
        "opposing_ballot_party": "R",
        "opposing_caucus": "R",
        "opposing_caucus_basis": "historical_major_party_identity",
        **prior,
    }])


def _crps(draws: np.ndarray, truth: float) -> float:
    x = np.sort(np.asarray(draws, dtype=float))
    n = len(x)
    first = float(np.mean(np.abs(x - float(truth))))
    coefficients = 2.0 * np.arange(1, n + 1) - n - 1.0
    pair_term = float(np.dot(coefficients, x)) / float(n * n)
    return first - pair_term


def _score_case(
    analog: dict[str, Any],
    *,
    lead_days: int,
    truth: float,
    prior_spec: dict[str, Any],
) -> dict[str, Any] | None:
    polls = _historical_poll_frame(analog)
    cutoff = pd.Timestamp(analog["election_day"]).date() - timedelta(days=int(lead_days))
    if polls.empty:
        return None
    available = pd.to_datetime(polls["available_at"], errors="coerce").dt.date
    polls = polls[available.notna() & (available <= cutoff)].copy()
    if polls.empty:
        return None
    fit = fit_non_major_adapter(
        _race_row(analog, as_of=cutoff.isoformat()), polls, as_of=cutoff, n_draws=4000,
        seed=9200 + int(lead_days) + int(analog["election_day"][:4]),
        prior_sd=float(prior_spec["prior_sd"]),
        prior_mode=str(prior_spec["prior_mode"]),
    )
    if not fit.race_ids:
        return None
    draws = fit.draws_margin[:, 0]
    probability = float(np.mean(draws > 0.0))
    outcome = float(truth > 0.0)
    record = fit.diagnostics["records"][0]
    return {
        "race_id": analog["race_id"],
        "as_of": cutoff.isoformat(),
        "lead_days": int(lead_days),
        "n_polls": len(polls),
        "poll_ids": sorted(polls["poll_id"].astype(str).tolist()),
        "truth_modeled_candidate_margin": float(truth),
        "predicted_margin": float(draws.mean()),
        "margin_error": float(draws.mean() - truth),
        "absolute_margin_error": float(abs(draws.mean() - truth)),
        "p_modeled_candidate": probability,
        "brier": float((probability - outcome) ** 2),
        "crps": _crps(draws, truth),
        "interval_05": float(np.quantile(draws, 0.05)),
        "interval_95": float(np.quantile(draws, 0.95)),
        "interval_90_covered": bool(
            np.quantile(draws, 0.05) <= truth <= np.quantile(draws, 0.95)
        ),
        "interval_90_width": float(np.quantile(draws, 0.95) - np.quantile(draws, 0.05)),
        "prior_spec_id": prior_spec["id"],
        "prior_mode": prior_spec["prior_mode"],
        "prior_location": record["prior_location"],
        "prior_sd": float(prior_spec["prior_sd"]),
        "posterior_location": record["posterior_location"],
        "poll_weighted_location": record["poll_weighted_location"],
        "poll_sensitivity": float(
            abs(record["posterior_location"] - record["prior_location"])
        ),
        "prior_snapshot_sha256": _race_row(
            analog, as_of=cutoff.isoformat()
        ).iloc[0]["prior_snapshot_sha256"],
        "exceptional_outcome_used_to_set_prior": False,
    }


def _aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        return {"n": 0, "calibration_claim_allowed": False}
    return {
        "n": len(cases),
        "mean_absolute_margin_error": float(np.mean([x["absolute_margin_error"] for x in cases])),
        "brier": float(np.mean([x["brier"] for x in cases])),
        "empirical_crps": float(np.mean([x["crps"] for x in cases])),
        "interval_90_coverage": float(np.mean([x["interval_90_covered"] for x in cases])),
        "average_interval_90_width": float(np.mean([x["interval_90_width"] for x in cases])),
        "average_poll_sensitivity": float(np.mean([x["poll_sensitivity"] for x in cases])),
        "calibration_claim_allowed": False,
        "limitation": "Too few structurally comparable races for a calibration claim.",
    }


def build_non_major_adapter_validation(*, current_as_of: str = "2026-10-03") -> dict[str, Any]:
    truths = _truth_by_race()
    scored: list[dict[str, Any]] = []
    prior_comparison: dict[str, Any] = {}
    for prior_spec in PRIOR_SPECIFICATIONS:
        cases = [
            case
            for analog in ANALOGS
            if analog["poll_source_status"] == "scorable"
            for lead in (60, 30)
            if (case := _score_case(
                analog,
                lead_days=lead,
                truth=truths[analog["race_id"]],
                prior_spec=prior_spec,
            )) is not None
        ]
        prior_comparison[str(prior_spec["id"])] = {
            "specification": dict(prior_spec),
            "aggregate": _aggregate(cases),
            "cases": cases,
        }
        if prior_spec["id"] == SELECTED_PRIOR_SPEC_ID:
            scored = cases

    warehouse = Warehouse(ensure_fixtures=False)
    cutoff = pd.Timestamp(current_as_of).date()
    current_polls = warehouse.polls[
        warehouse.polls["election_id"].astype(str).eq("senate-2026")
    ].copy()
    available = pd.to_datetime(current_polls["available_at"], errors="coerce").dt.date
    current_polls = current_polls[
        available.notna()
        & (available <= cutoff)
        & current_polls["exclusion_status"].fillna("include").eq("include")
    ].copy()
    current_races = warehouse.races[
        warehouse.races["election_id"].astype(str).eq("senate-2026")
    ].copy()
    from midterms.evidence.candidate_timeline import (
        apply_candidate_state_contract,
        candidate_structural_gaps_for_cutoff,
    )

    current_races, _safe, candidate_meta = apply_candidate_state_contract(
        current_races,
        warehouse.candidate_timeline,
        current_polls,
        as_of=cutoff,
        structural_gaps=candidate_structural_gaps_for_cutoff(
            warehouse.candidate_source_audit,
            election_id="senate-2026",
            as_of=cutoff,
        ),
    )
    current_prior = _prior_snapshot(current_as_of)
    current_prior_by_state = {
        row["state"]: row for row in current_prior["rows"]
    }
    current_races = current_races.copy()
    current_races["prior_lean"] = current_races["state"].map(
        lambda state: current_prior_by_state[str(state)]["prior_lean"]
    )
    current_races["prior_production_eligible"] = current_races["state"].map(
        lambda state: current_prior_by_state[str(state)]["prior_production_eligible"]
    )
    current_races["prior_source"] = current_races["state"].map(
        lambda state: current_prior_by_state[str(state)]["prior_source"]
    )
    current_races["prior_provenance_sha256"] = current_races["state"].map(
        lambda state: current_prior_by_state[str(state)]["prior_provenance_sha256"]
    )
    current_races["prior_snapshot_sha256"] = current_prior["snapshot_sha256"]
    compatible = candidate_meta.get("candidate_compatible_poll_ids_by_race") or {}
    exceptional_ids = {
        str(poll_id)
        for race_id, poll_ids in compatible.items()
        if str(race_id).split("-")[-1] in {"ID", "MT", "NE", "SD"}
        for poll_id in poll_ids
    }
    exceptional = current_polls[
        current_polls["poll_id"].astype(str).isin(exceptional_ids)
    ].copy()
    audit: list[dict[str, Any]] = []
    for state in ("ID", "MT", "NE", "SD"):
        race_id = f"senate-2026-{state}"
        rows = exceptional[exceptional.get(
            "race_id", pd.Series("", index=exceptional.index),
        ).astype(str).eq(race_id)].copy()
        audit.append({
            "race_id": race_id,
            "state": state,
            "n_usable_polls": len(rows),
            "polls": [
                {
                    key: row.get(key) for key in (
                        "poll_id", "pollster_id", "field_start", "field_end",
                        "available_at", "population", "sample_size",
                        "modeled_candidate_name", "opposing_candidate_name",
                        "modeled_margin", "margin_definition",
                    )
                }
                for row in rows.sort_values("field_end").to_dict(orient="records")
            ],
            "votehub_coverage": "zero" if rows.empty else "sparse" if len(rows) < 3 else "available",
            "additional_repository_source_ingested": False,
        })

    selected_spec = next(
        spec for spec in PRIOR_SPECIFICATIONS if spec["id"] == SELECTED_PRIOR_SPEC_ID
    )
    selected_current_fit = fit_non_major_adapter(
        current_races,
        exceptional,
        as_of=current_as_of,
        n_draws=8000,
        seed=923001,
        prior_mode=str(selected_spec["prior_mode"]),
        prior_sd=float(selected_spec["prior_sd"]),
    )
    selected_current_records = {
        record["race_id"]: record
        for record in selected_current_fit.diagnostics["records"]
    }
    zero_poll_behavior: dict[str, Any] = {}
    for spec in PRIOR_SPECIFICATIONS:
        fit = fit_non_major_adapter(
            current_races,
            exceptional,
            as_of=current_as_of,
            n_draws=8000,
            seed=923001,
            prior_mode=str(spec["prior_mode"]),
            prior_sd=float(spec["prior_sd"]),
        )
        by_race = {
            race_id: index for index, race_id in enumerate(fit.race_ids)
        }
        sd_index = by_race.get("senate-2026-SD")
        zero_poll_behavior[str(spec["id"])] = {
            "race_id": "senate-2026-SD",
            "n_compatible_polls": 0,
            "support_status": next(
                (
                    record["support_status"]
                    for record in fit.diagnostics["records"]
                    if record["race_id"] == "senate-2026-SD"
                ),
                "not_present",
            ),
            "prior_location": next(
                (
                    record["prior_location"]
                    for record in fit.diagnostics["records"]
                    if record["race_id"] == "senate-2026-SD"
                ),
                None,
            ),
            "predictive_mean": (
                None if sd_index is None else float(fit.mean_margin[sd_index])
            ),
            "predictive_sd": (
                None if sd_index is None else float(fit.sd_margin[sd_index])
            ),
            "interval_90_width": (
                None
                if sd_index is None
                else float(
                    np.quantile(fit.draws_margin[:, sd_index], 0.95)
                    - np.quantile(fit.draws_margin[:, sd_index], 0.05)
                )
            ),
        }

    # Common-shock sensitivity uses fixed synthetic ordinary draws and identical
    # exceptional seeds.  It changes dependence only; it is not scored against
    # current outcomes and does not tune the retained 20 percent value.
    rng = np.random.default_rng(923040)
    common = rng.normal(size=8000)
    ordinary_draws = np.column_stack([
        common + rng.normal(scale=0.7, size=8000),
        0.8 * common + rng.normal(scale=0.8, size=8000),
    ])
    from midterms.model.pymc_model import FitResult

    base_fit = FitResult(
        race_ids=["synthetic-ordinary-a", "synthetic-ordinary-b"],
        states=["AA", "BB"],
        mean_margin=ordinary_draws.mean(axis=0),
        sd_margin=ordinary_draws.std(axis=0),
        draws_margin=ordinary_draws,
        house_effects={},
        diagnostics={"purpose": "common_shock_sensitivity_only"},
        method="synthetic_common_factor",
    )
    common_sensitivity: dict[str, Any] = {}
    for common_share in (0.0, COMMON_VARIANCE_SHARE, 0.4):
        fit = fit_non_major_adapter(
            current_races,
            exceptional,
            as_of=current_as_of,
            n_draws=8000,
            seed=923041,
            base_fit=base_fit,
            prior_mode=str(selected_spec["prior_mode"]),
            prior_sd=float(selected_spec["prior_sd"]),
            common_variance_share=common_share,
        )
        ordinary_factor = ordinary_draws.mean(axis=1)
        probs = {
            race_id: float(np.mean(fit.draws_margin[:, index] > 0))
            for index, race_id in enumerate(fit.race_ids)
        }
        correlations = {
            race_id: float(np.corrcoef(ordinary_factor, fit.draws_margin[:, index])[0, 1])
            for index, race_id in enumerate(fit.race_ids)
        }
        common_sensitivity[str(common_share)] = {
            "p_modeled_candidate_by_race": probs,
            "correlation_with_synthetic_ordinary_factor_by_race": correlations,
            "exceptional_seat_count_variance": float(
                np.var((fit.draws_margin >= 0).sum(axis=1))
            ),
        }

    semantic = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "adapter_spec_version": ADAPTER_SPEC_VERSION,
        "classification": "limited_validation_exception_model",
        "target": "modeled_candidate_margin",
        "inclusion_rule": INCLUSION_RULE,
        "analogs": list(ANALOGS),
        "source_lineage": {
            "truth_ledger": {
                "path": "data/raw/external/official_senate_ledger.json",
                "sha256": _file_sha256(
                    RAW_DIR / "external" / "official_senate_ledger.json"
                ),
            },
            "normalized_historical_polls": {
                "path": "data/normalized/polls_fte_historical.parquet",
                "sha256": _file_sha256(
                    NORMALIZED_DIR / "polls_fte_historical.parquet"
                ),
            },
            "raw_historical_polls": {
                "path": "data/raw/external/fte_senate_polls_historical.csv",
                "sha256": _file_sha256(
                    RAW_DIR / "external" / "fte_senate_polls_historical.csv"
                ),
            },
            "current_polls": {
                "path": "data/normalized/polls.parquet",
                "sha256": _file_sha256(NORMALIZED_DIR / "polls.parquet"),
            },
        },
        "formal_lead_days": [60, 30],
        "selected_prior_spec_id": SELECTED_PRIOR_SPEC_ID,
        "selected_prior": {
            **selected_spec,
            "location_definition": "point_in_time_presidential_relative_state_lean",
            "interpretation": "weak geographic location anchor; not Independent-equals-Democrat",
            "estimated_from_analog_outcomes": False,
            "selection_reason": (
                "predeclared simplest state-anchored conservative specification; "
                "the four scored cases are too few for outcome-driven tuning"
            ),
        },
        "cases": scored,
        "aggregate": _aggregate(scored),
        "prior_specification_comparison": prior_comparison,
        "zero_poll_behavior": zero_poll_behavior,
        "selected_current_adapter_records": selected_current_records,
        "common_shock_sensitivity": {
            "retained_share": COMMON_VARIANCE_SHARE,
            "not_parameter_tuning": True,
            "results": common_sensitivity,
        },
        "current_poll_audit": audit,
        "ordinary_oof_touched": False,
        "leave_one_race_out_integrity": {
            "safe": True,
            "reason": (
                "exceptional outcomes are never used to estimate the structural "
                "location or uncertainty; every state anchor is derived only from "
                "presidential results available at that cutoff"
            ),
        },
        "calibration_claim_allowed": False,
    }
    return {**semantic, "artifact_sha256": _sha(semantic)}


def write_non_major_adapter_validation(*, current_as_of: str = "2026-10-03") -> dict[str, Any]:
    report = build_non_major_adapter_validation(current_as_of=current_as_of)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "non_major_adapter_validation_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report
