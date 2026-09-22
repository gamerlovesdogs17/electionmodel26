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

    p_bayes = sub.add_parser(
        "synthetic-bayesian-diagnostics",
        help="Run cheap synthetic SBC only; does not read election evidence or fit the forecast",
    )
    p_bayes.add_argument("--replications", type=int, default=20)
    p_bayes.add_argument("--seed", type=int, default=7)
    p_bayes.set_defaults(
        func=lambda a: print(json.dumps(
            __import__(
                "midterms.validation.bayesian_diagnostics",
                fromlist=["gaussian_location_sbc"],
            ).gaussian_location_sbc(replications=a.replications, seed=a.seed),
            indent=2,
        ))
    )

    p_pres = sub.add_parser(
        "build-presidential-vote-store",
        help="Verify pinned FEC raw files and rebuild the statewide vote-count source store",
    )
    p_pres.add_argument("--fetch-missing", action="store_true",
                        help="Download a missing pinned FEC file only after its hash verifies")

    def _build_presidential_vote_store(a: argparse.Namespace) -> None:
        from midterms.evidence.presidential_results import build_vote_count_store

        manifest = build_vote_count_store(fetch_missing=a.fetch_missing)
        print(json.dumps({
            "parser_version": manifest["parser_version"],
            "source_set_sha256": manifest["source_set_sha256"],
            "normalized_sha256": manifest["normalized_sha256"],
            "source_years": [row["year"] for row in manifest["source_blocks"]],
            "n_rows": manifest["n_rows"],
            "derived_prior_status": manifest["derived_prior_status"],
        }, indent=2))

    p_pres.set_defaults(func=_build_presidential_vote_store)

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
    from midterms.config import DEMO_AS_OF as _DEMO_AS_OF

    p_run.add_argument("--as-of", default=_DEMO_AS_OF)
    p_run.add_argument("--method", choices=["fast", "pymc", "pymc_dynamic", "state_space"], default="pymc")
    p_run.add_argument("--draws", type=int, default=None)
    p_run.add_argument("--tune", type=int, default=None)
    p_run.add_argument("--chains", type=int, default=None)
    p_run.add_argument("--seed", type=int, default=20260901)
    p_run.add_argument(
        "--generic-ballot",
        type=float,
        default=None,
        help="Dem-Rep generic ballot margin (pp). Default: VoteHub 21d trailing average",
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
    p_run.add_argument("--market-weight", type=float, default=0.12)
    p_run.add_argument(
        "--control-weight",
        type=float,
        default=0.15,
        help="Soft national pull toward CONTROLS market (blueprint §9.4; default 0.15)",
    )
    p_run.add_argument(
        "--control-calibrate",
        action="store_true",
        help="Opt-in hard chamber calibration to market P(control) — off by default",
    )
    p_run.add_argument(
        "--allow-fast-fallback",
        action="store_true",
        help="Allow degraded fast hierarchical-t if PyMC fails (off by default)",
    )
    p_run.add_argument(
        "--require-publishable",
        action="store_true",
        help="Hard-fail if evidence tiers are synthetic/stale/untraceable (audit P0.4)",
    )
    p_run.add_argument(
        "--n-joint-sims",
        type=int,
        default=None,
        help="Correlated chamber simulation count (default: 10k demo / 50k with --require-publishable)",
    )

    def _forecast(a: argparse.Namespace) -> None:
        gb_meta = None
        gb = a.generic_ballot
        if gb is None:
            from midterms.evidence.ingest import generic_ballot_aggregate

            gb_meta = generic_ballot_aggregate(as_of=a.as_of) or {
                "margin": -1.0,
                "method": "fallback_default",
            }
            gb = float(gb_meta["margin"])
        else:
            gb_meta = {"margin": float(gb), "method": "cli_override"}
        result = run_forecast(
            election_id=a.election_id,
            as_of=a.as_of,
            method=a.method,
            draws=a.draws,
            tune=a.tune,
            chains=a.chains,
            seed=a.seed,
            generic_ballot=gb,
            generic_ballot_meta=gb_meta,
            ensemble=not a.no_ensemble,
            with_ratings=not a.no_ratings,
            with_markets=not a.no_markets,
            rating_weight=a.rating_weight,
            market_weight=a.market_weight,
            control_weight=a.control_weight,
            control_calibrate=bool(a.control_calibrate),
            allow_fast_fallback=bool(a.allow_fast_fallback) or a.method == "fast",
            require_publishable=bool(a.require_publishable),
            n_joint_sims=a.n_joint_sims,
        )
        print(
            json.dumps(
                {
                    "paths": result["paths"],
                    "chamber": result["artifact"]["chamber"],
                    "generic_ballot": gb,
                    "generic_ballot_meta": gb_meta,
                    "method": result["artifact"]["method"],
                    "run_class": result["artifact"].get("run_class"),
                    "publishable": result["artifact"].get("publishable"),
                    "stack_weights": result["artifact"].get("stack_weights"),
                    "diagnostics": {
                        k: result["artifact"]["diagnostics"].get(k)
                        for k in (
                            "enop_global",
                            "enop_by_race_mean",
                            "n_polls",
                            "ensemble",
                            "core_method",
                        )
                        if k in result["artifact"]["diagnostics"]
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
    p_cycle.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow synthetic fixture polls (CI only; production should use FTE)",
    )
    p_cycle.add_argument(
        "--hierarchical-method",
        choices=["pymc", "pymc_dynamic", "fast"],
        default="pymc",
        help="OOS hierarchical spine (default pymc; pymc_dynamic = weekly RW path)",
    )

    def _cycle(a: argparse.Namespace) -> None:
        if a.all:
            summary = replay_all_cycles(
                allow_synthetic=a.allow_synthetic,
                hierarchical_method=a.hierarchical_method,
            )
            print(
                json.dumps(
                    {
                        "path": summary.get("path"),
                        "mean_crps_by_model": summary.get("mean_crps_by_model"),
                        "stack_weights": summary.get("stack_weights"),
                        "hierarchical_method": summary.get("hierarchical_method"),
                        "cycles": summary.get("cycles"),
                    },
                    indent=2,
                )
            )
        else:
            report = replay_cycle(
                a.year,
                allow_synthetic=a.allow_synthetic,
                hierarchical_method=a.hierarchical_method,
            )
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
                        "hierarchical_method": report.get("hierarchical_method"),
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

    p_est = sub.add_parser(
        "estimate-fundamentals",
        help="Nested LOO ridge fundamentals coefs + stability report (audit P1.2)",
    )
    p_est.add_argument("--ridge-lambda", type=float, default=25.0)
    p_est.add_argument(
        "--apply",
        action="store_true",
        help="Set working COEF from full-sample estimate (session only)",
    )
    p_est.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.coefficient_stability",
                    fromlist=["run_coefficient_stability"],
                ).run_coefficient_stability(
                    ridge_lambda=a.ridge_lambda,
                    apply_full_sample=a.apply,
                ),
                indent=2,
                default=str,
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

    p_lpo = sub.add_parser(
        "leave-pollster-out",
        help="Leave-pollster-out CRPS diagnostics (blueprint §4.3)",
    )
    p_lpo.add_argument("--year", type=int, default=2022)
    p_lpo.add_argument("--lead-days", type=int, default=60)
    p_lpo.add_argument("--draws", type=int, default=300)
    p_lpo.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.leave_pollster_out", fromlist=["leave_pollster_out"]
                ).leave_pollster_out(year=a.year, lead_days=a.lead_days, draws=a.draws),
                indent=2,
                default=str,
            )
        )
    )

    p_rec = sub.add_parser(
        "reconcile-chamber",
        help="Official ballot + 100-seat/control reconciliation gate (audit P0.2)",
    )
    p_rec.add_argument("--year", type=int, default=None, help="Single year; default gate years 2018–2024")
    p_rec.add_argument(
        "--all-meta",
        action="store_true",
        help="Include provisional CYCLE_META years (2014/2016) outside the production gate",
    )
    p_rec.set_defaults(
        func=lambda a: print(
            json.dumps(
                (
                    __import__(
                        "midterms.validation.chamber_reconcile", fromlist=["reconcile_cycle"]
                    ).reconcile_cycle(a.year)
                    if a.year
                    else __import__(
                        "midterms.validation.chamber_reconcile", fromlist=["reconcile_all_cycles"]
                    ).reconcile_all_cycles(
                        years=(
                            tuple(
                                sorted(
                                    __import__(
                                        "midterms.evidence.official_ballot",
                                        fromlist=["CYCLE_META"],
                                    ).CYCLE_META
                                )
                            )
                            if a.all_meta
                            else None
                        )
                    )
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_vrb = sub.add_parser(
        "verify-rebuild",
        help="Hash-seal lite and/or independent rebuild from sealed manifest (G10)",
    )
    p_vrb.add_argument("--run-id", default=None)
    p_vrb.add_argument(
        "--independent",
        action="store_true",
        help="Re-execute forecast from sealed knobs and compare within tolerances",
    )
    p_vrb.add_argument("--release-dir", default=None)
    p_vrb.add_argument(
        "--method-override",
        default=None,
        help="Optional fit method override for independent rebuild (e.g. fast)",
    )

    def _verify_rebuild(a: argparse.Namespace) -> None:
        from pathlib import Path

        from midterms.ops.reproducibility import independent_rebuild, verify_rebuild

        if a.independent:
            out = independent_rebuild(
                run_id=a.run_id,
                release_dir=Path(a.release_dir) if a.release_dir else None,
                method_override=a.method_override,
            )
        else:
            out = verify_rebuild(run_id=a.run_id)
        print(json.dumps(out, indent=2, default=str))
        if not out.get("ok"):
            raise SystemExit(1)

    p_vrb.set_defaults(func=_verify_rebuild)

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
    p_val.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow synthetic historical polls in cycle gate",
    )
    p_val.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__("midterms.validation.report", fromlist=["build_validation_report"]).build_validation_report(
                    quick=not a.full,
                    allow_synthetic=a.allow_synthetic,
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

    p_pcov = sub.add_parser(
        "poll-coverage",
        help="Official contest/nominee/horizon poll coverage gate (audit P0.3)",
    )
    p_pcov.add_argument("--year", type=int, default=None)
    p_pcov.set_defaults(
        func=lambda a: print(
            json.dumps(
                (
                    __import__(
                        "midterms.validation.poll_coverage", fromlist=["poll_coverage_report"]
                    ).poll_coverage_report(a.year)
                    if a.year
                    else __import__(
                        "midterms.validation.poll_coverage", fromlist=["write_poll_coverage_report"]
                    ).write_poll_coverage_report()
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_elig = sub.add_parser(
        "evidence-eligibility",
        help="Classify evidence tiers and publication eligibility (audit P0.4)",
    )
    p_elig.add_argument("--election-id", default="senate-2026")
    p_elig.add_argument("--as-of", default=None)
    p_elig.add_argument(
        "--strict", action="store_true",
        help="Exit nonzero after writing the report when publication eligibility fails",
    )

    def _evidence_eligibility(a: argparse.Namespace) -> None:
        report = __import__(
            "midterms.evidence.eligibility", fromlist=["write_eligibility_report"]
        ).write_eligibility_report(a.election_id, as_of=a.as_of)
        print(json.dumps(report, indent=2, default=str))
        if a.strict and not report.get("publishable"):
            raise SystemExit(1)

    p_elig.set_defaults(func=_evidence_eligibility)

    p_svd = sub.add_parser(
        "compare-static-dynamic",
        help="OOS score static PyMC vs weekly dynamic PyMC (audit P1.1)",
    )
    p_svd.add_argument("--year", type=int, default=2022)
    p_svd.add_argument("--draws", type=int, default=150)
    p_svd.add_argument("--tune", type=int, default=150)
    p_svd.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.static_vs_dynamic",
                    fromlist=["compare_static_vs_dynamic"],
                ).compare_static_vs_dynamic(
                    a.year, draws=a.draws, tune=a.tune, chains=2
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_cov = sub.add_parser(
        "calibrate-covariance",
        help="Nested terminal+similarity scale grid (audit P1.3)",
    )
    p_cov.add_argument("--draws", type=int, default=800)
    p_cov.add_argument("--max-configs", type=int, default=None)
    p_cov.add_argument(
        "--no-apply",
        action="store_true",
        help="Do not update module defaults when gate passes",
    )
    p_cov.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.covariance_calibration",
                    fromlist=["run_covariance_calibration"],
                ).run_covariance_calibration(
                    n_draws=a.draws,
                    max_configs=a.max_configs,
                    apply_defaults=not a.no_apply,
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_nloo = sub.add_parser(
        "nested-component-loo",
        help="Freeze-then-score nested LOO for every component (audit P2.1)",
    )
    p_nloo.add_argument("--draws", type=int, default=1600)
    p_nloo.add_argument(
        "--hierarchical-method",
        default="pymc",
        choices=("fast", "pymc", "pymc_dynamic"),
        help="Hierarchical spine for LOO (pymc default — align with production)",
    )
    p_nloo.add_argument("--years", default="2018,2020,2022,2024")
    p_nloo.add_argument("--leads", default="60,30")
    p_nloo.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.nested_component_loo",
                    fromlist=["run_nested_component_loo"],
                ).run_nested_component_loo(
                    years=tuple(int(x) for x in a.years.split(",") if x.strip()),
                    lead_days=tuple(int(x) for x in a.leads.split(",") if x.strip()),
                    hierarchical_method=a.hierarchical_method,
                    n_draws=a.draws,
                ),
                indent=2,
                default=str,
            )
        )
    )

    p_sw = sub.add_parser(
        "fit-stack-weights",
        help="Fit reproducible OOF ensemble weights from nested LOO matrix (audit P2.2)",
    )
    p_sw.add_argument("--temperature", type=float, default=0.75)
    p_sw.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.stack_weights",
                    fromlist=["fit_stack_weights_from_nested_loo"],
                ).fit_stack_weights_from_nested_loo(temperature=a.temperature),
                indent=2,
                default=str,
            )
        )
    )

    p_crossfit = sub.add_parser(
        "crossfit-stack-reliability",
        help=(
            "Cross-fit stack reliability from frozen historical predictive distributions only; "
            "performs no model refits or network calls"
        ),
    )
    p_crossfit.set_defaults(
        func=lambda a: print(
            json.dumps(
                __import__(
                    "midterms.validation.stack_reliability_crossfit",
                    fromlist=["write_stack_reliability_crossfit"],
                ).write_stack_reliability_crossfit(),
                indent=2,
                default=str,
            )
        )
    )

    p_nq = sub.add_parser(
        "numerical-check",
        help="MCSE / convergence gate on latest forecast (audit P2.3 / G9)",
    )
    p_nq.add_argument("--publishable", action="store_true")

    def _numerical_check(a: argparse.Namespace) -> None:
        from midterms.config import ARTIFACTS_DIR
        from midterms.validation.numerical_quality import evaluate_numerical_quality

        art = json.loads((ARTIFACTS_DIR / "forecast_latest.json").read_text())
        existing = art.get("numerical_quality")
        if existing and not a.publishable:
            print(json.dumps(existing, indent=2, default=str))
            return
        chamber = art.get("chamber") or {}
        diag = art.get("diagnostics") or {}
        report = evaluate_numerical_quality(
            p_dem_control=float(chamber.get("p_dem_majority") or 0.5),
            n_posterior_samples=diag.get("n_posterior_samples") or diag.get("draws"),
            draws=diag.get("draws"),
            tune=diag.get("tune"),
            chains=diag.get("chains"),
            convergence=diag.get("convergence"),
            seed=art.get("seed"),
            publishable=bool(a.publishable),
        )
        # Re-attach chamber MCSE if seat histogram implies draw count
        hist = chamber.get("seat_histogram") or []
        n = int(sum(int(h.get("count") or 0) for h in hist)) if hist else 0
        if n > 0:
            from midterms.validation.numerical_quality import mcse_bernoulli

            p = float(chamber.get("p_dem_majority") or 0.5)
            seats = float(chamber.get("expected_dem_seats") or 50.0)
            report["chamber_mcse"] = {
                "n_draws": float(n),
                "mcse_p_dem_control": mcse_bernoulli(p, n),
                "mcse_expected_dem_seats": float("nan"),  # need full draws
                "p_dem_control": p,
                "expected_dem_seats": seats,
                "note": "recomputed from artifact histogram counts",
            }
            report["checks"] = [
                c
                for c in report["checks"]
                if c["name"] not in {"mcse_control", "mcse_seats", "min_sim_draws"}
            ]
            report["checks"].append(
                {
                    "name": "mcse_control",
                    "ok": report["chamber_mcse"]["mcse_p_dem_control"]
                    <= report["thresholds"]["mcse_control_max"],
                    "detail": report["chamber_mcse"]["mcse_p_dem_control"],
                }
            )
            report["checks"].append(
                {
                    "name": "min_sim_draws",
                    "ok": n >= report["thresholds"]["min_sim_draws"],
                    "detail": n,
                }
            )
            report["ok"] = all(c["ok"] for c in report["checks"])
        print(json.dumps(report, indent=2, default=str))

    p_nq.set_defaults(func=_numerical_check)

    p_acc = sub.add_parser(
        "acceptance-gates",
        help="Aggregate G1–G11 acceptance report (first publishable milestone)",
    )
    p_acc.add_argument(
        "--strict", action="store_true",
        help="Exit nonzero after writing the report when research acceptance fails",
    )

    def _acceptance_gates(a: argparse.Namespace) -> None:
        report = __import__(
            "midterms.validation.acceptance_gates",
            fromlist=["evaluate_acceptance_gates"],
        ).evaluate_acceptance_gates()
        print(json.dumps(report, indent=2, default=str))
        if a.strict and not report.get("ok"):
            raise SystemExit(1)

    p_acc.set_defaults(func=_acceptance_gates)

    p_pub = sub.add_parser(
        "publish-live",
        help="Publish public-facing live probabilities (requires green G1–G11)",
    )
    p_pub.add_argument("--no-shadow", action="store_true", help="Skip live shadow seal")
    p_pub.add_argument("--no-web", action="store_true", help="Skip web/public sync")

    def _publish_live(a: argparse.Namespace) -> None:
        from midterms.ops.public_publish import publish_live

        out = publish_live(seal_shadow=not a.no_shadow, sync_web=not a.no_web)
        print(json.dumps(out, indent=2, default=str))

    p_pub.set_defaults(func=_publish_live)

    p_shadow = sub.add_parser(
        "shadow-publish",
        help="Freeze timestamped shadow publication (audit P3 / Milestone-0)",
    )
    p_shadow.add_argument(
        "--mode",
        choices=("historical", "live", "ensure", "milestone"),
        default="ensure",
        help="historical|live|ensure|milestone (pymc spine first-publishable seal)",
    )
    p_shadow.add_argument("--year", type=int, default=2022)
    p_shadow.add_argument("--lead-days", type=int, default=60)
    p_shadow.add_argument("--method", default="fast", choices=("fast", "pymc", "pymc_dynamic"))
    p_shadow.add_argument("--draws", type=int, default=400)
    p_shadow.add_argument("--full-components", action="store_true", help="Freeze all LOO components")

    def _shadow_publish(a: argparse.Namespace) -> None:
        from midterms.ops.shadow_publish import (
            ensure_retained_shadow,
            publish_historical_shadow,
            publish_live_shadow,
            publish_milestone_shadow,
        )

        if a.mode == "live":
            out = publish_live_shadow()
        elif a.mode == "milestone":
            out = publish_milestone_shadow(
                year=a.year,
                lead_days=a.lead_days,
                n_draws=max(a.draws, 800),
            )
        elif a.mode == "historical":
            out = publish_historical_shadow(
                year=a.year,
                lead_days=a.lead_days,
                hierarchical_method=a.method,
                n_draws=a.draws,
                spine_only=not a.full_components,
            )
        else:
            out = ensure_retained_shadow(
                year=a.year,
                lead_days=a.lead_days,
                hierarchical_method=a.method,
                n_draws=a.draws,
            )
        print(json.dumps(out, indent=2, default=str))

    p_shadow.set_defaults(func=_shadow_publish)

    p_sver = sub.add_parser(
        "shadow-verify",
        help="Verify sealed shadow publication hashes (audit P3)",
    )
    p_sver.add_argument("--shadow-id", default=None)
    p_sver.add_argument("--path", default=None)

    def _shadow_verify(a: argparse.Namespace) -> None:
        from pathlib import Path

        from midterms.ops.shadow_publish import verify_shadow

        out = verify_shadow(
            a.shadow_id,
            path=Path(a.path) if a.path else None,
        )
        print(json.dumps(out, indent=2, default=str))
        if not out.get("ok"):
            raise SystemExit(1)

    p_sver.set_defaults(func=_shadow_verify)

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
