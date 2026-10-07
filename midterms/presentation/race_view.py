"""Shared race presentation contract for ordinary, binary non-major, RCV, multiway.

Derives headline fields for UI without mutating authoritative forecast objects.
Alaska RCV remains candidate-level; binary aliases are never fabricated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CandidatePresentation:
    candidate_id: str
    candidate_name: str
    ballot_party: str
    caucus: str | None
    p_win: float | None
    share_estimate: float | None = None


@dataclass(frozen=True)
class RacePresentation:
    race_id: str
    state: str
    modeling_path: str
    contest_structure: str | None
    rating: str | None
    favored_candidate: str | None
    favored_candidate_id: str | None
    favored_party: str | None
    favored_caucus: str | None
    favored_win_probability: float | None
    principal_opponent: str | None
    principal_opponent_id: str | None
    principal_opponent_party: str | None
    principal_opponent_probability: float | None
    seat_control_p_dem_caucus: float | None
    seat_control_p_rep_caucus: float | None
    candidates: tuple[CandidatePresentation, ...] = field(default_factory=tuple)
    rcv_detail: dict[str, Any] | None = None
    unsupported_probability: bool = False
    presentation_schema: str = "race-presentation-v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [asdict(c) for c in self.candidates]
        return payload


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def present_race(race: dict[str, Any]) -> RacePresentation:
    """Build a presentation-safe view from an authoritative race forecast dict."""
    structure = race.get("contest_structure")
    modeling_path = str(race.get("modeling_path") or race.get("method") or "ordinary_stack")
    candidate_probs = list(race.get("candidate_probabilities") or [])
    is_rcv = structure == "ranked_choice_multiway" and bool(candidate_probs)
    is_multiway = structure == "multiway_plurality"
    unsupported = bool(
        race.get("probability_model_support_status") in {"unsupported", "fail_closed"}
        or race.get("win_probability_status") == "fail_closed"
    )

    candidates: list[CandidatePresentation] = []
    if candidate_probs:
        for row in sorted(candidate_probs, key=lambda item: -float(item.get("p_win") or 0.0)):
            candidates.append(
                CandidatePresentation(
                    candidate_id=str(row.get("candidate_id") or ""),
                    candidate_name=str(row.get("candidate_name") or ""),
                    ballot_party=str(row.get("ballot_party") or ""),
                    caucus=(str(row["caucus"]) if row.get("caucus") is not None else None),
                    p_win=_finite(row.get("p_win")),
                    share_estimate=_finite(
                        row.get("final_support_estimate")
                        if row.get("final_support_estimate") is not None
                        else row.get("first_choice_estimate")
                    ),
                )
            )
    else:
        modeled_name = race.get("modeled_candidate") or race.get("dem_candidate")
        opposing_name = race.get("opposing_candidate") or race.get("rep_candidate")
        if modeled_name:
            candidates.append(
                CandidatePresentation(
                    candidate_id=str(race.get("modeled_candidate_id") or ""),
                    candidate_name=str(modeled_name),
                    ballot_party=str(
                        race.get("modeled_ballot_party") or race.get("dem_party") or "D"
                    ),
                    caucus=(
                        str(race.get("modeled_caucus"))
                        if race.get("modeled_caucus") is not None
                        else None
                    ),
                    p_win=_finite(
                        race.get("p_modeled_candidate")
                        if race.get("p_modeled_candidate") is not None
                        else race.get("p_dem")
                    ),
                    share_estimate=_finite(race.get("modeled_candidate_share") or race.get("dem_share")),
                )
            )
        if opposing_name:
            candidates.append(
                CandidatePresentation(
                    candidate_id=str(race.get("opposing_candidate_id") or ""),
                    candidate_name=str(opposing_name),
                    ballot_party=str(
                        race.get("opposing_ballot_party") or race.get("rep_party") or "R"
                    ),
                    caucus=(
                        str(race.get("opposing_caucus"))
                        if race.get("opposing_caucus") is not None
                        else "R"
                    ),
                    p_win=_finite(
                        race.get("p_opposing_candidate")
                        if race.get("p_opposing_candidate") is not None
                        else race.get("p_rep")
                    ),
                    share_estimate=_finite(
                        race.get("opposing_candidate_share") or race.get("rep_share")
                    ),
                )
            )

    favored = None
    opponent = None
    if candidates:
        ranked = sorted(
            candidates,
            key=lambda item: (-1.0 if item.p_win is None else -float(item.p_win), item.candidate_id),
        )
        favored = ranked[0]
        opponent = ranked[1] if len(ranked) > 1 else None
    elif race.get("favored_candidate") or race.get("favored_candidate_id"):
        favored = CandidatePresentation(
            candidate_id=str(race.get("favored_candidate_id") or ""),
            candidate_name=str(race.get("favored_candidate") or ""),
            ballot_party=str(race.get("favored_party") or ""),
            caucus=(str(race["favored_caucus"]) if race.get("favored_caucus") is not None else None),
            p_win=None,
        )

    rcv_detail = None
    if is_rcv:
        rcv_detail = {
            "exhausted_ballot_share": _finite(race.get("exhausted_ballot_share")),
            "candidate_probabilities": [
                {
                    "candidate_id": c.candidate_id,
                    "candidate_name": c.candidate_name,
                    "ballot_party": c.ballot_party,
                    "caucus": c.caucus,
                    "p_win": c.p_win,
                    "share_estimate": c.share_estimate,
                }
                for c in candidates
            ],
            "authoritative_binary_aliases": bool(race.get("authoritative_binary_aliases")),
            "note": "Authoritative Alaska result remains candidate-level RCV.",
        }

    return RacePresentation(
        race_id=str(race.get("race_id") or ""),
        state=str(race.get("state") or ""),
        modeling_path=modeling_path,
        contest_structure=str(structure) if structure is not None else None,
        rating=str(race["rating"]) if race.get("rating") is not None else None,
        favored_candidate=favored.candidate_name if favored else None,
        favored_candidate_id=favored.candidate_id if favored else None,
        favored_party=(
            favored.ballot_party
            if favored and favored.ballot_party
            else (str(race["favored_party"]) if race.get("favored_party") is not None else None)
        ),
        favored_caucus=(
            favored.caucus
            if favored and favored.caucus is not None
            else (str(race["favored_caucus"]) if race.get("favored_caucus") is not None else None)
        ),
        favored_win_probability=None if unsupported else (favored.p_win if favored else None),
        principal_opponent=opponent.candidate_name if opponent else None,
        principal_opponent_id=opponent.candidate_id if opponent else None,
        principal_opponent_party=opponent.ballot_party if opponent else None,
        principal_opponent_probability=None if unsupported else (opponent.p_win if opponent else None),
        seat_control_p_dem_caucus=_finite(race.get("p_dem_caucus")),
        seat_control_p_rep_caucus=_finite(race.get("p_rep_caucus")),
        candidates=tuple(candidates),
        rcv_detail=rcv_detail,
        unsupported_probability=unsupported or is_multiway and race.get("p_win") is None,
    )
