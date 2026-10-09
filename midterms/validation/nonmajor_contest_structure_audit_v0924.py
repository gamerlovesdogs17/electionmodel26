"""Structural audit for ID/MT/NE/SD using independent official ballot fields."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, ROOT
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.model.contest_classifier import (
    GENUINE_MULTIWAY_PLURALITY,
    PRINCIPAL_BINARY_WITH_MINORS,
    classify_contest_structure,
    registry_contest_structure,
)
from midterms.model.multiway_plurality import (
    MULTIWAY_MODELING_PATH,
    historical_analog_support_report,
)
from midterms.validation.official_ballot_fields import load_official_ballot_fields


def build_nonmajor_contest_structure_audit() -> dict[str, Any]:
    official = load_official_ballot_fields()
    registry = load_current_candidate_registry()
    reg_by_state = {str(r["state"]): r for r in registry["races"]}
    races: dict[str, Any] = {}
    for state, off in official["races"].items():
        reg = reg_by_state.get(state, {})
        certified = [
            c
            for c in off.get("candidates") or []
            if c.get("status") == "certified_general_ballot" and not c.get("withdrawn")
        ]
        n = len(certified)
        classification = classify_contest_structure(
            ballot_candidates=certified,
            election_rule=str(off.get("institutional_rule") or ""),
        )
        structure = registry_contest_structure(classification)
        if classification["category"] == GENUINE_MULTIWAY_PLURALITY:
            path = MULTIWAY_MODELING_PATH
            validation = "unsupported"
            prior_binary_invalid = True
        elif classification["category"] == PRINCIPAL_BINARY_WITH_MINORS:
            path = "candidate_neutral_binary_with_minor_residual"
            validation = "limited_supported_principal_binary"
            prior_binary_invalid = False
        else:
            path = str(classification.get("modeling_path") or "binary_non_major_adapter")
            validation = "limited_supported"
            prior_binary_invalid = False
        races[state] = {
            "race_id": off["race_id"],
            "verified_structure": structure,
            "classifier_category": classification["category"],
            "statistical_path": path,
            "validation_level": validation,
            "n_certified_ballot_candidates": n,
            "principal_pair": classification.get("principal_pair"),
            "residual_candidates": classification.get("residual_candidates"),
            "classification_evidence": classification.get("classification_evidence"),
            "authority": off["authority"],
            "previous_registry_structure": reg.get("contest_structure"),
            "previous_registry_path": (
                "binary_non_major_adapter"
                if reg.get("contest_structure") == "non_major_party_vs_republican"
                else reg.get("modeling_path")
            ),
            "prior_binary_probabilities_invalid": prior_binary_invalid,
            "analog_support": historical_analog_support_report(n_analogs=0),
            "candidates": [
                {
                    "candidate_name": c["candidate_name"],
                    "ballot_party": c["ballot_party"],
                    "caucus": c.get("caucus"),
                }
                for c in certified
            ],
            "outside_model_probabilities_used": False,
            "registry_used_as_ballot_authority": False,
        }
    return {
        "schema_version": "nonmajor-contest-structure-audit-v0925",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "registry_used_as_ballot_authority": False,
        "alaska": {
            "race_id": "senate-2026-AK",
            "verified_structure": "ranked_choice_multiway",
            "statistical_path": "alaska_rcv_adapter",
            "validation_level": "limited",
        },
        "races": races,
        "decision_gate": {
            state: {
                "verified_structure": races[state]["verified_structure"],
                "statistical_path": races[state]["statistical_path"],
                "validation_level": races[state]["validation_level"],
            }
            for state in ("ID", "MT", "NE", "SD")
        },
    }


def write_nonmajor_contest_structure_audit() -> dict[str, Any]:
    payload = build_nonmajor_contest_structure_audit()
    path = ARTIFACTS_DIR / "nonmajor_contest_structure_audit_v0925.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        payload["path"] = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        payload["path"] = path.as_posix()
    return payload
