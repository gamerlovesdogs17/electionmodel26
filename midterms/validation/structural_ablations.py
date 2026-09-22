"""Registry for one-change-at-a-time, same-family structural ablations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from midterms.model.poll_structure import PollStructureConfig


ABLATION_REGISTRY_VERSION = "same-family-ablation-v1"


@dataclass(frozen=True)
class StructuralAblation:
    identifier: str
    changed_feature: str
    fit_overrides: dict[str, Any] = field(default_factory=dict)


STRUCTURAL_ABLATIONS: tuple[StructuralAblation, ...] = (
    StructuralAblation("hier_no_similarity", "similarity", {"include_similarity": False}),
    StructuralAblation("hier_no_terminal_race", "terminal_race", {"include_terminal_race": False}),
    StructuralAblation(
        "hier_no_study_effect", "study_effect",
        {"poll_structure": PollStructureConfig(study_effect=False)},
    ),
    StructuralAblation(
        "hier_no_sponsor_effect", "sponsor_effect",
        {"poll_structure": PollStructureConfig(sponsor_effect=False)},
    ),
    StructuralAblation(
        "hier_no_questionnaire_effect", "questionnaire_effect",
        {"poll_structure": PollStructureConfig(questionnaire_effect=False)},
    ),
)


def same_family_fit_spec(
    *, base_method: str, base_seed: int, ablation: StructuralAblation,
) -> dict[str, Any]:
    """Describe a structural ablation without changing family, data, or seed."""
    return {
        "registry_version": ABLATION_REGISTRY_VERSION,
        "ablation_id": ablation.identifier,
        "changed_feature": ablation.changed_feature,
        "method": base_method,
        "seed": int(base_seed),
        "fit_overrides": ablation.fit_overrides,
        "same_model_family": True,
        "same_seed_policy": True,
    }


def ablation_by_id(identifier: str) -> StructuralAblation:
    for item in STRUCTURAL_ABLATIONS:
        if item.identifier == identifier:
            return item
    raise KeyError(identifier)
