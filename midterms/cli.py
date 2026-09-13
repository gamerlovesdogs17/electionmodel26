"""CLI entrypoints."""

from __future__ import annotations

import argparse
import json

from midterms.evidence.fixtures import build_fixtures
from midterms.evidence.ingest import merge_live_polls_into_warehouse, try_fetch_preferred
from midterms.evidence.ratings import write_normalized_ratings
from midterms.pipeline.run_forecast import replay_baselines, run_forecast
from midterms.validation.cycle_replay import replay_all_cycles, replay_cycle


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

    p_econ = sub.add_parser("fetch-economics", help="Build ALFRED/fixture economic vintage store")
    p_econ.set_defaults(
        func=lambda a: print(
            json.dumps((__import__("midterms.evidence.economics", fromlist=["try_refresh_alfred"]).try_refresh_alfred()), indent=2)
        )
    )

    p_fec = sub.add_parser("fetch-finance", help="Fetch OpenFEC Senate totals → fundraising shares")
    p_fec.add_argument("--cycle", type=int, default=2026)
    p_fec.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__("midterms.evidence.fec", fromlist=["write_finance_store"]).write_finance_store(
                    cycle=a.cycle
                ),
                indent=2,
            )
        )
    )

    p_mkt = sub.add_parser("fetch-markets", help="Fetch Kalshi Senate race + control markets")
    p_mkt.add_argument("--election-id", default="senate-2026")
    p_mkt.add_argument("--as-of", default=None, help="Stamp available_at for as-of filtering")
    p_mkt.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.evidence.markets", fromlist=["write_markets_store"]
                ).write_markets_store(election_id=a.election_id, available_at=a.as_of),
                indent=2,
            )
        )
    )

    p_rat = sub.add_parser(
        "fetch-ratings",
        help="Write expert ratings (Wikipedia Cook/IE/Sabato consensus by default)",
    )
    p_rat.add_argument("--election-id", default="senate-2026")
    p_rat.add_argument("--as-of", default=None, help="Override available_at (default: wiki header dates)")
    p_rat.add_argument("--csv", default=None, help="Optional CSV with state,rating columns")
    p_rat.add_argument(
        "--curated",
        action="store_true",
        help="Use curated research snapshot instead of Wikipedia",
    )
    p_rat.add_argument(
        "--wikipedia",
        action="store_true",
        help="Force Wikipedia Predictions table ingest (default when no --csv/--curated)",
    )

    def _fetch_ratings(a: argparse.Namespace) -> None:
        if a.csv:
            man = __import__(
                "midterms.evidence.expert_ratings", fromlist=["write_expert_ratings_store"]
            ).write_expert_ratings_store(
                election_id=a.election_id,
                available_at=a.as_of or "2026-09-01",
                csv_path=a.csv,
            )
        elif a.curated:
            man = __import__(
                "midterms.evidence.expert_ratings", fromlist=["write_expert_ratings_store"]
            ).write_expert_ratings_store(
                election_id=a.election_id,
                available_at=a.as_of or "2026-09-01",
            )
        else:
            man = __import__(
                "midterms.evidence.wiki_ratings", fromlist=["write_from_wikipedia"]
            ).write_from_wikipedia(
                election_id=a.election_id,
                available_at=a.as_of,
            )
        print(json.dumps(man, indent=2))

    p_rat.set_defaults(func=_fetch_ratings)

    p_peer = sub.add_parser("fetch-peers", help="Write peer forecast comparison snapshots")
    p_peer.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.evidence.peers", fromlist=["write_peer_snapshots"]
                ).write_peer_snapshots(),
                indent=2,
            )
        )
    )

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
    p_run.add_argument("--method", choices=["fast", "pymc", "state_space"], default="fast")
    p_run.add_argument("--draws", type=int, default=400)
    p_run.add_argument("--tune", type=int, default=400)
    p_run.add_argument("--chains", type=int, default=2)
    p_run.add_argument("--seed", type=int, default=20260901)
    p_run.add_argument(
        "--generic-ballot",
        type=float,
        default=None,
        help="Dem-Rep generic ballot margin (pp). Default: VoteHub latest if available, else -1.0",
    )
    p_run.add_argument(
        "--no-ensemble",
        action="store_true",
        help="Disable out-of-fold stack mixture (hierarchical core only)",
    )
    p_run.add_argument(
        "--no-ratings",
        action="store_true",
        help="Disable expert-rating overlay (on by default when ratings exist)",
    )
    p_run.add_argument(
        "--no-markets",
        action="store_true",
        help="Disable Kalshi market overlay (on by default when markets exist)",
    )
    p_run.add_argument("--rating-weight", type=float, default=0.15)
    p_run.add_argument("--market-weight", type=float, default=0.10)

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
            ensemble=not a.no_ensemble,
            with_ratings=not a.no_ratings,
            with_markets=not a.no_markets,
            rating_weight=a.rating_weight,
            market_weight=a.market_weight,
        )
        print(
            json.dumps(
                {
                    "paths": result["paths"],
                    "chamber": result["artifact"]["chamber"],
                    "generic_ballot": gb,
                    "method": result["artifact"]["method"],
                    "stack_weights": result["artifact"].get("stack_weights"),
                    "diagnostics": {
                        k: result["artifact"]["diagnostics"].get(k)
                        for k in ("enop_global", "enop_by_race_mean", "n_polls", "ensemble")
                    },
                    "n_polls": result["artifact"]["snapshot"]["n_polls"],
                },
                indent=2,
            )
        )

    p_run.set_defaults(func=_forecast)

    p_rep = sub.add_parser("replay-baselines", help="Holdout as-of baseline replay")
    p_rep.add_argument("--year", type=int, default=2022)
    p_rep.set_defaults(func=lambda a: print(json.dumps(replay_baselines(a.year), indent=2)))

    p_cycle = sub.add_parser(
        "replay-cycle",
        help="Complete-cycle replay of baselines + hierarchical model (proper scores)",
    )
    p_cycle.add_argument("--year", type=int, default=2022)
    p_cycle.add_argument("--all", action="store_true", help="Replay every historical cycle")

    def _cycle(a: argparse.Namespace) -> None:
        if a.all:
            summary = replay_all_cycles()
            print(
                json.dumps(
                    {
                        "path": summary.get("path"),
                        "mean_crps_by_model": summary.get("mean_crps_by_model"),
                        "stack_weights": summary.get("stack_weights"),
                        "cycles": summary.get("cycles"),
                    },
                    indent=2,
                )
            )
        else:
            report = replay_cycle(a.year)
            from midterms.config import ARTIFACTS_DIR

            out = ARTIFACTS_DIR / f"cycle_replay_{a.year}.json"
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2))
            print(
                json.dumps(
                    {
                        "path": str(out),
                        "aggregate": report.get("aggregate"),
                        "stack_weights": report.get("stack_weights"),
                        "chamber": report.get("chamber"),
                        "overlay_ablation": report.get("overlay_ablation"),
                    },
                    indent=2,
                )
            )

    p_cycle.set_defaults(func=_cycle)

    p_fund = sub.add_parser(
        "ablate-fundamentals",
        help="Nested leave-one-cycle drop-one COEF ablation (CRPS)",
    )
    p_fund.add_argument(
        "--primary-only",
        action="store_true",
        help="Only PRIMARY_HOLDOUT cycle (faster)",
    )
    p_fund.set_defaults(
        func=lambda a: print(
            json.dumps(
                (
                    __import__(
                        "midterms.validation.fundamentals_ablation",
                        fromlist=["run_primary_holdout_ablation", "run_fundamentals_ablation"],
                    ).run_primary_holdout_ablation()
                    if a.primary_only
                    else __import__(
                        "midterms.validation.fundamentals_ablation",
                        fromlist=["run_fundamentals_ablation"],
                    ).run_fundamentals_ablation()
                ),
                indent=2,
            )
        )
    )

    p_api = sub.add_parser("serve-api", help="Serve forecast JSON API for the research UI")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=8787)

    def _serve(a: argparse.Namespace) -> None:
        import uvicorn
        from midterms.api.app import app

        uvicorn.run(app, host=a.host, port=a.port, log_level="info")

    p_api.set_defaults(func=_serve)

    p_mon = sub.add_parser("monitor-check", help="Health-check latest forecast artifact")
    p_mon.add_argument("--min-polls", type=int, default=50)
    p_mon.add_argument("--min-enop", type=float, default=5.0)

    def _monitor(a: argparse.Namespace) -> None:
        from midterms.ops.monitor import monitor_check

        report = monitor_check(min_polls=a.min_polls, min_enop=a.min_enop)
        print(json.dumps(report, indent=2))
        if not report.get("ok"):
            raise SystemExit(1)

    p_mon.set_defaults(func=_monitor)

    p_res = sub.add_parser(
        "write-results-archive",
        help="Build redistributable certified Senate results archive",
    )
    p_res.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.evidence.results_archive", fromlist=["write_results_archive"]
                ).write_results_archive(),
                indent=2,
            )
        )
    )

    p_ref = sub.add_parser(
        "refresh",
        help="Fetch → ingest → forecast → monitor (ops refresh chain)",
    )
    p_ref.add_argument("--election-id", default="senate-2026")
    p_ref.add_argument("--as-of", default="2026-09-01")
    p_ref.add_argument("--no-forecast", action="store_true")
    p_ref.add_argument("--draws", type=int, default=400)
    p_ref.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__("midterms.ops.refresh", fromlist=["run_refresh"]).run_refresh(
                    election_id=a.election_id,
                    as_of=a.as_of,
                    forecast=not a.no_forecast,
                    draws=a.draws,
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_val = sub.add_parser("validation-report", help="Build lead-time + ablation + nested-df report")
    p_val.add_argument("--full", action="store_true", help="More draws (slower)")
    p_val.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__("midterms.validation.report", fromlist=["build_validation_report"]).build_validation_report(
                    quick=not a.full
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_lead = sub.add_parser("lead-time-grid", help="Replay LEAD_DAYS grid for one cycle")
    p_lead.add_argument("--year", type=int, default=2022)
    p_lead.add_argument("--draws", type=int, default=250)
    p_lead.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.lead_time_grid", fromlist=["replay_lead_time_grid"]
                ).replay_lead_time_grid(year=a.year, draws=a.draws),
                indent=2,
                default=str,
            )
        )
    )

    p_abl = sub.add_parser("ablate-components", help="Heavy-tails / national / independence ablations")
    p_abl.add_argument("--year", type=int, default=2022)
    p_abl.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__("midterms.validation.ablations", fromlist=["run_component_ablations"]).run_component_ablations(
                    year=a.year
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_appr = sub.add_parser("fetch-approval", help="Write presidential approval vintage store")
    p_appr.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__("midterms.evidence.approval", fromlist=["write_approval_store"]).write_approval_store(),
                indent=2,
            )
        )
    )

    p_hist = sub.add_parser("freeze-historical-polls", help="Seal historical poll archive")
    p_hist.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.evidence.historical_polls",
                    fromlist=["freeze_historical_polls_from_warehouse"],
                ).freeze_historical_polls_from_warehouse(),
                indent=2,
            )
        )
    )

    p_vh = sub.add_parser(
        "seal-votehub-dumps",
        help="Seal VoteHub CC BY dumps + import into warehouse",
    )
    p_vh.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.evidence.votehub_archive",
                    fromlist=["ingest_votehub_dumps_to_warehouse"],
                ).ingest_votehub_dumps_to_warehouse(),
                indent=2,
                default=str,
            )
        )
    )

    p_fte = sub.add_parser(
        "ingest-fte-polls",
        help="Fetch FiveThirtyEight Senate polls (historical CC BY) into warehouse",
    )
    p_fte.add_argument(
        "--cycles",
        default=None,
        help="Comma-separated cycles (default: all non-2026 in the mirror)",
    )
    p_fte.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.evidence.fte_polls", fromlist=["ingest_fte_historical_into_warehouse"]
                ).ingest_fte_historical_into_warehouse(
                    cycles=[int(x) for x in a.cycles.split(",")] if a.cycles else None,
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_lic = sub.add_parser(
        "ingest-licensed-ratings",
        help="Load local licensed ratings CSV (COOK_RATINGS_CSV) if present",
    )
    p_lic.add_argument("--election-id", default="senate-2026")
    p_lic.add_argument("--as-of", default="2026-09-01")
    p_lic.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.evidence.licensed_ratings", fromlist=["try_ingest_licensed_ratings"]
                ).try_ingest_licensed_ratings(election_id=a.election_id, available_at=a.as_of),
                indent=2,
            )
        )
    )

    p_keys = sub.add_parser("generate-signing-keys", help="Generate Ed25519 keypair (public committed)")
    p_keys.add_argument(
        "--no-private-file",
        action="store_true",
        help="Only write public key; print reminder to set env for private key",
    )
    p_keys.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__("midterms.ops.signing", fromlist=["generate_keypair"]).generate_keypair(
                    write_private=not a.no_private_file
                ),
                indent=2,
            )
        )
    )

    p_ver = sub.add_parser("verify-signature", help="Verify an artifact .sig.json sidecar")
    p_ver.add_argument("path", help="Path to forecast.json (sidecar path.json.sig.json)")

    def _verify(a: argparse.Namespace) -> None:
        from pathlib import Path

        from midterms.ops.signing import verify_signature

        path = Path(a.path)
        sig_path = path.with_suffix(path.suffix + ".sig.json")
        if not sig_path.exists():
            raise SystemExit(f"missing sidecar {sig_path}")
        meta = json.loads(sig_path.read_text())
        ok = verify_signature(path.read_text(), meta["signature"], alg=meta.get("alg"))
        print(json.dumps({"ok": ok, "alg": meta.get("alg"), "path": str(path)}, indent=2))
        if not ok:
            raise SystemExit(1)

    p_ver.set_defaults(func=_verify)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
