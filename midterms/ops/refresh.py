"""Scheduled / on-demand evidence refresh + forecast + monitor chain."""

from __future__ import annotations

import json
from typing import Any


def run_refresh(
    *,
    election_id: str = "senate-2026",
    as_of: str = "2026-09-01",
    forecast: bool = True,
    draws: int = 400,
) -> dict[str, Any]:
    """
    Blueprint §11 ops lite: fetch → ingest → forecast → monitor.
    Intended for cron / GitHub Actions / manual `midterms refresh`.
    """
    steps: dict[str, Any] = {}

    from midterms.evidence.ingest import merge_live_polls_into_warehouse, try_fetch_preferred
    from midterms.evidence.ratings import write_normalized_ratings
    from midterms.evidence.results_archive import write_results_archive

    steps["fetch"] = try_fetch_preferred()
    steps["ratings"] = write_normalized_ratings()
    steps["results_archive"] = write_results_archive()
    try:
        steps["votehub_ccby"] = __import__(
            "midterms.evidence.votehub_archive", fromlist=["ingest_votehub_dumps_to_warehouse"]
        ).ingest_votehub_dumps_to_warehouse()
    except Exception as exc:  # noqa: BLE001
        steps["votehub_ccby"] = {"ok": False, "error": str(exc)}
    try:
        steps["licensed_ratings"] = __import__(
            "midterms.evidence.licensed_ratings", fromlist=["try_ingest_licensed_ratings"]
        ).try_ingest_licensed_ratings(election_id=election_id, available_at=as_of)
    except Exception as exc:  # noqa: BLE001
        steps["licensed_ratings"] = {"ok": False, "error": str(exc)}
    try:
        steps["markets"] = __import__(
            "midterms.evidence.markets", fromlist=["write_markets_store"]
        ).write_markets_store(election_id=election_id, available_at=as_of)
    except Exception as exc:  # noqa: BLE001
        steps["markets"] = {"ok": False, "error": str(exc)}
    try:
        # Prefer licensed CSV when present; else curated snapshot
        lic = steps.get("licensed_ratings") or {}
        if not lic.get("licensed_present"):
            steps["expert_ratings"] = __import__(
                "midterms.evidence.expert_ratings", fromlist=["write_expert_ratings_store"]
            ).write_expert_ratings_store(election_id=election_id, available_at=as_of)
        else:
            steps["expert_ratings"] = {"ok": True, "source": "licensed"}
    except Exception as exc:  # noqa: BLE001
        steps["expert_ratings"] = {"ok": False, "error": str(exc)}

    steps["ingest"] = merge_live_polls_into_warehouse(
        election_id=election_id,
        replace_synthetic_for_election=True,
    )

    if forecast:
        from midterms.evidence.ingest import generic_ballot_latest
        from midterms.pipeline.run_forecast import run_forecast

        gb = generic_ballot_latest(as_of=as_of)
        steps["forecast"] = run_forecast(
            election_id=election_id,
            as_of=as_of,
            method="fast",
            draws=draws,
            seed=20260901,
            generic_ballot=float(gb) if gb is not None else -1.0,
        )["paths"]

    from midterms.ops.monitor import monitor_check

    steps["monitor"] = monitor_check()
    return steps


def main() -> None:
    print(json.dumps(run_refresh(), indent=2, default=str))


if __name__ == "__main__":
    main()
