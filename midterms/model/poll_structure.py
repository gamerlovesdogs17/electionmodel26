"""Optional, shrinkage-safe poll measurement structures.

These structures are challengers.  All switches default off until a same-family
nested validation run establishes incremental value.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


MISSING_CATEGORY = "__unknown__"
POLL_STRUCTURE_VERSION = "poll-structure-v1"


@dataclass(frozen=True)
class PollStructureConfig:
    sponsor_effect: bool = False
    questionnaire_effect: bool = False
    study_effect: bool = False
    heuristic_study_downweight: bool | None = None
    sponsor_scale: float = 0.75
    questionnaire_scale: float = 0.75
    study_scale: float = 1.25

    @classmethod
    def coerce(cls, value: "PollStructureConfig | dict[str, Any] | None") -> "PollStructureConfig":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": POLL_STRUCTURE_VERSION,
            **asdict(self),
            "effective_heuristic_study_downweight": self.use_heuristic_study_downweight,
        }

    @property
    def use_heuristic_study_downweight(self) -> bool:
        if self.heuristic_study_downweight is not None:
            return bool(self.heuristic_study_downweight)
        return not self.study_effect


def _stable_codes(values: pd.Series, *, missing_unique: bool = False) -> tuple[np.ndarray, list[str]]:
    labels: list[str] = []
    for pos, value in enumerate(values.tolist()):
        if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip():
            labels.append(f"{MISSING_CATEGORY}:{pos}" if missing_unique else MISSING_CATEGORY)
        else:
            labels.append(str(value).strip())
    levels = sorted(set(labels))
    lookup = {value: idx for idx, value in enumerate(levels)}
    return np.asarray([lookup[value] for value in labels], dtype=int), levels


def encode_poll_structure(
    polls: pd.DataFrame,
    config: PollStructureConfig | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return stable categorical IDs and poll-level indices.

    Missing study IDs are deliberately unique by row: unrelated polls with
    absent metadata must not acquire a shared latent study shock.
    """
    cfg = PollStructureConfig.coerce(config)
    frame = polls.reset_index(drop=True)

    def col(name: str) -> pd.Series:
        if name in frame.columns:
            return frame[name]
        return pd.Series([None] * len(frame), dtype=object)

    questionnaire = col("questionnaire_hash").copy()
    if questionnaire.isna().all() or questionnaire.astype(str).str.strip().isin({"", "None", "nan"}).all():
        questionnaire = col("question_id")
    sponsor_idx, sponsor_ids = _stable_codes(col("sponsor_id"))
    questionnaire_idx, questionnaire_ids = _stable_codes(questionnaire)
    study_idx, study_ids = _stable_codes(col("study_id"), missing_unique=True)
    return {
        "config": cfg,
        "sponsor_idx": sponsor_idx,
        "sponsor_ids": sponsor_ids,
        "questionnaire_idx": questionnaire_idx,
        "questionnaire_ids": questionnaire_ids,
        "study_idx": study_idx,
        "study_ids": study_ids,
    }


def add_optional_poll_effects(pm: Any, prep: dict[str, Any], config: PollStructureConfig) -> tuple[Any, dict[str, str]]:
    """Create identifiable hierarchical effects and return a poll-level offset."""
    n_polls = len(prep["poll_y"])
    offset: Any = np.zeros(n_polls, dtype=float)
    active: dict[str, str] = {}

    def centered_effect(prefix: str, scale: float, dim: str, idx: np.ndarray) -> Any:
        sigma = pm.HalfNormal(f"sigma_{prefix}", scale)
        raw = pm.Normal(f"{prefix}_raw", 0.0, 1.0, dims=dim)
        effect = pm.Deterministic(f"{prefix}_eff", sigma * (raw - pm.math.mean(raw)), dims=dim)
        return effect[idx]

    if config.sponsor_effect and prep["sponsor_ids"]:
        offset = offset + centered_effect(
            "sponsor", config.sponsor_scale, "sponsor", prep["poll_sponsor"]
        )
        active["sponsor"] = "hierarchical_zero_centered"
    if config.questionnaire_effect and prep["questionnaire_ids"]:
        offset = offset + centered_effect(
            "questionnaire", config.questionnaire_scale, "questionnaire", prep["poll_questionnaire"]
        )
        active["questionnaire"] = "hierarchical_zero_centered"
    if config.study_effect and prep["study_ids"]:
        sigma_study = pm.HalfNormal("sigma_study", config.study_scale)
        study = pm.Normal("study_eff", 0.0, sigma_study, dims="study")
        offset = offset + study[prep["poll_study"]]
        active["study"] = "shared_latent_deviation"
    return offset, active
