"""Structural audit for ID/MT/NE/SD using independent official ballot fields."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.model.multiway_plurality import (
    MULTIWAY_CONTEST_STRUCTURE,
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
        n = len(
            [
                c
                for c in off.get("candidates") or []
                if c.get("status") == "certified_general_ballot" and not c.get("withdrawn")
            ]
        )
        structure = off["contest_structure"]
        if structure == MULTIWAY_CONTEST_STRUCTURE:
            path = MULTIWAY_MODELING_PATH
            validation = "unsupported"
            prior_binary_invalid = True
        else:
            path = "binary_non_major_adapter"
            validation = "limited_supported"
            prior_binary_invalid = False
        races[state] = {
            "race_id": off["race_id"],
            "verified_structure": structure,
            "statistical_path": path,
            "validation_level": validation,
            "n_certified_ballot_candidates": n,
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
                for c in off.get("candidates") or []
                if c.get("status") == "certified_general_ballot" and not c.get("withdrawn")
            ],
            "outside_model_probabilities_used": False,
            "registry_used_as_ballot_authority": False,
        }
    return {
        "schema_version": "nonmajor-contest-structure-audit-v0924",
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
    path = ARTIFACTS_DIR / "nonmajor_contest_structure_audit_v0924.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
