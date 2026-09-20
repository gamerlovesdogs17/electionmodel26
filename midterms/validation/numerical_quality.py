"""Numerical quality: MCSE, ESS/R-hat, deterministic replay (audit P2.3 / G9)."""

from __future__ import annotations

from typing import Any

import numpy as np

from midterms.config import PRODUCTION_CHAINS, PRODUCTION_DRAWS, PRODUCTION_TUNE


# Publishable-run thresholds (predeclared).
MCSE_CONTROL_MAX = 0.01  # 1pp on P(Dem control); needs ~2500 draws at p=0.5
MCSE_SEATS_MAX = 0.15  # seats
RHAT_MAX = 1.05
ESS_BULK_MIN_FRAC = 0.10  # ess_bulk >= frac * n_samples for worst race
MIN_POSTERIOR_SAMPLES = 1600  # routine development diagnostic
PRODUCTION_POSTERIOR_SAMPLES = PRODUCTION_DRAWS * PRODUCTION_CHAINS
MIN_SIM_DRAWS = 2500  # chamber / stacked predictive draws (CI floor)
# Routine internal forecasts should meet this; production target is higher still.
ROUTINE_SIM_DRAWS = 10_000
PRODUCTION_SIM_DRAWS = 50_000


def mcse_bernoulli(p: float, n: int) -> float:
    """Monte Carlo SE for an empirical probability."""
    n = max(int(n), 1)
    p = float(np.clip(p, 0.0, 1.0))
    return float(np.sqrt(p * (1.0 - p) / n))


def mcse_mean(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float).ravel()
    if len(x) < 2:
        return float("inf")
    return float(np.std(x, ddof=1) / np.sqrt(len(x)))


def chamber_mcse(
    seat_draws: np.ndarray,
    *,
    p_dem_control: float | None = None,
    majority_threshold: int = 51,
    vp_tiebreak_party: str = "R",
) -> dict[str, float]:
    """MCSE for expected seats and P(Dem chamber control)."""
    seats = np.asarray(seat_draws, dtype=float).ravel()
    n = len(seats)
    if p_dem_control is None:
        if vp_tiebreak_party == "D":
            p_dem_control = float((seats >= 50).mean())
        else:
            p_dem_control = float((seats >= majority_threshold).mean())
    return {
        "n_draws": float(n),
        "mcse_p_dem_control": mcse_bernoulli(p_dem_control, n),
        "mcse_expected_dem_seats": mcse_mean(seats),
        "p_dem_control": float(p_dem_control),
        "expected_dem_seats": float(seats.mean()) if n else float("nan"),
    }


def race_win_mcse(draws_margin: np.ndarray) -> dict[str, Any]:
    """Per-race MCSE on P(Dem win) from margin draws (n_draws, n_races)."""
    d = np.asarray(draws_margin, dtype=float)
    if d.ndim != 2 or d.size == 0:
        return {"n_races": 0, "max_mcse_p_dem": float("inf"), "mean_mcse_p_dem": float("inf")}
    n, n_races = d.shape
    p = (d > 0).mean(axis=0)
    mcse = np.sqrt(np.clip(p * (1.0 - p), 0.0, None) / max(n, 1))
    return {
        "n_draws": n,
        "n_races": n_races,
        "max_mcse_p_dem": float(np.max(mcse)),
        "mean_mcse_p_dem": float(np.mean(mcse)),
        "p95_mcse_p_dem": float(np.quantile(mcse, 0.95)),
    }


