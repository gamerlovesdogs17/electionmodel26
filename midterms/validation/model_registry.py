"""Domain-neutral registry and freeze-before-truth grouped validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

Draws = Mapping[str, Sequence[float]]
Predictor = Callable[[Any], Draws]
TruthProvider = Callable[[str], Mapping[str, float]]
Scorer = Callable[[Draws, Mapping[str, float]], float]


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class RegisteredModel:
    model_id: str
    predict: Predictor
    fit_settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_id.replace("_", "").isalnum():
            raise ValueError("model_id must be a stable alphanumeric identifier")


@dataclass(frozen=True)
class FrozenModelPrediction:
    model_id: str
    outer_group: str
    lead_id: str
    case_draws: dict[str, list[float]]
    fit_settings: dict[str, Any]
    status: str
    prediction_sha256: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "outer_group": self.outer_group,
            "lead_id": self.lead_id,
            "case_draws": self.case_draws,
            "fit_settings": self.fit_settings,
            "status": self.status,
            "prediction_sha256": self.prediction_sha256,
            "error": self.error,
        }


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, RegisteredModel] = {}

    def register(self, spec: RegisteredModel) -> None:
        if spec.model_id in self._models:
            raise ValueError(f"duplicate model identifier: {spec.model_id}")
        self._models[spec.model_id] = spec

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    def freeze(self, *, outer_group: str, lead_id: str, context: Any) -> list[FrozenModelPrediction]:
        """Fit every registered model without accepting a truth argument."""
        snapshots = []
        for model_id in self.model_ids:
            spec = self._models[model_id]
            error = None
            try:
                raw = spec.predict(context)
                draws = {str(case): [float(x) for x in values] for case, values in raw.items()}
                if not draws or any(
                    len(values) < 2 or not all(isfinite(value) for value in values)
                    for values in draws.values()
                ):
                    raise ValueError("empty, undersized, or nonfinite predictive distribution")
                status = "ok"
            except Exception as exc:  # noqa: BLE001 - isolate each registered implementation
                draws = {}
                status = "failed"
                error = str(exc)
            snapshots.append(FrozenModelPrediction(
                model_id=model_id,
                outer_group=str(outer_group),
                lead_id=str(lead_id),
                case_draws=draws,
                fit_settings=dict(spec.fit_settings),
                status=status,
                prediction_sha256=_fingerprint({
                    "model_id": model_id, "outer_group": str(outer_group),
                    "lead_id": str(lead_id), "draws": draws,
                    "fit_settings": dict(spec.fit_settings), "status": status,
                    "error": error,
                }),
                error=error,
            ))
        return snapshots


def validate_grouped_oof(
    registry: ModelRegistry,
    contexts: Mapping[str, Mapping[str, Any]],
    *,
    truth_provider: TruthProvider,
    scorer: Scorer,
) -> dict[str, Any]:
    """Freeze every group/lead/model before first calling ``truth_provider``."""
    frozen: list[FrozenModelPrediction] = []
    for outer_group in sorted(contexts):
        for lead_id in sorted(contexts[outer_group]):
            frozen.extend(registry.freeze(
                outer_group=outer_group,
                lead_id=lead_id,
                context=contexts[outer_group][lead_id],
            ))
    scored: dict[str, dict[str, dict[str, Any]]] = {}
    for outer_group in sorted(contexts):
        truth = dict(truth_provider(outer_group))
        group_scores: dict[str, dict[str, Any]] = {}
        for item in frozen:
            if item.outer_group != outer_group:
                continue
            key = f"{item.lead_id}:{item.model_id}"
            if item.status == "failed":
                group_scores[key] = {"status": "failed", "error": item.error}
            else:
                try:
                    score = float(scorer(item.case_draws, truth))
                    if not isfinite(score):
                        raise ValueError("nonfinite validation score")
                    group_scores[key] = {"status": "ok", "score": score}
                except Exception as exc:  # noqa: BLE001 - preserve per-model failure
                    group_scores[key] = {"status": "failed", "error": str(exc)}
        scored[outer_group] = group_scores
    return {
        "model_ids": list(registry.model_ids),
        "freeze_before_truth": True,
        "frozen": [item.as_dict() for item in frozen],
        "scores_by_group": scored,
        "freeze_index_sha256": _fingerprint([item.as_dict() for item in frozen]),
    }
