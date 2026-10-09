"""Historical validation for the generic multiway plurality forecast adapter.

Election-result analog counts alone never activate win probabilities. This
module reconstructs candidate-level multiway poll questions at formal cutoffs
and scores only predeclared eligible cases. When no scorable poll-driven cases
exist, the adapter remains unsupported.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR
from midterms.model.multiway_plurality import (
    ADAPTER_SPEC_VERSION,
    MULTIWAY_SUPPORT_LIMITED,
    MULTIWAY_SUPPORT_UNSUPPORTED,
    MULTIWAY_VALIDATION_CLASS,
    adapter_specification,
    classify_multiway_question,
    historical_analog_support_report,
    shares_sum_to_one,
)
from midterms.validation.historical_multiway_analogs import (
    discover_historical_multiway_plurality_analogs,
)

VALIDATION_SCHEMA = "multiway-plurality-validation-v1"
ELIGIBILITY_RULE_ID = "multiway-poll-validation-universe-v1"
FORMAL_LEAD_DAYS = (60, 30)
MIN_SCORABLE_FOR_LIMITED = 4


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def predeclared_eligibility_rule() -> dict[str, Any]:
    """Fold-safe eligibility contract declared before examining scores."""
    return {
        "id": ELIGIBILITY_RULE_ID,
        "predeclared_before_scoring": True,
        "require": [
            "plurality institutional rule",
            "at least three ballot candidates",
            "structural_class == materially_multiway (not principal-binary-with-minors)",
            "at least one candidate-level multiway poll question before cutoff",
            "certified candidate-level vote shares available after freeze",
        ],
        "exclude": [
            "principal_binary_with_minor_residual elections used as multiway proxies",
            "Alaska RCV / IRV",
            "majority-runoff / jungle-primary contests",
            "fabricated multiway polls",
        ],
        "formal_lead_days": list(FORMAL_LEAD_DAYS),
        "note": (
            "Materially multiway races are validated separately from "
            "ordinary D/R + small-residual contests."
        ),
    }


def _election_day_for_analog(analog: dict[str, Any]) -> date | None:
    year = int(analog.get("year") or 0)
    if year < 1990:
        return None
    from midterms.evidence.federal_election_day import federal_election_day

    return federal_election_day(year)


def _candidate_level_poll_source_available() -> dict[str, Any]:
    """Report whether a historical candidate-level multiway poll archive exists."""
    votehub_shares = NORMALIZED_DIR / "poll_candidate_shares_votehub.parquet"
    fte = NORMALIZED_DIR / "polls_fte_historical.parquet"
    report = {
        "votehub_candidate_shares_present": votehub_shares.is_file(),
        "fte_historical_present": fte.is_file(),
        "fte_is_binary_two_party_only": True,
        "historical_multiway_poll_archive_present": False,
        "usable_for_multiway_share_validation": False,
    }
    if votehub_shares.is_file():
        df = pd.read_parquet(votehub_shares)
        years = set()
        if "available_at" in df.columns:
            years = {
                int(str(v)[:4])
                for v in df["available_at"].dropna().astype(str)
                if str(v)[:4].isdigit()
            }
        # Live VoteHub shares are current-cycle only in this warehouse.
        report["votehub_share_years"] = sorted(years)
        report["historical_multiway_poll_archive_present"] = bool(years & set(range(2014, 2026)))
        report["usable_for_multiway_share_validation"] = report[
            "historical_multiway_poll_archive_present"
        ]
    return report


def build_multiway_plurality_validation(
    *,
    analogs: dict[str, Any] | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the conservative multiway validation artifact.

    Without a historical candidate-level multiway poll archive, the scorable
    sample is empty and win probabilities remain unsupported.
    """
    analogs = analogs or discover_historical_multiway_plurality_analogs()
    eligibility = predeclared_eligibility_rule()
    poll_source = _candidate_level_poll_source_available()
    material = [
        row for row in (analogs.get("analogs") or [])
        if row.get("materially_multiway") is True
        or str(row.get("structural_class") or "") == "materially_multiway"
    ]
    eligible_universe: list[dict[str, Any]] = []
    for analog in material:
        election_day = _election_day_for_analog(analog)
        if election_day is None:
            continue
        n_cands = int(analog.get("n_candidates") or len(analog.get("candidates") or []))
        if n_cands < 3:
            continue
        eligible_universe.append({
            "race_id": analog.get("race_id"),
            "state": analog.get("state"),
            "year": analog.get("year"),
            "n_candidates": n_cands,
            "structural_class": analog.get("structural_class"),
            "election_day": election_day.isoformat(),
            "eligible_cutoffs": [
                (election_day - timedelta(days=lead)).isoformat() for lead in FORMAL_LEAD_DAYS
            ],
            "has_certified_candidate_shares": bool(analog.get("candidates")),
            "has_usable_multiway_polls_before_cutoff": False,
            "exclusion_reason": (
                None
                if poll_source["usable_for_multiway_share_validation"]
                else "no_historical_candidate_level_multiway_poll_archive"
            ),
        })

    scorable_cases: list[dict[str, Any]] = []
    # Honest empty set: do not fabricate polls or score binary FTE rows as multiway.
    metrics = {
        "candidate_share_mae": None,
        "winner_accuracy": None,
        "winner_margin_mae": None,
        "crps_multivariate": None,
        "mass_conservation_rate": None,
        "candidate_winner_frequency": None,
        "interval_coverage": None,
    }
    n_scorable = len(scorable_cases)
    activates = n_scorable >= MIN_SCORABLE_FOR_LIMITED
    support_status = MULTIWAY_SUPPORT_LIMITED if activates else MULTIWAY_SUPPORT_UNSUPPORTED
    structural = historical_analog_support_report(n_analogs=int(analogs.get("n_analogs") or 0))
    payload = {
        "schema_version": VALIDATION_SCHEMA,
        "model_version": MODEL_VERSION,
        "adapter_spec_version": ADAPTER_SPEC_VERSION,
        "adapter_specification": adapter_specification(),
        "validation_class": MULTIWAY_VALIDATION_CLASS if activates else None,
        "probability_model_support_status": support_status,
        "activates_forecast_probabilities": bool(activates),
        "win_probability_status": "ok" if activates else "fail_closed",
        "eligibility_rule": eligibility,
        "structural_analog_inventory": {
            "n_analogs": analogs.get("n_analogs"),
            "n_ballot_multiway": analogs.get("n_ballot_multiway"),
            "n_principal_binary_with_minors": analogs.get("n_principal_binary_with_minors"),
            "n_materially_multiway": analogs.get("n_materially_multiway"),
            "structural_inventory_only": True,
            "does_not_activate_probabilities": True,
            "analog_support_report": structural,
        },
        "poll_source_audit": poll_source,
        "eligible_universe": eligible_universe,
        "scorable_cases": scorable_cases,
        "summary": {
            "n_materially_multiway_analogs": len(material),
            "n_predeclared_eligible_races": len(eligible_universe),
            "n_scorable_cases": n_scorable,
            "n_scorable_fold_cutoffs": 0,
            "min_scorable_for_limited_validation": MIN_SCORABLE_FOR_LIMITED,
            "metrics": metrics,
            "calibration_claim_allowed": False,
            "label": (
                MULTIWAY_VALIDATION_CLASS
                if activates
                else "insufficient_scorable_historical_multiway_poll_cases"
            ),
        },
        "ordinary_oof_touched": False,
        "note": (
            "Materially multiway structural analogs exist, but no historical "
            "candidate-level multiway poll archive is available to freeze and "
            "score a share model. Montana remains correctly classified and "
            "withheld from publishable win probabilities until scorable "
            "historical poll validation exists."
        ),
    }
    payload["artifact_sha256"] = _canonical_sha256(payload)
    path = Path(out_path or ARTIFACTS_DIR / "multiway_plurality_validation_latest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**payload, "path": str(path)}


def smoke_fit_multiway_share_draws(
    *,
    n_candidates: int = 4,
    n_draws: int = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """Cheap synthetic check of share/winner invariants (no PyMC)."""
    rng = np.random.default_rng(seed)
    alpha = np.full(n_candidates, 1.5)
    shares = rng.dirichlet(alpha, size=n_draws)
    assert shares_sum_to_one(shares)
    winners = shares.argmax(axis=1)
    return {
        "n_draws": n_draws,
        "n_candidates": n_candidates,
        "shares_sum_to_one": True,
        "n_unique_winners": int(len(set(winners.tolist()))),
        "question_classes_recognized": [
            classify_multiway_question(
                ["a", "b", "c", "d"],
                ballot_candidate_ids=["a", "b", "c", "d"],
            ),
            classify_multiway_question(
                ["a", "b"],
                ballot_candidate_ids=["a", "b", "c", "d"],
            ),
        ],
    }
