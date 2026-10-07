"""Independent MT/ID contest-field and poll-inclusion audits (non-circular)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, NORMALIZED_DIR, ROOT
from midterms.evidence.current_candidates import (
    CURRENT_CANDIDATE_REGISTRY_PATH,
    load_current_candidate_registry,
)
from midterms.model.multiway_plurality import (
    MULTIWAY_CONTEST_STRUCTURE,
    MULTIWAY_MODELING_PATH,
    historical_analog_support_report,
)
from midterms.validation.official_ballot_fields import (
    certified_candidates,
    load_official_ballot_fields,
    official_race,
)

TARGET_STATES = ("MT", "ID")


def _poll_frame() -> pd.DataFrame:
    path = NORMALIZED_DIR / "polls.parquet"
    if not path.is_file():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    return frame[frame["election_id"].astype(str).eq("senate-2026")].copy()


def _matchup_candidates(matchup_id: str) -> list[str]:
    text = str(matchup_id or "")
    if not text:
        return []
    if "|" in text:
        return [part.split(":")[-1].strip() for part in text.split("|") if part.strip()]
    if ":" in text:
        return [text.split(":")[-1]]
    return [text]


def _slug(candidate_id: str) -> str:
    return str(candidate_id).split(":")[-1]


def build_mt_id_contest_field_audit() -> dict[str, Any]:
    """Audit MT/ID using official ballot authorities — never the registry as truth."""
    official = load_official_ballot_fields()
    registry = load_current_candidate_registry()
    reg_by_state = {str(r["state"]): r for r in registry["races"]}
    races_out: list[dict[str, Any]] = []

    for state in TARGET_STATES:
        off = official_race(state)
        certified = certified_candidates(state)
        n_certified = len(certified)
        structure = str(off["contest_structure"])
        support = historical_analog_support_report(n_analogs=0)
        if structure == MULTIWAY_CONTEST_STRUCTURE:
            path = MULTIWAY_MODELING_PATH
            win_status = "fail_closed"
            prob_status = "unsupported"
        else:
            path = "binary_non_major_adapter"
            win_status = "ok"
            prob_status = "limited_supported"

        reg = reg_by_state.get(state, {})
        reg_ballot_ids = {
            _slug(str(row.get("candidate_id") or ""))
            for row in (reg.get("ballot_candidates") or [])
            if str(row.get("status") or "")
            in {"certified_general_ballot", "reviewed_general_modeled", "reviewed_general_opposing"}
            or str(row.get("status", "")).startswith("reviewed_general")
        }
        # Modeled/opposing alone are not a complete ballot field.
        official_ids = {_slug(c["candidate_id"]) for c in certified}
        omitted_from_registry = sorted(official_ids - reg_ballot_ids)
        extra_in_registry_as_verified = sorted(reg_ballot_ids - official_ids)

        races_out.append(
            {
                "race_id": off["race_id"],
                "state": state,
                "registry_used_as_ballot_authority": False,
                "authority": off["authority"],
                "previous_registry_contest_structure": reg.get("contest_structure"),
                "verified_contest_structure": structure,
                "previous_modeling_path": "binary_non_major_adapter",
                "recommended_modeling_path": path,
                "n_certified_general_ballot_candidates": n_certified,
                "probability_model_support_status": prob_status,
                "win_probability_status": win_status,
                "analog_support": support,
                "ballot_candidates": certified,
                "registry_errors": {
                    "official_candidates_omitted_from_registry": omitted_from_registry,
                    "registry_verified_candidates_not_on_official_ballot": extra_in_registry_as_verified,
                    "circular_binary_assumption_detected": (
                        str(reg.get("contest_structure")) == "non_major_party_vs_republican"
                        and n_certified >= 3
                    ),
                },
                "prior_binary_probabilities_invalid": n_certified >= 3,
                "outside_model_probabilities_used": False,
            }
        )

    return {
        "schema_version": "mt-id-contest-field-audit-v0924.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "registry_used_as_ballot_authority": False,
        "authority_root": official.get("disallowed_authorities"),
        "races": races_out,
    }


def _classify_poll(
    matchup_slugs: set[str],
    official_slugs: set[str],
) -> str:
    if not matchup_slugs:
        return "missing_matchup_identity"
    if matchup_slugs == official_slugs:
        return "full_multiway"
    if matchup_slugs.issubset(official_slugs) and len(matchup_slugs) >= 3:
        return "partial_multiway"
    if matchup_slugs.issubset(official_slugs) and len(matchup_slugs) == 2:
        return "explicit_binary_matchup"
    if matchup_slugs & official_slugs and matchup_slugs - official_slugs:
        return "outdated_candidate_field"
    if not (matchup_slugs & official_slugs):
        return "hypothetical_or_unrelated_matchup"
    return "partial_multiway"


def build_mt_id_poll_inclusion_audit() -> dict[str, Any]:
    field = build_mt_id_contest_field_audit()
    by_state = {row["state"]: row for row in field["races"]}
    raw = _poll_frame()
    rows: list[dict[str, Any]] = []
    for state in TARGET_STATES:
        info = by_state[state]
        official_slugs = {_slug(c["candidate_id"]) for c in info["ballot_candidates"]}
        state_raw = raw[raw["state"].astype(str).eq(state)]
        multiway = info["verified_contest_structure"] == MULTIWAY_CONTEST_STRUCTURE
        for _, poll in state_raw.iterrows():
            matchup = str(poll.get("matchup_id") or "")
            matchup_slugs = set(_matchup_candidates(matchup))
            poll_type = _classify_poll(matchup_slugs, official_slugs)
            omitted = sorted(official_slugs - matchup_slugs)
            # Under multiway structure, binary/partial polls do not enter a
            # validated multiway likelihood (no fake renormalization).
            if multiway:
                included = False
                reason = (
                    "multiway_contest_binary_or_partial_poll_not_validated_likelihood"
                    if poll_type
                    in {
                        "explicit_binary_matchup",
                        "partial_multiway",
                        "outdated_candidate_field",
                        "hypothetical_or_unrelated_matchup",
                        "missing_matchup_identity",
                    }
                    else "multiway_probability_model_unsupported"
                )
                if poll_type == "full_multiway":
                    reason = "full_multiway_poll_but_probability_model_unsupported"
                model_qty = "diagnostic_only"
            else:
                included = matchup_slugs == official_slugs or (
                    len(official_slugs) == 2 and matchup_slugs == official_slugs
                )
                reason = None if included else "matchup_not_current_reviewed_pair"
                model_qty = "modeled_candidate_margin" if included else None

            rows.append(
                {
                    "race_id": info["race_id"],
                    "state": state,
                    "poll_id": str(poll.get("poll_id") or ""),
                    "pollster": poll.get("pollster") or poll.get("pollster_id"),
                    "field_start": str(poll.get("field_start") or ""),
                    "field_end": str(poll.get("field_end") or ""),
                    "sample_size": poll.get("sample_size"),
                    "population": poll.get("population"),
                    "matchup_id": matchup,
                    "candidates_or_options": sorted(matchup_slugs),
                    "ballot_field_candidates_omitted": omitted,
                    "poll_type": poll_type,
                    "raw_dem_share": poll.get("dem_share"),
                    "raw_rep_share": poll.get("rep_share"),
                    "two_party_margin": poll.get("two_party_margin"),
                    "included": included,
                    "model_quantity_informed": model_qty,
                    "model_path": None,
                    "exclusion_reason": reason,
                    "share_renormalized_to_fake_multiway": False,
                    "silent_multiway_to_binary": False,
                    "candidate_identity_resolution": {
                        slug: ("on_certified_ballot" if slug in official_slugs else "not_on_certified_ballot")
                        for slug in sorted(matchup_slugs)
                    },
                }
            )
    return {
        "schema_version": "mt-id-poll-inclusion-audit-v0924.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "policy": (
            "Polls are classified against the independently verified ballot field. "
            "Binary or partial questionnaires are not renormalized into a fake "
            "multiway November ballot. Unsupported multiway races withhold win "
            "probability rather than reuse the binary non-major adapter."
        ),
        "rows": rows,
        "n_rows": len(rows),
        "n_included": sum(1 for row in rows if row["included"]),
    }


def apply_official_fields_to_registry(
    *,
    states: tuple[str, ...] = ("MT", "ID", "NE", "SD"),
) -> dict[str, Any]:
    """Rewrite registry ballot fields from official evidence (non-circular)."""
    path = CURRENT_CANDIDATE_REGISTRY_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    official = load_official_ballot_fields()
    for race in payload["races"]:
        state = str(race["state"])
        if state not in states or state not in official["races"]:
            continue
        off = official["races"][state]
        certified = [
            {
                "candidate_id": c["candidate_id"],
                "candidate_name": c["candidate_name"],
                "ballot_party": c["ballot_party"],
                "ballot_party_label": c.get("ballot_party_label"),
                "caucus": c.get("caucus"),
                "caucus_basis": c.get("caucus_basis"),
                "status": "certified_general_ballot",
                "qualification_source": off["authority"]["source_url"],
                "qualification_authority": off["authority"]["issuer"],
                "source_publication_date": off["authority"].get("publication_date"),
                "reviewed_as_of": official["reviewed_as_of"],
                "write_in": bool(c.get("write_in")),
                "withdrawn": bool(c.get("withdrawn")),
            }
            for c in off["candidates"]
            if c.get("status") == "certified_general_ballot" and not c.get("withdrawn")
        ]
        race["ballot_candidates"] = certified
        race["contest_structure"] = off["contest_structure"]
        race["ballot_field_authority"] = off["authority"]
        if off["contest_structure"] == MULTIWAY_CONTEST_STRUCTURE:
            race["ordinary_binary_target_supported"] = False
            race["exceptional_probability_model_supported"] = False
            race["probability_model_support_status"] = "unsupported"
            race["statistical_target_supported"] = False
            race["win_probability_status"] = "fail_closed"
            race["modeling_path"] = MULTIWAY_MODELING_PATH
            race["note"] = (
                "Official general ballot is multiway plurality. Binary non-major "
                "adapter disabled. Win probability withheld until a historically "
                "supported multiway model exists. Prior binary I-v-R probabilities "
                "are superseded under the wrong contest structure."
            )
        else:
            # SD remains binary I-v-R
            race["ordinary_binary_target_supported"] = False
            race["exceptional_probability_model_supported"] = True
            race["probability_model_support_status"] = "limited_supported"
            race["statistical_target_supported"] = False
            race["modeling_path"] = "binary_non_major_adapter"
            race["note"] = (
                "Official SD general ballot is binary Independent-vs-Republican "
                "(Democratic nominee withdrawn). Binary non-major adapter retained."
            )
    # Keep a single registry review boundary; ballot-field authority timestamps
    # remain on ballot_field_authority / candidate qualification metadata.
    reviewed = str(payload.get("reviewed_as_of") or official["reviewed_as_of"])
    payload["reviewed_as_of"] = reviewed
    for race in payload["races"]:
        race["reviewed_as_of"] = reviewed
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return load_current_candidate_registry()


def write_mt_id_audits() -> dict[str, Any]:
    field = build_mt_id_contest_field_audit()
    polls = build_mt_id_poll_inclusion_audit()
    field_path = ARTIFACTS_DIR / "mt_id_contest_field_audit_v0924.json"
    poll_path = ARTIFACTS_DIR / "mt_id_poll_inclusion_audit_v0924.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    field_path.write_text(json.dumps(field, indent=2) + "\n", encoding="utf-8")
    poll_path.write_text(json.dumps(polls, indent=2) + "\n", encoding="utf-8")
    return {
        "contest_field": str(field_path.relative_to(ROOT)),
        "poll_inclusion": str(poll_path.relative_to(ROOT)),
        "field": field,
        "polls": polls,
    }
