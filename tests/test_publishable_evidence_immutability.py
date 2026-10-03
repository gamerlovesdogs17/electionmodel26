"""Synthetic guardrails for sealed evidence during publication fitting."""

from __future__ import annotations

import importlib

import pytest


def test_evidence_refresh_policy_is_read_only_for_publication_and_rebuild() -> None:
    module = importlib.import_module("midterms.pipeline.run_forecast")

    assert module._evidence_refresh_allowed(
        require_publishable=False, rebuild_mode=False,
    )
    assert not module._evidence_refresh_allowed(
        require_publishable=True, rebuild_mode=False,
    )
    assert not module._evidence_refresh_allowed(
        require_publishable=False, rebuild_mode=True,
    )


def test_publishable_forecast_does_not_mutate_verified_evidence(monkeypatch) -> None:
    module = importlib.import_module("midterms.pipeline.run_forecast")
    eligibility = importlib.import_module("midterms.evidence.eligibility")
    overlay_validation = importlib.import_module("midterms.validation.overlay_validation")
    approval = importlib.import_module("midterms.evidence.approval")
    demography = importlib.import_module("midterms.evidence.demography")
    economics = importlib.import_module("midterms.evidence.economics")
    expert_ratings = importlib.import_module("midterms.evidence.expert_ratings")
    fec = importlib.import_module("midterms.evidence.fec")
    markets = importlib.import_module("midterms.evidence.markets")
    peers = importlib.import_module("midterms.evidence.peers")

    mutation_calls: list[str] = []

    def forbidden(name):
        def call(*args, **kwargs):
            mutation_calls.append(name)
            raise AssertionError(f"publication fit attempted evidence mutation: {name}")

        return call

    monkeypatch.setattr(approval, "write_approval_store", forbidden("approval"))
    monkeypatch.setattr(demography, "write_demography_store", forbidden("demography"))
    monkeypatch.setattr(economics, "try_refresh_alfred", forbidden("economics"))
    monkeypatch.setattr(
        expert_ratings, "ensure_expert_ratings_store", forbidden("expert_ratings")
    )
    monkeypatch.setattr(fec, "write_finance_store", forbidden("finance"))
    monkeypatch.setattr(markets, "write_markets_store", forbidden("markets"))
    monkeypatch.setattr(peers, "write_peer_snapshots", forbidden("peers"))
    monkeypatch.setattr(
        overlay_validation,
        "publication_overlay_policy",
        lambda **kwargs: {
            "policy": "synthetic",
            "use_ratings": False,
            "use_race_markets": False,
            "use_control_market": False,
        },
    )
    monkeypatch.setattr(
        eligibility, "effective_production_domain_contract", lambda **kwargs: {}
    )
    monkeypatch.setattr(
        eligibility,
        "assert_publishable",
        lambda *args, **kwargs: {
            "publishable": True, "run_class": "publication", "reasons": [],
        },
    )

    class StopAfterEvidenceBoundary:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("stop after evidence refresh boundary")

    monkeypatch.setattr(module, "Warehouse", StopAfterEvidenceBoundary)
    with pytest.raises(RuntimeError, match="refresh boundary"):
        module.run_forecast(
            election_id="synthetic-election",
            as_of="2026-09-27",
            require_publishable=True,
            with_ratings=False,
            with_markets=False,
        )
    assert mutation_calls == []
