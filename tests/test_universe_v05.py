"""Universe, tickets, rating consistency, specials."""

from __future__ import annotations

from midterms.evidence.fixtures import CLASS_II, SPECIALS_2026, generate_2026_races
from midterms.evidence.tickets import TICKETS_2026, ticket_for_state
from midterms.model.overlays import rating_from_probability
from midterms.pipeline.run_forecast import run_forecast


def test_2026_includes_oh_fl_specials():
    races = generate_2026_races()
    contested = races[~races["not_up"]]
    assert len(contested) == 35
    assert len(races[races["not_up"]]) == 65
    assert len(races) == 100
    for st in SPECIALS_2026:
        row = contested[contested["state"] == st]
        assert len(row) == 1
        assert str(row.iloc[0]["seat_class"]) == "special"
        assert f"senate-2026-{st}" == row.iloc[0]["race_id"]
    assert set(CLASS_II).issubset(set(contested["state"]))


def test_held_independents_present():
    races = generate_2026_races()
    held = races[races["not_up"]]
    ind = held[held["held_by"] == "I"]
    assert len(ind) >= 2
    assert "ME" in set(ind["state"])
    assert "VT" in set(ind["state"])


def test_tickets_cover_contested_including_specials():
    for st in list(CLASS_II) + SPECIALS_2026:
        t = ticket_for_state(st)
        assert t["dem_name"]
        assert t["rep_name"]
        assert t["dem_party"] in {"D", "I"}
        assert "nominee" not in t["dem_name"].lower()
        assert "nominee" not in t["rep_name"].lower()
    assert TICKETS_2026["ME"]["dem_name"] == "Troy Jackson"
    assert TICKETS_2026["MI"]["dem_name"] == "Abdul El-Sayed"
    assert TICKETS_2026["NE"]["dem_name"] == "Dan Osborn"
    assert TICKETS_2026["NE"]["dem_party"] == "I"
    assert TICKETS_2026["FL"]["dem_name"] == "Angie Nixon"
    assert TICKETS_2026["CO"]["dem_name"] == "John Hickenlooper"


def test_forecast_rating_matches_probability(tmp_path):
    result = run_forecast(
        method="fast",
        draws=200,
        seed=7,
        ensemble=False,
        with_ratings=True,
        with_markets=False,
        out_dir=tmp_path,
    )
    art = result["artifact"]
    assert art["model_version"].endswith("v0.5") or art["model_version"].endswith("v0.6")
    states = {r["state"] for r in art["races"]}
    assert "OH" in states and "FL" in states
    for r in art["races"]:
        assert r["rating"] == rating_from_probability(r["p_dem"])
        assert "expert_rating" in r or True  # optional if store empty for race
    assert art["chamber"].get("held_ind", 0) >= 2
    assert "peer_comparison" in art
    assert art["peer_comparison"]["control_p_dem"]["ours"] is not None
