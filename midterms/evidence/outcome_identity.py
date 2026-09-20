"""Separate candidate, ballot-party, modeled-side, and seat-affiliation identities."""

from __future__ import annotations

from dataclasses import asdict, dataclass


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
