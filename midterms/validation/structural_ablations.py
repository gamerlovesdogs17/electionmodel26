"""Registry for one-change-at-a-time, same-family structural ablations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from midterms.model.poll_structure import PollStructureConfig

ABLATION_REGISTRY_VERSION = "same-family-ablation-v2"


@dataclass(frozen=True)
class StructuralAblation:
    identifier: str
    changed_feature: str
    fit_overrides: dict[str, Any] = field(default_factory=dict)
    poll_structure_overrides: dict[str, Any] = field(default_factory=dict)


STRUCTURAL_ABLATIONS: tuple[StructuralAblation, ...] = (
    StructuralAblation("hier_no_similarity", "similarity", {"include_similarity": False}),
    StructuralAblation("hier_no_terminal_race", "terminal_race", {"include_terminal_race": False}),
    StructuralAblation(
        "hier_no_study_effect", "study_effect",
        poll_structure_overrides={"study_effect": False},
    ),
    StructuralAblation(
        "hier_no_sponsor_effect", "sponsor_effect",
        poll_structure_overrides={"sponsor_effect": False},
    ),
    StructuralAblation(
        "hier_no_questionnaire_effect", "questionnaire_effect",
        poll_structure_overrides={"questionnaire_effect": False},
    ),
)


def same_family_fit_spec(
    *, base_method: str, base_seed: int, ablation: StructuralAblation,
    reference_poll_structure: PollStructureConfig | dict[str, Any] | None = None,
    reference_fit_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize and verify one declared delta from a reference fit."""
    reference_poll = PollStructureConfig.coerce(reference_poll_structure)
    challenger_poll = reference_poll.merged(ablation.poll_structure_overrides)
    reference = {
        "include_similarity": True,
        "include_terminal_race": True,
        **dict(reference_fit_config or {}),
        "poll_structure": reference_poll.to_dict(),
    }
    challenger = {
        **reference,
        **dict(ablation.fit_overrides),
        "poll_structure": challenger_poll.to_dict(),
    }
    changed: list[str] = []
    for key in ("include_similarity", "include_terminal_race"):
        if reference.get(key) != challenger.get(key):
            changed.append("similarity" if key == "include_similarity" else "terminal_race")
    for key in PollStructureConfig.__dataclass_fields__:
        if getattr(reference_poll, key) != getattr(challenger_poll, key):
            changed.append(key)
    is_noop = not changed
    eligible = changed == [ablation.changed_feature]
    return {
        "registry_version": ABLATION_REGISTRY_VERSION,
        "ablation_id": ablation.identifier,
        "changed_feature": ablation.changed_feature,
        "method": base_method,
        "seed": int(base_seed),
        "fit_overrides": dict(ablation.fit_overrides),
        "poll_structure_overrides": dict(ablation.poll_structure_overrides),
        "reference_config": reference,
        "challenger_config": challenger,
        "changed_features": changed,
        "is_noop": is_noop,
        "eligible": eligible,
        "ineligible_reason": (
            "declared feature is already disabled in the reference" if is_noop else
            None if eligible else "challenger changed more than its declared feature"
        ),
        "same_model_family": True,
        "same_seed_policy": True,
    }


def ablation_by_id(identifier: str) -> StructuralAblation:
    for item in STRUCTURAL_ABLATIONS:
        if item.identifier == identifier:
            return item
    raise KeyError(identifier)