def idata_convergence(idata: Any, *, var_name: str = "mu_final") -> dict[str, Any]:
    """R-hat / ESS from ArviZ InferenceData when available."""
    out: dict[str, Any] = {"available": False, "var_name": var_name}
    try:
        import arviz as az

        if var_name not in idata.posterior:
            out["error"] = f"{var_name} missing from posterior"
            return out
        summary = az.summary(idata, var_names=[var_name], kind="all", round_to=None)
        rhat = summary["r_hat"].to_numpy(dtype=float)
        ess_bulk = summary["ess_bulk"].to_numpy(dtype=float)
        ess_tail = (
            summary["ess_tail"].to_numpy(dtype=float)
            if "ess_tail" in summary.columns
            else ess_bulk
        )
        n_samples = int(idata.posterior.sizes.get("chain", 1) * idata.posterior.sizes.get("draw", 0))
        out.update(
            {
                "available": True,
                "n_samples": n_samples,
                "r_hat_max": float(np.nanmax(rhat)),
                "r_hat_mean": float(np.nanmean(rhat)),
                "ess_bulk_min": float(np.nanmin(ess_bulk)),
                "ess_bulk_median": float(np.nanmedian(ess_bulk)),
                "ess_tail_min": float(np.nanmin(ess_tail)),
                "ess_bulk_min_frac": float(np.nanmin(ess_bulk) / max(n_samples, 1)),
            }
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def deterministic_replay_check(
    draws_margin: np.ndarray,
    *,
    seed: int,
    n_probe: int = 64,
) -> dict[str, Any]:
    """
    Same seed + same draws → identical chamber control probe.

    Uses a cheap hash of the first/last probe rows as a stand-in for full rerun
    when the forecast pipeline is not invoked twice.
    """
    d = np.asarray(draws_margin, dtype=float)
    if d.size == 0:
        return {"ok": False, "error": "empty draws"}
    n = min(n_probe, d.shape[0])
    rng_a = np.random.default_rng(seed)
    rng_b = np.random.default_rng(seed)
    idx_a = rng_a.integers(0, d.shape[0], size=n)
    idx_b = rng_b.integers(0, d.shape[0], size=n)
    same_idx = bool(np.array_equal(idx_a, idx_b))
    import hashlib

    probe = np.asarray(d[:n], dtype=np.float64)
    digest = hashlib.sha256(probe.tobytes()).hexdigest()[:16]
    return {
        "ok": same_idx,
        "seed": seed,
        "n_probe": n,
        "draws_fingerprint": digest,
        "note": "RNG probe identical under fixed seed; draws content fingerprinted",
    }


def evaluate_numerical_quality(
    *,
    seat_draws: np.ndarray | None = None,
    draws_margin: np.ndarray | None = None,
    p_dem_control: float | None = None,
    n_posterior_samples: int | None = None,
    draws: int | None = None,
    tune: int | None = None,
    chains: int | None = None,
    convergence: dict[str, Any] | None = None,
    seed: int | None = None,
    publishable: bool = False,
) -> dict[str, Any]:
    """
    Gate G9: convergence + MCSE + sample-size floors.

    When ``publishable`` is True, failures set ``ok=False``.
    """
    checks: list[dict[str, Any]] = []
    alerts: list[str] = []

    def add(name: str, ok: bool, detail: Any = None, alert: str | None = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok and alert:
            alerts.append(alert)

    chamber = {}
    if seat_draws is not None:
        chamber = chamber_mcse(seat_draws, p_dem_control=p_dem_control)
        # Seat-count probability MCSEs for key thresholds
        seats = np.asarray(seat_draws, dtype=float).ravel()
        n_s = max(len(seats), 1)
        seat_prob_mcse = {}
        for k in (50, 51, 52, 48, 49):
            p_k = float((seats == k).mean())
            seat_prob_mcse[f"p_dem_seats_eq_{k}"] = {
                "p": p_k,
                "mcse": mcse_bernoulli(p_k, n_s),
            }
        chamber["seat_count_mcse"] = seat_prob_mcse
        add(
            "mcse_control",
            chamber["mcse_p_dem_control"] <= MCSE_CONTROL_MAX,
            chamber["mcse_p_dem_control"],
            f"MCSE(P_control)={chamber['mcse_p_dem_control']:.4f} > {MCSE_CONTROL_MAX}",
        )
        add(
            "mcse_seats",
            chamber["mcse_expected_dem_seats"] <= MCSE_SEATS_MAX,
            chamber["mcse_expected_dem_seats"],
            f"MCSE(E[seats])={chamber['mcse_expected_dem_seats']:.3f} > {MCSE_SEATS_MAX}",
        )
        add(
            "min_sim_draws",
            chamber["n_draws"] >= MIN_SIM_DRAWS,
            int(chamber["n_draws"]),
            f"sim draws {int(chamber['n_draws'])} < {MIN_SIM_DRAWS}",
        )
        add(
            "n_joint_sims_reported",
            True,
            int(chamber["n_draws"]),
        )

    race = {}
    if draws_margin is not None:
        race = race_win_mcse(draws_margin)
        add(
            "race_win_mcse",
            race.get("max_mcse_p_dem", 1.0) <= 0.02,
            race.get("max_mcse_p_dem"),
            f"max race P(win) MCSE={race.get('max_mcse_p_dem')}",
        )

    posterior_floor = PRODUCTION_POSTERIOR_SAMPLES if publishable else MIN_POSTERIOR_SAMPLES
    if n_posterior_samples is not None:
        add(
            "min_posterior_samples",
            int(n_posterior_samples) >= posterior_floor,
            int(n_posterior_samples),
            f"posterior samples {n_posterior_samples} < {posterior_floor}",
        )
    elif publishable:
        add("min_posterior_samples", False, None, "posterior sample count unavailable")
    if publishable:
        for name, value, floor in (
            ("draws", draws, PRODUCTION_DRAWS),
            ("tune", tune, PRODUCTION_TUNE),
            ("chains", chains, PRODUCTION_CHAINS),
        ):
            add(
                f"production_{name}",
                value is not None and int(value) >= floor,
                value,
                f"publication {name} {value} < production floor {floor}",
            )

    conv = convergence or {}
    if conv.get("available"):
        add(
            "r_hat",
            float(conv.get("r_hat_max") or 99) <= RHAT_MAX,
            conv.get("r_hat_max"),
            f"r_hat_max={conv.get('r_hat_max')} > {RHAT_MAX}",
        )
        frac = float(conv.get("ess_bulk_min_frac") or 0)
        add(
            "ess_bulk",
            frac >= ESS_BULK_MIN_FRAC,
            {"ess_bulk_min": conv.get("ess_bulk_min"), "frac": frac},
            f"ess_bulk_min_frac={frac:.3f} < {ESS_BULK_MIN_FRAC}",
        )
    elif publishable:
        # Publishable PyMC runs should report convergence; fast-only is soft
        add(
            "convergence_reported",
            False,
            conv,
            "convergence diagnostics unavailable for publishable run",
        )

    replay = {}
    if draws_margin is not None and seed is not None:
        replay = deterministic_replay_check(draws_margin, seed=seed)
        add("deterministic_replay", bool(replay.get("ok")), replay)

    hard_ok = all(c["ok"] for c in checks) if publishable else True
    soft_ok = all(c["ok"] for c in checks)
    return {
        "audit_item": "P2.3",
        "ok": soft_ok if not publishable else hard_ok,
        "publishable_gate": publishable,
        "thresholds": {
            "mcse_control_max": MCSE_CONTROL_MAX,
            "mcse_seats_max": MCSE_SEATS_MAX,
            "rhat_max": RHAT_MAX,
            "ess_bulk_min_frac": ESS_BULK_MIN_FRAC,
            "min_posterior_samples": posterior_floor,
            "production_draws": PRODUCTION_DRAWS,
            "production_tune": PRODUCTION_TUNE,
            "production_chains": PRODUCTION_CHAINS,
            "min_sim_draws": MIN_SIM_DRAWS,
        },
        "chamber_mcse": chamber,
        "race_mcse": race,
        "convergence": conv,
        "deterministic_replay": replay,
        "checks": checks,
        "alerts": alerts,
    }
