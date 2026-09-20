"""Synthetic validation and vintage tests, with no external outcome data."""

from __future__ import annotations

from datetime import date

import pytest

from midterms.evidence.prior_sources import (
    PriorSource,
    available_sources,
    derive_relative_prior,
)
from midterms.validation.model_registry import (
    ModelRegistry,
    RegisteredModel,
    validate_grouped_oof,
)


def test_registry_preserves_model_identity_and_freezes_before_truth():
    events = []
    registry = ModelRegistry()

    def predictor(label):
        def predict(context):
            events.append(("freeze", label, context))
            return {"sample": [float(context), float(context + 1)]}
        return predict

    for model_id in ("pymc_static", "pymc_dynamic", "state_space", "ridge_fundamentals"):
        registry.register(RegisteredModel(model_id, predictor(model_id), {"draws": 2}))

    def broken(_context):
        raise RuntimeError("synthetic fit failure")

    registry.register(RegisteredModel("broken", broken, {"draws": 2}))

    def truth_provider(group):
        events.append(("truth", group))
        return {"sample": 0.0}

    report = validate_grouped_oof(
        registry,
        {"group_a": {"early": 1, "late": 2}, "group_b": {"early": 3}},
        truth_provider=truth_provider,
        scorer=lambda draws, truth: abs(sum(draws["sample"]) / 2 - truth["sample"]),
    )
    first_truth = next(i for i, event in enumerate(events) if event[0] == "truth")
    assert all(event[0] == "freeze" for event in events[:first_truth])
    assert report["model_ids"] == sorted(report["model_ids"])
    assert len(report["frozen"]) == 15
    assert report["scores_by_group"]["group_a"]["early:broken"]["status"] == "failed"
    assert report["scores_by_group"]["group_a"]["early:pymc_dynamic"]["status"] == "ok"
    again = validate_grouped_oof(
        registry, {"group_a": {"early": 1, "late": 2}, "group_b": {"early": 3}},
        truth_provider=lambda _group: {"sample": 0.0},
        scorer=lambda draws, truth: abs(sum(draws["sample"]) / 2 - truth["sample"]),
    )
    assert report["freeze_index_sha256"] == again["freeze_index_sha256"]
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(RegisteredModel("pymc_dynamic", predictor("duplicate")))


def _source(source_id, observation, available, local, national, kind="observed"):
    return PriorSource(
        source_id=source_id, entity_id="location_x", observation_date=observation,
        available_at=available, source_sha256="a" * 64,
        source_url="https://example.invalid/synthetic", local_value=local,
        national_reference=national, source_kind=kind,
    )


def test_relative_prior_weighting_and_complete_provenance():
    older = _source("older", date(2010, 1, 1), date(2010, 1, 2), 6.0, 4.0)
    newer = _source("newer", date(2014, 1, 1), date(2014, 1, 2), 8.0, 4.0)
    weights = {"older": 0.25, "newer": 0.75}
    result = derive_relative_prior(
        [older, newer], entity_id="location_x", as_of=date(2015, 1, 1),
        source_weights=weights,
    )
    assert result["final_prior"] == pytest.approx(3.5)
    assert result["production_eligible"] is True
    assert result == derive_relative_prior(
        [newer, older], entity_id="location_x", as_of=date(2015, 1, 1),
        source_weights=weights,
    )
    for component in result["provenance"]["components"]:
        assert {"source_election_date", "available_at", "source_sha256", "source_url",
                "national_reference", "local_value", "relative_value", "weight"} <= set(component)
    assert len(result["snapshot_sha256"]) == 64


def test_future_observation_and_late_availability_cannot_leak():
    available = _source("available", date(2010, 1, 1), date(2010, 1, 2), 3.0, 1.0)
    future_event = _source("future", date(2018, 1, 1), date(2018, 1, 2), 4.0, 1.0)
    late_release = _source("late", date(2010, 1, 1), date(2018, 1, 2), 5.0, 1.0)
    snapshot = date(2015, 1, 1)
    assert available_sources([available, future_event, late_release], as_of=snapshot) == (available,)
    for source in (future_event, late_release):
        with pytest.raises(ValueError, match="future source"):
            derive_relative_prior(
                [source], entity_id="location_x", as_of=snapshot,
                source_weights={source.source_id: 1.0},
            )


def test_fixture_fallback_is_explicit_and_not_production_eligible():
    with pytest.raises(ValueError, match="source_weights required"):
        derive_relative_prior([], entity_id="location_x", as_of=date(2015, 1, 1), source_weights={})
    fallback = derive_relative_prior(
        [], entity_id="location_x", as_of=date(2015, 1, 1),
        source_weights={}, allow_fixture_fallback=True, fixture_value=7.0,
    )
    assert fallback["method"] == "fixture_fallback"
    assert fallback["production_eligible"] is False
    assert fallback["provenance"]["source_kind"] == "synthetic_fixture"
    with pytest.raises(ValueError, match="finite"):
        derive_relative_prior(
            [], entity_id="location_x", as_of=date(2015, 1, 1),
            source_weights={}, allow_fixture_fallback=True, fixture_value=float("nan"),
        )
