"""Wikipedia Senate ratings consensus (Cook / IE / Sabato)."""

from __future__ import annotations

from pathlib import Path

import pytest

from midterms.evidence.wiki_ratings import (
    consensus_rating,
    normalize_wiki_rating,
    parse_ratings_table_html,
    state_abbr_from_label,
    write_from_wikipedia,
)

FIXTURE_HTML = """
<table class="wikitable sortable">
<tr><th>Constituency</th><th>Incumbent</th><th>Ratings</th></tr>
<tr>
<th>State</th><th>PVI</th><th>Senator</th><th>Last election</th>
<th>Cook<br />Aug. 20, 2026</th>
<th>IE<br />Sep. 3, 2026</th>
<th>Sabato<br />Aug. 26, 2026</th>
<th>DDHQ<br />Sep. 11, 2026</th>
</tr>
<tr>
<td>Alabama</td><td>R+15</td><td>Tommy Tuberville</td><td>60.10% R</td>
<td>Solid R</td><td>Solid R</td><td>Safe R</td><td>Safe R</td>
</tr>
<tr>
<td>Georgia</td><td>R+1</td><td>Jon Ossoff</td><td>50.62% D</td>
<td>Lean D</td><td>Tilt D</td><td>Likely D</td><td>Likely D</td>
</tr>
<tr>
<td>Maine</td><td>D+4</td><td>Susan Collins</td><td>50.98% R</td>
<td>Tossup</td><td>Tilt R</td><td>Tossup</td><td>Tossup</td>
</tr>
<tr>
<td>North Carolina</td><td>R+1</td><td>Thom Tillis</td><td>48.69% R</td>
<td>Lean D (flip)</td><td>Tilt D (flip)</td><td>Lean D (flip)</td><td>Likely D</td>
</tr>
<tr>
<td>Florida (special)</td><td>R+5</td><td>Ashley Moody</td><td>Appointed</td>
<td>Solid R</td><td>Solid R</td><td>Safe R</td><td>Likely R</td>
</tr>
</table>
"""


def test_normalize_wiki_rating():
    assert normalize_wiki_rating("Safe R") == "Solid R"
    assert normalize_wiki_rating("Tilt D (flip)") == "Tilt D"
    assert normalize_wiki_rating("Tossup") == "Tossup"
    assert normalize_wiki_rating("Likely D") == "Likely D"
    assert normalize_wiki_rating("—") is None


def test_consensus_median():
    assert consensus_rating(["Lean D", "Tilt D", "Likely D"]) == "Lean D"
    assert consensus_rating(["Tossup", "Tilt R", "Tossup"]) == "Tossup"
    assert consensus_rating(["Solid R", None, "Solid R"]) == "Solid R"


def test_state_abbr():
    assert state_abbr_from_label("Florida (special)") == "FL"
    assert state_abbr_from_label("North Carolina") == "NC"


def test_parse_fixture_table():
    rows, meta = parse_ratings_table_html(FIXTURE_HTML)
    by = {r["state"]: r for r in rows}
    assert by["AL"]["rating"] == "Solid R"
    # Lean D, Tilt D, Likely D → median Lean D
    assert by["GA"]["rating"] == "Lean D"
    assert by["ME"]["rating"] == "Tossup"
    assert by["ME"]["ie"] == "Tilt R"
    assert by["NC"]["rating"] == "Lean D"
    assert by["FL"]["rating"] == "Solid R"
    assert meta["header_asofs"]["Cook"] == "2026-08-20"
    assert meta["header_asofs"]["IE"] == "2026-09-03"
    assert meta["n"] == 5


def test_live_wikipedia_fetch(tmp_path, monkeypatch):
    """Optional live fetch — skips if Wikipedia unreachable."""
    from midterms import config

    monkeypatch.setattr(config, "NORMALIZED_DIR", tmp_path / "normalized")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "MANIFESTS_DIR", tmp_path / "manifests")
    (tmp_path / "normalized").mkdir()
    (tmp_path / "raw" / "external").mkdir(parents=True)
    (tmp_path / "manifests").mkdir()
    # Re-bind module paths used by write helpers
    import midterms.evidence.expert_ratings as er
    import midterms.evidence.wiki_ratings as wr

    monkeypatch.setattr(er, "NORMALIZED_DIR", tmp_path / "normalized")
    monkeypatch.setattr(er, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(er, "MANIFESTS_DIR", tmp_path / "manifests")
    monkeypatch.setattr(wr, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(wr, "MANIFESTS_DIR", tmp_path / "manifests")
    try:
        man = write_from_wikipedia(available_at="2026-09-01")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Wikipedia unreachable: {exc}")
    assert man["n"] >= 30
    assert str(man["source"]).startswith("wikipedia")
    assert Path(man["path"]).exists()
