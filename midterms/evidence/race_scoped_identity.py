"""Race-scoped candidate identity: race_id + candidate_id → ballot party/caucus.

Production must never infer ballot party from a bare human-readable name when a
race-specific registry/timeline identity exists. Global name→party maps remain
legacy aliases for source matching only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from midterms.evidence.candidates import normalize_candidate_key, stable_candidate_id


@dataclass(frozen=True)
class RaceScopedCandidate:
    race_id: str
    candidate_id: str
    candidate_name: str
    ballot_party: str
    caucus: str | None = None
    aliases: tuple[str, ...] = ()


def _alias_keys(name: str, aliases: list[str] | tuple[str, ...] | None = None) -> set[str]:
    keys = {normalize_candidate_key(name)}
    for alias in aliases or ():
        keys.add(normalize_candidate_key(alias))
    return {k for k in keys if k}


def build_race_identity_index(race_rows: list[dict[str, Any]]) -> dict[str, dict[str, RaceScopedCandidate]]:
    """Map race_id → {normalized_name_or_id → RaceScopedCandidate}."""
    index: dict[str, dict[str, RaceScopedCandidate]] = {}
    for race in race_rows:
        race_id = str(race.get("race_id") or "")
        if not race_id:
            continue
        bucket = index.setdefault(race_id, {})
        candidates: list[dict[str, Any]] = []
        if isinstance(race.get("ballot_candidates"), list) and race["ballot_candidates"]:
            candidates = list(race["ballot_candidates"])
        else:
            # Principal binary pair from registry principal fields.
            for side, prefix in (("modeled", "modeled"), ("opposing", "opposing")):
                cid = race.get(f"{prefix}_candidate_id")
                cname = race.get(f"{prefix}_candidate_name")
                party = race.get(f"{prefix}_ballot_party")
                if cid and cname and party:
                    candidates.append(
                        {
                            "candidate_id": cid,
                            "candidate_name": cname,
                            "ballot_party": party,
                            "caucus": race.get(f"{prefix}_caucus"),
                            "aliases": race.get(f"{prefix}_poll_aliases") or [],
                        }
                    )
        for cand in candidates:
            cid = str(cand.get("candidate_id") or "")
            cname = str(cand.get("candidate_name") or "")
            party = str(cand.get("ballot_party") or "").upper()
            if not cid or not cname or not party:
                continue
            # Normalize historical REP/DEM labels.
            if party in {"REP", "REPUBLICAN"}:
                party = "R"
            elif party in {"DEM", "DEMOCRATIC", "DEMOCRAT"}:
                party = "D"
            elif party in {"IND", "INDEPENDENT"}:
                party = "I"
            elif party in {"LIB", "LIBERTARIAN"}:
                party = "L"
            entry = RaceScopedCandidate(
                race_id=race_id,
                candidate_id=cid,
                candidate_name=cname,
                ballot_party=party,
                caucus=(str(cand["caucus"]).upper() if cand.get("caucus") else None),
                aliases=tuple(str(a) for a in (cand.get("aliases") or cand.get("poll_aliases") or [])),
            )
            bucket[cid] = entry
            for key in _alias_keys(cname, entry.aliases):
                bucket[key] = entry
            # Also index stable source-local slug for VoteHub names.
            try:
                bucket[stable_candidate_id(cname)] = entry
            except ValueError:
                pass
    return index


def resolve_race_candidate(
    race_id: str,
    source_name: str,
    *,
    index: dict[str, dict[str, RaceScopedCandidate]],
    allow_global_fallback: bool = False,
) -> RaceScopedCandidate | None:
    """Resolve a source answer name to a race-scoped candidate.

    When unresolved, returns None (fail closed). Global name→party fallback is
    disabled for production paths unless explicitly allowed for diagnostics.
    """
    race_bucket = index.get(race_id) or {}
    key = normalize_candidate_key(source_name)
    if key in race_bucket:
        return race_bucket[key]
    try:
        sid = stable_candidate_id(source_name)
        if sid in race_bucket:
            return race_bucket[sid]
    except ValueError:
        pass
    # Substring / last-token match only within this race's candidates.
    tokens = key.split()
    last = tokens[-1] if tokens else ""
    hits = [
        entry
        for entry_key, entry in race_bucket.items()
        if ":" not in entry_key and (entry_key == last or last and last in entry_key.split())
    ]
    # Deduplicate by candidate_id
    uniq = {h.candidate_id: h for h in hits}
    if len(uniq) == 1:
        return next(iter(uniq.values()))
    if allow_global_fallback:
        from midterms.evidence.candidates import candidate_party

        party = candidate_party(source_name)
        if party:
            return RaceScopedCandidate(
                race_id=race_id,
                candidate_id=stable_candidate_id(source_name),
                candidate_name=source_name,
                ballot_party=party,
                caucus=None,
                aliases=(),
            )
    return None


def race_scoped_party(
    race_id: str,
    source_name: str,
    *,
    index: dict[str, dict[str, RaceScopedCandidate]],
) -> str | None:
    resolved = resolve_race_candidate(race_id, source_name, index=index, allow_global_fallback=False)
    return None if resolved is None else resolved.ballot_party


def load_current_race_identity_index() -> dict[str, dict[str, RaceScopedCandidate]]:
    from midterms.evidence.current_candidates import load_current_candidate_registry

    registry = load_current_candidate_registry()
    return build_race_identity_index(list(registry.get("races") or []))
