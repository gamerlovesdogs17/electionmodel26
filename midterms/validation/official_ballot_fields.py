"""Authoritative 2026 general-election ballot fields (not the current registry)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from midterms.config import ROOT

OFFICIAL_BALLOT_FIELDS_PATH = ROOT / "data" / "current" / "official_ballot_fields_2026.json"


def load_official_ballot_fields(path: Path | None = None) -> dict[str, Any]:
    source = path or OFFICIAL_BALLOT_FIELDS_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("registry_used_as_ballot_authority") is not False:
        raise ValueError("official ballot fields must not treat the registry as authority")
    if payload.get("election_id") != "senate-2026":
        raise ValueError("official ballot fields have the wrong election_id")
    races = payload.get("races")
    if not isinstance(races, dict) or not races:
        raise ValueError("official ballot fields missing races")
    return payload


def official_race(state: str, *, path: Path | None = None) -> dict[str, Any]:
    payload = load_official_ballot_fields(path)
    race = payload["races"].get(state)
    if not isinstance(race, dict):
        raise KeyError(f"no official ballot field for state {state}")
    return race


def certified_candidates(state: str, *, path: Path | None = None) -> list[dict[str, Any]]:
    race = official_race(state, path=path)
    return [
        dict(row)
        for row in (race.get("candidates") or [])
        if str(row.get("status")) == "certified_general_ballot" and not row.get("withdrawn")
    ]
