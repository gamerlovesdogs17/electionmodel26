"""MT/ID contest-field and poll-inclusion audits for v0.9.24 (no probability tuning)."""

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
from midterms.evidence.warehouse import Warehouse
from midterms.model.multiway_plurality import (
    MULTIWAY_CONTEST_STRUCTURE,
    historical_analog_support_report,
)

TARGET_STATES = ("MT", "ID")


def _poll_frame() -> pd.DataFrame:
    path = NORMALIZED_DIR / "polls.parquet"
    if not path.is_file():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    return frame[frame["election_id"].astype(str).eq("senate-2026")].copy()


def _matchup_candidates(matchup_id: str) -> list[str]:
    """Extract candidate slugs from VoteHub-style matchup ids.

    Example: ``senate-2026:MT:votehub:alani-bankhead|votehub:kurt-alme``
    → ``['alani-bankhead', 'kurt-alme']``.
    """
    text = str(matchup_id or "")
    if not text:
        return []
    if "|" in text:
        parts = text.split("|")
        slugs: list[str] = []
        for part in parts:
            token = part.split(":")[-1].strip()
            if token:
                slugs.append(token)
        return slugs
    if ":" in text:
        return [text.split(":")[-1]]
    return [text]


def build_mt_id_contest_field_audit() -> dict[str, Any]:
    registry = load_current_candidate_registry()
    polls = _poll_frame()
    races_out: list[dict[str, Any]] = []
    for state in TARGET_STATES:
        race = next(r for r in registry["races"] if r["state"] == state)
        race_polls = polls[polls["state"].astype(str).eq(state)]
        observed: dict[str, dict[str, Any]] = {}
        for matchup_id, count in (
            race_polls.get("matchup_id", pd.Series(dtype=object))
            .dropna()
            .astype(str)
            .value_counts()
            .items()
        ):
            for slug in _matchup_candidates(str(matchup_id)):
                entry = observed.setdefault(
                    slug,
                    {
                        "candidate_slug": slug,
                        "poll_matchups": [],
                        "n_poll_rows": 0,
                        "status": "poll_observed_not_verified_general_ballot",
                    },
                )
                entry["poll_matchups"].append(str(matchup_id))
                entry["n_poll_rows"] += int(count)

        ballot_candidates = [
            {
                "candidate_id": race["modeled_candidate_id"],
                "candidate_name": race["modeled_candidate_name"],
                "ballot_party": race["modeled_ballot_party"],
                "caucus": race["modeled_caucus"],
                "caucus_basis": race["modeled_caucus_basis"],
                "status": "reviewed_general_modeled",
                "qualification_source": "current_candidates_2026_registry",
                "reviewed_as_of": race["reviewed_as_of"],
            },
            {
                "candidate_id": race["opposing_candidate_id"],
                "candidate_name": race["opposing_candidate_name"],
                "ballot_party": race["opposing_ballot_party"],
                "caucus": race["opposing_caucus"],
                "caucus_basis": race["opposing_caucus_basis"],
                "status": "reviewed_general_opposing",
                "qualification_source": "current_candidates_2026_registry",
                "reviewed_as_of": race["reviewed_as_of"],
            },
        ]
        reviewed_slugs = {
            str(race["modeled_candidate_id"]).split(":")[-1],
            str(race["opposing_candidate_id"]).split(":")[-1],
        }
        for slug, meta in sorted(observed.items()):
            if slug in reviewed_slugs:
                continue
            ballot_candidates.append(
                {
                    "candidate_id": f"senate-2026-{state}:{slug}",
                    "candidate_name": slug.replace("-", " ").title(),
                    "ballot_party": None,
                    "caucus": None,
                    "status": meta["status"],
                    "qualification_source": "votehub_poll_matchup_observation_only",
                    "poll_matchups": sorted(set(meta["poll_matchups"])),
                    "n_poll_rows": meta["n_poll_rows"],
                    "reviewed_as_of": race["reviewed_as_of"],
                    "note": (
                        "Observed in stored poll matchups; not verified as a "
                        "ballot-qualified general-election candidate from an "
                        "official ballot authority in-repo."
                    ),
                }
            )

        verified_general = [
            c for c in ballot_candidates if str(c.get("status", "")).startswith("reviewed_general")
        ]
        n_verified = len(verified_general)
        if n_verified >= 3:
            structure = MULTIWAY_CONTEST_STRUCTURE
            path = "multiway_plurality_adapter"
            support = historical_analog_support_report(n_analogs=0)
        else:
            structure = race["contest_structure"]
            path = "binary_non_major_adapter"
            support = {
                "probability_model_support_status": race.get("probability_model_support_status"),
                "note": (
                    "Verified general field remains binary I-vs-R; alternate "
                    "poll matchups treated as non-general / unverified."
                ),
            }

        races_out.append(
            {
                "race_id": race["race_id"],
                "state": state,
                "reviewed_as_of": race["reviewed_as_of"],
                "previous_contest_structure": race["contest_structure"],
                "verified_contest_structure": structure,
                "previous_modeling_path": "binary_non_major_adapter",
                "recommended_modeling_path": path,
                "n_verified_general_ballot_candidates": n_verified,
                "n_poll_observed_unverified_candidates": len(ballot_candidates) - n_verified,
                "ballot_candidates": ballot_candidates,
                "support": support,
                "outside_model_probabilities_used": False,
            }
        )

    return {
        "schema_version": "mt-id-contest-field-audit-v0924",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "authority": "current_candidates_2026_registry + in-repo poll matchup observation",
        "forecast_sites_as_ballot_authority": False,
        "races": races_out,
    }


