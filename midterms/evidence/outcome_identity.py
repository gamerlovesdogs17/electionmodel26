"""Separate candidate, ballot-party, modeled-side, and seat-affiliation identities."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

import pandas as pd

INDEPENDENT_DEM_CAUCUSES_BASIS = "user_declared_independent_dem_caucus_assumption_2026_09_20"


@dataclass(frozen=True)
class CandidateOutcomeIdentity:
    candidate_id: str
    ballot_party: str
    modeled_side: str
    caucus_affiliation: str | None = None
    caucus_basis: str | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.ballot_party or not self.modeled_side:
            raise ValueError("candidate, ballot party, and modeled side must be explicit")
        if (self.caucus_affiliation is None) != (self.caucus_basis is None):
            raise ValueError("caucus affiliation and its evidence/assumption basis must occur together")

    def require_caucus(self) -> str:
        """Never infer caucus from party or candidate identity."""
        if self.caucus_affiliation is None:
            raise ValueError(f"caucus affiliation is unknown for {self.candidate_id}")
        return self.caucus_affiliation

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class ContestIdentity:
    contest_id: str
    contenders: tuple[CandidateOutcomeIdentity, ...]
    two_party_margin_eligible: bool = False

    def __post_init__(self) -> None:
        if not self.contest_id or len(self.contenders) < 2:
            raise ValueError("contest identity requires at least two contenders")
        ids = [candidate.candidate_id for candidate in self.contenders]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate candidate identity")
        if self.two_party_margin_eligible and {c.ballot_party for c in self.contenders} != {"D", "R"}:
            raise ValueError("two-party margin requires an explicit D/R ballot contest")

    def caucus_for_candidate(self, candidate_id: str) -> str:
        for candidate in self.contenders:
            if candidate.candidate_id == candidate_id:
                return candidate.require_caucus()
        raise KeyError(candidate_id)

    def ballot_party_for_candidate(self, candidate_id: str) -> str:
        for candidate in self.contenders:
            if candidate.candidate_id == candidate_id:
                return candidate.ballot_party
        raise KeyError(candidate_id)


def _ticket_id(race_id: str, name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = "-".join(re.findall(r"[a-z0-9]+", plain.lower()))
    if not slug:
        raise ValueError("ticket candidate name is missing")
    return f"{race_id}:{slug}"


def identity_from_ticket(race_id: str, ticket: dict[str, str | None]) -> ContestIdentity:
    """Build explicit identities from a dated ticket; never infer I caucus."""
    modeled_name = str(ticket.get("dem_name") or "").strip()
    opposing_name = str(ticket.get("rep_name") or "").strip()
    modeled_party = str(ticket.get("dem_party") or "").strip()
    modeled = CandidateOutcomeIdentity(
        candidate_id=str(ticket.get("modeled_candidate_id") or _ticket_id(race_id, modeled_name)),
        ballot_party=modeled_party, modeled_side="modeled",
        caucus_affiliation=ticket.get("modeled_caucus"),
        caucus_basis=ticket.get("modeled_caucus_basis"),
    )
    opposing = CandidateOutcomeIdentity(
        candidate_id=str(ticket.get("opposing_candidate_id")
                         or ticket.get("republican_candidate_id")
                         or _ticket_id(race_id, opposing_name)),
        ballot_party="R", modeled_side="opposing",
        caucus_affiliation=ticket.get("opposing_caucus"),
        caucus_basis=ticket.get("opposing_caucus_basis"),
    )
    return ContestIdentity(
        contest_id=race_id, contenders=(modeled, opposing),
        two_party_margin_eligible=modeled_party == "D",
    )


def attach_2026_ticket_identities(races: pd.DataFrame) -> pd.DataFrame:
    """Join curated ticket identities to active 2026 rows without touching truth."""
    from midterms.evidence.tickets import TICKETS_2026, TICKET_REGISTRY_VERSION

    out = races.copy()
    for col in (
        "modeled_candidate_id", "modeled_ballot_party", "modeled_caucus",
        "modeled_caucus_basis", "opposing_candidate_id", "opposing_ballot_party",
        "opposing_caucus", "opposing_caucus_basis", "identity_source",
        "identity_available_at", "identity_registry_version",
    ):
        if col not in out.columns:
            out[col] = None
    for index, row in out.iterrows():
        if str(row.get("election_id")) != "senate-2026" or bool(row.get("not_up")):
            continue
        # A sourced as-of identity always wins.  The undated registry is only
        # a development fallback for rows whose timeline evidence is missing.
        if str(row.get("candidate_timeline_status") or "") == "point_in_time":
            continue
        ticket = TICKETS_2026.get(str(row.get("state")))
        if ticket is None:
            continue
        contest = identity_from_ticket(str(row["race_id"]), ticket)
        modeled, opposing = contest.contenders
        updates = {
            "modeled_candidate_id": modeled.candidate_id,
            "modeled_ballot_party": modeled.ballot_party,
            "modeled_caucus": modeled.caucus_affiliation,
            "modeled_caucus_basis": modeled.caucus_basis,
            "opposing_candidate_id": opposing.candidate_id,
            "opposing_ballot_party": opposing.ballot_party,
            "opposing_caucus": opposing.caucus_affiliation,
            "opposing_caucus_basis": opposing.caucus_basis,
            "identity_source": "dated_ticket_registry",
            "identity_available_at": None,
            "identity_registry_version": TICKET_REGISTRY_VERSION,
        }
        for key, value in updates.items():
            out.at[index, key] = value
    return out


def attach_declared_held_independent_caucus(races: pd.DataFrame) -> pd.DataFrame:
    """Apply the explicit model assumption to held I seats; retain ``held_by=I``.

    This is a chamber-accounting assumption, not an assertion that any named
    Independent has declared a real-world caucus affiliation.
    """
    out = races.copy()
    for column in ("held_caucus", "held_caucus_basis"):
        if column not in out.columns:
            out[column] = None
    if "not_up" not in out.columns or "held_by" not in out.columns:
        return out
    held_independent = out["not_up"].fillna(False).astype(bool) & out["held_by"].eq("I")
    if held_independent.any():
        existing = out.loc[held_independent, "held_caucus"]
        incompatible = existing.notna() & existing.ne("D")
        if incompatible.any():
            raise ValueError("held Independent has a conflicting explicit caucus")
        out.loc[held_independent, "held_caucus"] = "D"
        out.loc[held_independent, "held_caucus_basis"] = INDEPENDENT_DEM_CAUCUSES_BASIS
    return out


def require_explicit_caucus(races: pd.DataFrame, race_ids: list[str]) -> None:
    """Fail before seat accounting if an active candidate lacks caucus metadata."""
    by_id = races.set_index("race_id")
    for race_id in race_ids:
        if race_id not in by_id.index:
            raise ValueError(f"contested identity missing: {race_id}")
        row = by_id.loc[race_id]
        for side in ("modeled", "opposing"):
            if pd.isna(row.get(f"{side}_caucus")) or pd.isna(row.get(f"{side}_caucus_basis")):
                raise ValueError(f"explicit {side} caucus metadata missing: {race_id}")
    held = races[races["not_up"]] if "not_up" in races.columns else races.head(0)
    for _, row in held.iterrows():
        if str(row.get("held_by")) == "I" and (
            pd.isna(row.get("held_caucus")) or pd.isna(row.get("held_caucus_basis"))
        ):
            raise ValueError(f"held Independent caucus metadata missing: {row['race_id']}")


def require_binary_chamber_compatibility(races: pd.DataFrame) -> None:
    """Reject nonstandard affiliations the current two-caucus engine cannot account for."""
    if "not_up" not in races.columns:
        raise ValueError(
            "chamber accounting requires complete race columns: not_up"
        )
    has_held_seats = races["not_up"].fillna(False).astype(bool).any()
    if has_held_seats and "held_by" not in races.columns:
        raise ValueError("chamber accounting requires complete race columns: held_by")
    for _, row in races.iterrows():
        race_id = str(row.get("race_id"))
        if bool(row.get("not_up")):
            if str(row.get("held_by")) == "I" and (
                row.get("held_caucus") != "D" or pd.isna(row.get("held_caucus_basis"))
            ):
                raise ValueError(f"held Independent lacks supported explicit caucus: {race_id}")
            continue
        party = row.get("modeled_ballot_party")
        if pd.isna(party):
            party = row.get("dem_party")
        if pd.isna(party) or str(party) == "D":
            continue
        if (row.get("modeled_caucus") != "D"
                or row.get("opposing_caucus") != "R"
                or pd.isna(row.get("modeled_caucus_basis"))
                or pd.isna(row.get("opposing_caucus_basis"))):
            raise ValueError(f"nonstandard contest lacks supported explicit caucus mapping: {race_id}")
