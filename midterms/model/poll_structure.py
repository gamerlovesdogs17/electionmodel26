"""Optional, shrinkage-safe poll measurement structures.

These structures are challengers.  All switches default off until a same-family
nested validation run establishes incremental value.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

MISSING_CATEGORY = "missing-row"
POLL_STRUCTURE_VERSION = "poll-structure-v2"


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

    def merged(self, overrides: dict[str, Any] | None) -> "PollStructureConfig":
        """Materialize a challenger as explicit deltas from this reference."""
        overrides = dict(overrides or {})
        valid = set(asdict(self))
        unknown = sorted(set(overrides) - valid)
        if unknown:
            raise KeyError(f"unknown poll-structure override(s): {', '.join(unknown)}")
        return replace(self, **overrides)

    @property
    def use_heuristic_study_downweight(self) -> bool:
        if self.heuristic_study_downweight is not None:
            return bool(self.heuristic_study_downweight)
        return not self.study_effect


def _stable_codes(
    values: pd.Series,
    *,
    missing_unique: bool = False,
    row_keys: pd.Series | None = None,
) -> tuple[np.ndarray, list[str]]:
    labels: list[str] = []
    for pos, value in enumerate(values.tolist()):
        if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip():
            key = str(row_keys.iloc[pos]) if row_keys is not None else str(pos)
            labels.append(f"{MISSING_CATEGORY}:{key}" if missing_unique else MISSING_CATEGORY)
        else:
            # Namespace real source identities so they cannot collide with the
            # reserved per-row missing labels.
            labels.append(f"known:{str(value).strip()}")
    levels = sorted(set(labels))
    lookup = {value: idx for idx, value in enumerate(levels)}
    return np.asarray([lookup[value] for value in labels], dtype=int), levels


def encode_poll_structure(
    polls: pd.DataFrame,
    config: PollStructureConfig | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return stable categorical IDs and poll-level indices.

    Missing sponsor, questionnaire and study IDs are deliberately unique by
    row: absent metadata is not evidence that unrelated polls share a latent
    effect. Known source identities continue to share an effect.
    """
    cfg = PollStructureConfig.coerce(config)
    frame = polls.reset_index(drop=True)

    if "poll_id" in frame.columns:
        base_keys = frame["poll_id"].fillna("").astype(str).str.strip()
    else:
        base_keys = pd.Series([""] * len(frame), dtype=str)
    # Duplicate/missing poll IDs remain separated deterministically within a
    # fixed input snapshot. The warehouse snapshot itself canonicalizes rows.
    counts: dict[str, int] = {}
    keys: list[str] = []
    for pos, value in enumerate(base_keys.tolist()):
        stem = value or f"row-{pos}"
        occurrence = counts.get(stem, 0)
        counts[stem] = occurrence + 1
        keys.append(f"{stem}#{occurrence}")
    row_keys = pd.Series(keys, dtype=str)

    def col(name: str) -> pd.Series:
        if name in frame.columns:
            return frame[name]
        return pd.Series([None] * len(frame), dtype=object)

    questionnaire = col("questionnaire_hash").copy()
    if questionnaire.isna().all() or questionnaire.astype(str).str.strip().isin({"", "None", "nan"}).all():
        questionnaire = col("question_id")
    sponsor_idx, sponsor_ids = _stable_codes(
        col("sponsor_id"), missing_unique=True, row_keys=row_keys
    )
    questionnaire_idx, questionnaire_ids = _stable_codes(
        questionnaire, missing_unique=True, row_keys=row_keys
    )
    study_idx, study_ids = _stable_codes(
        col("study_id"), missing_unique=True, row_keys=row_keys
    )
    return {
        "config": cfg,
        "sponsor_idx": sponsor_idx,
        "sponsor_ids": sponsor_ids,
        "questionnaire_idx": questionnaire_idx,
        "questionnaire_ids": questionnaire_ids,
        "study_idx": study_idx,
        "study_ids": study_ids,
        "missing_metadata_policy": "unique_row_reserved_namespace_v1",
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
