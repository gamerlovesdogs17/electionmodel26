"""CLI entrypoints."""

from __future__ import annotations

import argparse
import json

from midterms.evidence.fixtures import build_fixtures
from midterms.evidence.ingest import merge_live_polls_into_warehouse, try_fetch_preferred
from midterms.evidence.ratings import write_normalized_ratings
from midterms.pipeline.run_forecast import replay_baselines, run_forecast


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="midterms", description="Senate probability model")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fix = sub.add_parser("build-fixtures", help="Generate synthetic evidence fixtures")
    p_fix.set_defaults(func=lambda a: print(json.dumps(build_fixtures(), indent=2)))

    p_fetch = sub.add_parser(
        "fetch-external",
        help="Fetch VoteHub polls/ratings (+ MEDSL/FTE archives when reachable)",
    )
    p_fetch.set_defaults(func=lambda a: print(json.dumps(try_fetch_preferred(), indent=2)))

    p_merge = sub.add_parser(
        "ingest-polls",
        help="Normalize VoteHub Senate polls + pollster ratings into the warehouse",
    )
    p_merge.add_argument("--election-id", default="senate-2026")
    p_merge.add_argument(
        "--keep-synthetic",
        action="store_true",
        help="Keep synthetic fixture polls for the target election (default: replace them)",
    )

    def _merge(a: argparse.Namespace) -> None:
        write_normalized_ratings()
        summary = merge_live_polls_into_warehouse(
            election_id=a.election_id,
            replace_synthetic_for_election=not a.keep_synthetic,
        )
        print(json.dumps(summary, indent=2))

    p_merge.set_defaults(func=_merge)

    p_run = sub.add_parser("forecast", help="Fit/simulate and write forecast artifact")
    p_run.add_argument("--election-id", default="senate-2026")
    p_run.add_argument("--as-of", default="2026-09-01")
    p_run.add_argument("--method", choices=["fast", "pymc"], default="fast")
    p_run.add_argument("--draws", type=int, default=400)
    p_run.add_argument("--tune", type=int, default=400)
    p_run.add_argument("--chains", type=int, default=2)
    p_run.add_argument("--seed", type=int, default=20260901)
    p_run.add_argument(
        "--generic-ballot",
        type=float,
        default=None,
        help="Dem−Rep generic ballot margin (pp). Default: VoteHub latest if available, else -1.0",
    )

    def _forecast(a: argparse.Namespace) -> None:
        gb = a.generic_ballot
        if gb is None:
            from midterms.evidence.ingest import generic_ballot_latest

            live_gb = generic_ballot_latest(as_of=a.as_of)
            gb = float(live_gb) if live_gb is not None else -1.0
        result = run_forecast(
            election_id=a.election_id,
            as_of=a.as_of,
            method=a.method,
            draws=a.draws,
            tune=a.tune,
            chains=a.chains,
            seed=a.seed,
            generic_ballot=gb,
        )
        print(
            json.dumps(
                {
                    "paths": result["paths"],
                    "chamber": result["artifact"]["chamber"],
                    "generic_ballot": gb,
                    "n_polls": result["artifact"]["snapshot"]["n_polls"],
                },
                indent=2,
            )
        )

    p_run.set_defaults(func=_forecast)

    p_rep = sub.add_parser("replay-baselines", help="Holdout as-of baseline replay")
    p_rep.add_argument("--year", type=int, default=2022)
    p_rep.set_defaults(func=lambda a: print(json.dumps(replay_baselines(a.year), indent=2)))

    p_api = sub.add_parser("serve-api", help="Serve forecast JSON API for the research UI")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=8787)

    def _serve(a: argparse.Namespace) -> None:
        import uvicorn
        from midterms.api.app import app

        uvicorn.run(app, host=a.host, port=a.port, log_level="info")

    p_api.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