def build_mt_id_poll_inclusion_audit() -> dict[str, Any]:
    registry = load_current_candidate_registry()
    wh = Warehouse(ensure_fixtures=False)
    snap = wh.build_as_of(str(registry["reviewed_as_of"]), "senate-2026")
    polls = snap.polls.copy() if len(snap.polls) else pd.DataFrame()
    raw = _poll_frame()
    timeline_meta = snap.candidate_timeline or {}
    compatible = {
        str(race_id): set(map(str, ids))
        for race_id, ids in (
            timeline_meta.get("candidate_compatible_poll_ids_by_race") or {}
        ).items()
    }
    exclusions = list(timeline_meta.get("poll_exclusions") or [])
    rows: list[dict[str, Any]] = []
    for state in TARGET_STATES:
        race = next(r for r in registry["races"] if r["state"] == state)
        race_id = str(race["race_id"])
        state_raw = raw[raw["state"].astype(str).eq(state)]
        compatible_ids = compatible.get(race_id, set())
        for _, poll in state_raw.iterrows():
            poll_id = str(poll.get("poll_id") or "")
            matchup = str(poll.get("matchup_id") or "")
            modeled = str(race["modeled_candidate_id"]).split(":")[-1]
            opposing = str(race["opposing_candidate_id"]).split(":")[-1]
            matchup_slugs = set(_matchup_candidates(matchup))
            binary_match = matchup_slugs == {modeled, opposing}
            multiway_options = len(matchup_slugs) > 2
            included = poll_id in compatible_ids
            reason = None
            if not included:
                matched_excl = [
                    item for item in exclusions
                    if str(item.get("poll_id") or "") == poll_id
                    or str(item.get("race_id") or "") == race_id
                ]
                reason = (
                    str(matched_excl[0].get("reason"))
                    if matched_excl
                    else (
                        "matchup_not_selected_current_reviewed_pair"
                        if not binary_match
                        else "excluded_by_candidate_state_contract"
                    )
                )
            rows.append(
                {
                    "race_id": race_id,
                    "state": state,
                    "poll_id": poll_id,
                    "pollster": poll.get("pollster") or poll.get("pollster_id"),
                    "field_start": str(poll.get("field_start") or ""),
                    "field_end": str(poll.get("field_end") or ""),
                    "sample_size": poll.get("sample_size"),
                    "population": poll.get("population"),
                    "matchup_id": matchup,
                    "candidates_or_options": sorted(matchup_slugs),
                    "raw_dem_share": poll.get("dem_share"),
                    "raw_rep_share": poll.get("rep_share"),
                    "two_party_margin": poll.get("two_party_margin"),
                    "included": included,
                    "model_path": "binary_non_major_adapter" if included else None,
                    "exclusion_reason": reason,
                    "binary_or_multiway": (
                        "multiway_options" if multiway_options else "binary_matchup"
                    ),
                    "matches_reviewed_general_pair": binary_match,
                    "share_renormalized_to_binary": bool(
                        included and binary_match and not multiway_options
                    ),
                    "silent_multiway_to_binary": False,
                }
            )
    return {
        "schema_version": "mt-id-poll-inclusion-audit-v0924",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "policy": (
            "Multiway observed options are not silently converted into an I-v-R "
            "binary likelihood; only the reviewed general pair is adapter-eligible."
        ),
        "rows": rows,
        "n_rows": len(rows),
        "n_included": sum(1 for row in rows if row["included"]),
    }


def attach_ballot_candidates_to_registry() -> dict[str, Any]:
    """Write ballot_candidates onto MT/ID registry rows (verified pair + observations)."""
    path = CURRENT_CANDIDATE_REGISTRY_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    audit = build_mt_id_contest_field_audit()
    by_state = {row["state"]: row for row in audit["races"]}
    for race in payload["races"]:
        state = str(race["state"])
        if state not in by_state:
            continue
        info = by_state[state]
        race["ballot_candidates"] = info["ballot_candidates"]
        race["contest_structure"] = info["verified_contest_structure"]
        if info["verified_contest_structure"] == MULTIWAY_CONTEST_STRUCTURE:
            race["ordinary_binary_target_supported"] = False
            race["exceptional_probability_model_supported"] = False
            race["probability_model_support_status"] = "unsupported"
            race["statistical_target_supported"] = False
            race["note"] = (
                "Multiway plurality general field verified; binary non-major "
                "adapter disabled; win probability fail-closed pending analog support."
            )
        else:
            suffix = (
                "ballot_candidates lists reviewed general pair plus "
                "poll-observed unverified challengers."
            )
            base = str(race.get("note") or "").replace(suffix, "").strip()
            race["note"] = f"{base} {suffix}".strip()
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
    attach_ballot_candidates_to_registry()
    return {
        "contest_field": str(field_path.relative_to(ROOT)),
        "poll_inclusion": str(poll_path.relative_to(ROOT)),
        "field": field,
        "polls": polls,
    }
