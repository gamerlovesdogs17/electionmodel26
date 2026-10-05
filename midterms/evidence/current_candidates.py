"""Reviewed current-cycle candidate identities, isolated from historical replay.

The registry is a present-day research input for selecting the current 2026
matchup and filtering current polls.  It deliberately has no ``available_at``
field and must never be used to reconstruct a historical as-of snapshot.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from midterms.config import ROOT

CURRENT_CANDIDATE_REGISTRY_PATH = ROOT / "data" / "current" / "current_candidates_2026.json"
CURRENT_CANDIDATE_SCHEMA_VERSION = "current-candidate-registry-v1"


def _canonical_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_current_candidate_registry(
    path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate the explicitly reviewed current candidate registry."""
    source = path or CURRENT_CANDIDATE_REGISTRY_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != CURRENT_CANDIDATE_SCHEMA_VERSION:
        raise ValueError("current candidate registry schema is missing or stale")
    if payload.get("election_id") != "senate-2026":
        raise ValueError("current candidate registry has the wrong election_id")
    if payload.get("historical_use_prohibited") is not True:
        raise ValueError("current candidate registry must prohibit historical use")
    reviewed_as_of = date.fromisoformat(str(payload.get("reviewed_as_of")))
    races = payload.get("races")
    if not isinstance(races, list) or not races:
        raise ValueError("current candidate registry has no races")
    required = {
        "race_id", "state", "modeled_candidate_id", "modeled_candidate_name",
        "modeled_ballot_party", "modeled_side", "modeled_caucus",
        "modeled_caucus_basis", "opposing_candidate_id", "opposing_candidate_name",
        "opposing_ballot_party", "opposing_side", "opposing_caucus",
        "opposing_caucus_basis", "contest_structure", "current_matchup_status",
        "reviewed_as_of", "statistical_target_supported",
        "modeled_candidate_is_incumbent", "opposing_candidate_is_incumbent",
    }
    seen: set[str] = set()
    for row in races:
        missing = sorted(required - set(row))
        if missing:
            raise ValueError(f"current candidate registry row lacks {missing}")
        race_id = str(row["race_id"])
        if race_id in seen:
            raise ValueError(f"duplicate current candidate race_id: {race_id}")
        seen.add(race_id)
        if not race_id.startswith("senate-2026-"):
            raise ValueError("current registry may contain only senate-2026 races")
        if row["current_matchup_status"] != "reviewed_current":
            raise ValueError(f"current matchup is not reviewed: {race_id}")
        if date.fromisoformat(str(row["reviewed_as_of"])) != reviewed_as_of:
            raise ValueError(f"review date differs within current registry: {race_id}")
        if not isinstance(row.get("modeled_candidate_is_incumbent"), bool):
            raise ValueError(f"modeled_candidate_is_incumbent must be bool: {race_id}")
        if not isinstance(row.get("opposing_candidate_is_incumbent"), bool):
            raise ValueError(f"opposing_candidate_is_incumbent must be bool: {race_id}")
        if (
            row["modeled_candidate_is_incumbent"]
            and row["opposing_candidate_is_incumbent"]
        ):
            raise ValueError(f"both candidates cannot be personal incumbents: {race_id}")
    payload["registry_sha256"] = _canonical_sha256(payload)
    try:
        payload["source_path"] = source.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        payload["source_path"] = source.as_posix()
    return payload


def current_registry_for_as_of(
    *,
    election_id: str,
    as_of: str | date,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Return the registry only at its explicitly reviewed current boundary.

    This hard election/as-of boundary prevents the current registry from
    entering historical cycles or an earlier 2026 replay.
    """
    if election_id != "senate-2026":
        return None
    payload = load_current_candidate_registry(path)
    cutoff = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
    if cutoff < date.fromisoformat(str(payload["reviewed_as_of"])):
        return None
    return payload


def current_candidate_rows(
    *,
    as_of: str | date,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    payload = current_registry_for_as_of(
        election_id="senate-2026", as_of=as_of, path=path,
    )
    if payload is None:
        return {}
    return {str(row["state"]): dict(row) for row in payload["races"]}
