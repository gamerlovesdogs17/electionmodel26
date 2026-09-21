"""Synthetic accounting checks for the declared Independent caucus assumption."""

import numpy as np
import pandas as pd
import pytest

from midterms.evidence.outcome_identity import (
    INDEPENDENT_DEM_CAUCUSES_BASIS,
    attach_2026_ticket_identities,
    attach_declared_held_independent_caucus,
    identity_from_ticket,
    require_binary_chamber_compatibility,
    require_explicit_caucus,
)
from midterms.evidence.tickets import TICKETS_2026
from midterms.model.pymc_model import FitResult
from midterms.simulate.chamber import simulate_chamber


def test_independent_tickets_keep_ballot_identity_and_declared_caucus() -> None:
    independent = {
        state: ticket for state, ticket in TICKETS_2026.items()
        if ticket["dem_party"] == "I"
    }
    assert set(independent) == {"ID", "MT", "NE", "SD"}
    for state, ticket in independent.items():
        assert ticket["modeled_caucus"] == "D"
        assert ticket["modeled_caucus_basis"] == INDEPENDENT_DEM_CAUCUSES_BASIS
        contest = identity_from_ticket(f"synthetic-{state}", ticket)
        assert contest.contenders[0].ballot_party == "I"
        assert contest.contenders[0].caucus_affiliation == "D"
        assert contest.two_party_margin_eligible is False


def test_synthetic_race_snapshot_attaches_candidate_and_held_policy() -> None:
    rows = [{
        "race_id": f"senate-2026-{state}", "election_id": "senate-2026",
        "state": state, "not_up": False, "held_by": "R",
    } for state in ("ID", "MT", "NE", "SD")]
    rows.append({
        "race_id": "synthetic-held-i", "election_id": "senate-2026",
        "state": "ME", "not_up": True, "held_by": "I",
    })
    races = attach_2026_ticket_identities(
        attach_declared_held_independent_caucus(pd.DataFrame(rows))
    )
    active_ids = [f"senate-2026-{state}" for state in ("ID", "MT", "NE", "SD")]
    require_explicit_caucus(races, active_ids)
    require_binary_chamber_compatibility(races)
    assert races.loc[races["race_id"].isin(active_ids), "modeled_ballot_party"].eq("I").all()
    assert races.loc[races["race_id"].isin(active_ids), "modeled_caucus"].eq("D").all()
    assert races.loc[races["race_id"] == "synthetic-held-i", "held_by"].iloc[0] == "I"


def test_synthetic_held_independent_keeps_i_label_and_counts_in_d_caucus() -> None:
    rows = [{
        "race_id": "synthetic-held-i", "not_up": True, "held_by": "I",
        "state": "ZZ", "prior_lean": 0.0,
    }]
    rows += [{
        "race_id": f"synthetic-held-r-{i}", "not_up": True, "held_by": "R",
        "state": "ZZ", "prior_lean": 0.0,
    } for i in range(98)]
    rows.append({
        "race_id": "synthetic-contested-i", "not_up": False, "held_by": "R",
        "state": "ZZ", "prior_lean": 0.0, "incumbent_party": None,
        "is_open": True, "seat_class": "II", "ballot_status": "nominated",
        "election_phase": "general", "modeled_ballot_party": "I",
        "modeled_caucus": "D", "modeled_caucus_basis": INDEPENDENT_DEM_CAUCUSES_BASIS,
        "opposing_caucus": "R", "opposing_caucus_basis": "synthetic_declared_assumption",
    })
    races = attach_declared_held_independent_caucus(pd.DataFrame(rows))
    held_i = races.loc[races["race_id"] == "synthetic-held-i"].iloc[0]
    assert held_i["held_by"] == "I"
    assert held_i["held_caucus"] == "D"
    assert held_i["held_caucus_basis"] == INDEPENDENT_DEM_CAUCUSES_BASIS
    require_explicit_caucus(races, ["synthetic-contested-i"])
    require_binary_chamber_compatibility(races)

    fit = FitResult(
        race_ids=["synthetic-contested-i"], states=["ZZ"],
        mean_margin=np.array([0.0]), sd_margin=np.array([1.0]),
        draws_margin=np.array([[1.0], [-1.0]]), house_effects={},
        diagnostics={}, method="synthetic",
    )
    sim, summaries = simulate_chamber(fit, races)
    assert sim.held_ind == 1
    assert sim.seat_draws.tolist() == [2, 1]
    assert summaries[0]["dem_party"] == "I"
    assert summaries[0]["modeled_caucus"] == "D"
    assert summaries[0]["favored_party"] == "I"


def test_conflicting_held_independent_caucus_fails_closed() -> None:
    races = pd.DataFrame([{
        "race_id": "synthetic-held", "not_up": True, "held_by": "I",
        "held_caucus": "R", "held_caucus_basis": "synthetic_other_claim",
    }])
    with pytest.raises(ValueError, match="conflicting"):
        attach_declared_held_independent_caucus(races)


def test_partial_metadata_attachment_does_not_invent_held_by() -> None:
    partial = pd.DataFrame([{"race_id": "synthetic-partial"}])
    attached = attach_declared_held_independent_caucus(partial)
    assert "held_by" not in attached.columns
    assert attached["held_caucus"].isna().all()
    assert attached["held_caucus_basis"].isna().all()
    with pytest.raises(ValueError, match="chamber accounting requires complete race columns"):
        require_binary_chamber_compatibility(attached)


def test_legacy_synthetic_fixture_aligns_extended_held_schema() -> None:
    from midterms.evidence.fixtures import _chamber_held

    rows = _chamber_held(2021, {"AL"}, np.random.default_rng(7))
    assert "held_caucus" in rows.columns
    assert "held_caucus_basis" in rows.columns
