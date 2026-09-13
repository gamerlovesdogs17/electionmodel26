"""Historical poll identity + coverage (audit P0.3)."""

from __future__ import annotations

from midterms.evidence.fte_polls import map_fte_to_official_race_id, normalize_fte_senate_polls
from midterms.evidence.official_ballot import CLASS_III
from midterms.validation.poll_coverage import poll_coverage_report


def test_2022_maps_class_iii_and_ok_special():
    rid, kind = map_fte_to_official_race_id(cycle=2022, state="PA", seat_name="Class III")
    assert rid == "senate-2022-PA" and kind == "regular"
    rid_s, kind_s = map_fte_to_official_race_id(cycle=2022, state="OK", seat_name="Class II")
    assert rid_s == "senate-2022-OK-special" and kind_s == "special"
    # Wrong-class without a special is not on the ballot
    rid_bad, _ = map_fte_to_official_race_id(cycle=2022, state="DE", seat_name="Class II")
    assert rid_bad is None
    assert "PA" in CLASS_III and "DE" not in CLASS_III


def test_normalize_keeps_candidate_identity_and_drops_hypotheticals():
    from pathlib import Path

    path = Path("data/raw/external/fte_senate_polls.csv")
    if not path.exists() or path.stat().st_size < 100_000:
        return  # sealed archive required for full test
    polls = normalize_fte_senate_polls(cycles=[2022], include_hypothetical=False)
    assert len(polls) > 50
    assert polls["dem_candidate_name"].notna().mean() > 0.9
    assert "senate-2022-PA" in set(polls["race_id"].astype(str))
    assert "senate-2022-OK-special" in set(polls["race_id"].astype(str))
    # No Class-II-only DE on 2022 Class III ballot
    assert "senate-2022-DE" not in set(polls["race_id"].astype(str))


def test_2022_poll_coverage_after_normalize():
    from pathlib import Path

    from midterms.evidence.fte_polls import ingest_fte_historical_into_warehouse

    path = Path("data/raw/external/fte_senate_polls.csv")
    if not path.exists() or path.stat().st_size < 100_000:
        return
    # Normalize into warehouse if needed
    fte_path = Path("data/normalized/polls_fte_historical.parquet")
    if not fte_path.exists():
        ingest_fte_historical_into_warehouse(cycles=[2018, 2020, 2022, 2024])
    rep = poll_coverage_report(2022)
    assert rep["n_poll_rows"] > 100, rep
    assert rep["n_polls_with_candidate_identity"] > 50, rep
    # Late horizons should see real PA/OH coverage
    lead30 = rep["by_lead"]["30"]
    assert "senate-2022-PA" not in lead30.get("missing_contests", []), lead30
    assert lead30["n_polls"] > 0
