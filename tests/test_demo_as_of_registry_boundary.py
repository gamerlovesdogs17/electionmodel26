"""DEMO_AS_OF must not precede the reviewed current-candidate registry boundary."""

from __future__ import annotations

from datetime import date

from midterms.config import DEMO_AS_OF, DEMO_ELECTION_ID
from midterms.evidence.current_candidates import (
    current_registry_for_as_of,
    load_current_candidate_registry,
)
from midterms.evidence.eligibility import candidate_timeline_freshness
from midterms.evidence.outcome_identity import require_explicit_caucus
from midterms.evidence.warehouse import Warehouse


def test_demo_as_of_is_on_or_after_current_registry_review() -> None:
    registry = load_current_candidate_registry()
    reviewed = date.fromisoformat(str(registry["reviewed_as_of"]))
    demo = date.fromisoformat(DEMO_AS_OF)
    assert demo >= reviewed, (
        f"DEMO_AS_OF={DEMO_AS_OF} precedes current registry reviewed_as_of="
        f"{registry['reviewed_as_of']}; default forecasts would lack caucus metadata"
    )
    assert current_registry_for_as_of(
        election_id=DEMO_ELECTION_ID, as_of=DEMO_AS_OF,
    ) is not None


def test_demo_as_of_warehouse_snapshot_has_explicit_caucus() -> None:
    snap = Warehouse(ensure_fixtures=False).build_as_of(DEMO_AS_OF, DEMO_ELECTION_ID)
    active = (
        snap.races.loc[~snap.races["not_up"].fillna(False), "race_id"]
        .astype(str)
        .tolist()
    )
    require_explicit_caucus(snap.races, active)


def test_registry_resolved_snapshot_marks_identity_freshness_clock() -> None:
    """Mutation-safe: do not re-audit finance/economics publishability here.

    Forecast tests in the same job may rewrite sealed stores; this only checks
    that the warehouse candidate-state metadata carries the registry review
    clock used by publication freshness. Sealed publishability is asserted by
    the workflow step that reads evidence_eligibility_latest.json before pytest.
    """
    snap = Warehouse(ensure_fixtures=False).build_as_of(DEMO_AS_OF, DEMO_ELECTION_ID)
    meta = snap.candidate_timeline or {}
    assert meta.get("identity_freshness_basis") == (
        "reviewed_current_candidate_registry"
    )
    assert meta.get("latest_retrieved_at")
    audit = {
        "eligible": True,
        "conditional_identity_contract": True,
        "n_identity_required_and_resolved": int(
            (meta.get("counts") or {}).get("identity_required_and_resolved") or 0
        ),
        "n_identity_required_and_missing": 0,
        "classification_records": meta.get("classification_records") or [],
    }
    freshness = candidate_timeline_freshness(
        audit, meta, checked_at=f"{DEMO_AS_OF}T12:00:00Z",
    )
    assert freshness["status"] == "fresh", freshness
    assert freshness.get("identity_freshness_basis") == (
        "reviewed_current_candidate_registry"
    )
