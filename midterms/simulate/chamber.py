"""Joint chamber simulator — correlated race margins → seats → control.

2025–2029: Republican Vice President breaks a 50–50 Senate, so exactly 50
Democratic seats counts as Republican chamber control (not a separate tie
outcome for the control estimand).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from midterms.evidence.schema import is_active_ballot_row
from midterms.evidence.tickets import ticket_for_state
from midterms.model.overlays import rating_from_modeled_probability
from midterms.model.pymc_model import FitResult


def vp_tiebreak_for_election_year(year: int) -> str:
    """Vice President's party on November election day of `year`."""
    y = int(year)
    if y >= 2025:
        return "R"  # Vance
    if y >= 2021:
        return "D"  # Harris
    if y >= 2017:
        return "R"  # Pence
    if y >= 2009:
        return "D"  # Biden
    return "R"


@dataclass
class ChamberSimulation:
    seat_draws: np.ndarray  # dem caucus seats per draw (length n_draws)
    race_win: np.ndarray  # (n_draws, n_races) 1 if dem-caucus win
    race_ids: list[str]
    states: list[str]
    held_dem: int
    held_rep: int
    majority_threshold: int
    p_dem_majority: float
    p_rep_majority: float
    p_fifty_fifty: float  # P(exactly 50 D) — counted inside Rep control via VP
    expected_dem_seats: float
    seat_histogram: dict[str, int]
    vp_tiebreak_party: str = "R"
    held_ind: int = 0
    n_joint_sims: int = 0
    n_posterior_margin_draws: int = 0
    unresolved_seat_draws: np.ndarray | None = None  # seats with unknown caucus
    p_unresolved_control: float = 0.0
    expected_unresolved_seats: float = 0.0


def expand_joint_draws(
    margins: np.ndarray,
    *,
    n_sims: int,
    seed: int = 0,
) -> np.ndarray:
    """
    Expand posterior predictive margin draws to ``n_sims`` joint election sims.

    Resamples existing correlated draws with replacement (preserves dependence).
    Does **not** invent independent Bernoulli race outcomes.
    """
    margins = np.asarray(margins, dtype=float)
    if margins.ndim != 2 or margins.shape[0] == 0:
        raise ValueError("margins must be (n_draws, n_races) with n_draws > 0")
    n_post, _n_races = margins.shape
    target = int(n_sims)
    if target <= n_post:
        return margins[:target].copy()
    rng = np.random.default_rng(int(seed))
    idx = rng.integers(0, n_post, size=target)
    return margins[idx]


def simulate_chamber(
    fit: FitResult,
    races: pd.DataFrame,
    *,
    majority_threshold: int = 51,
    vp_tiebreak_party: str = "R",
    n_sims: int | None = None,
    sim_seed: int | None = None,
    withheld_contested_seats: int = 0,
) -> tuple[ChamberSimulation, list[dict]]:
    """
    Translate joint margin draws into seat outcomes and chamber control.

    Held seats (not_up) are fixed from `held_by`. Contested seats flip by sign of
    the joint margin draw. Independents caucusing with Democrats count as Dem seats.

    With a Republican VP, Dem control requires >=51 seats; dem_seats <= 50 is
    Republican control (including 50–50).

    ``n_sims`` optionally expands posterior draws to a larger joint-simulation
    count via resampling (correlated structure preserved).

    ``withheld_contested_seats`` counts active contests intentionally excluded
    from the FitResult (e.g. unsupported multiway) so 100-seat accounting still
    closes; those seats are unresolved for chamber control.
    """
    from midterms.evidence.outcome_identity import require_binary_chamber_compatibility

    require_binary_chamber_compatibility(races)
    withheld_contested_seats = int(withheld_contested_seats)
    if withheld_contested_seats < 0:
        raise ValueError("withheld_contested_seats must be nonnegative")
    # ``fit`` can intentionally exclude an active contested seat from binary
    # margin scoring (for example, a non D-v-R general election). Such a seat
    # still exists for chamber accounting. Inactive alternate rows (withdrawn
    # candidates, pending runoffs, etc.) are representations of the same seat,
    # however, and must never be counted as additional Senate seats.
    contested = races[races.apply(is_active_ballot_row, axis=1)] if len(races) else races
    held = races[races["not_up"]] if len(races) else races
    held_ind = int((held["held_by"] == "I").sum()) if len(held) else 0
    held_dem = int((held["held_by"] == "D").sum()) + held_ind if len(held) else 0
    held_rep = int((held["held_by"] == "R").sum()) if len(held) else 0

    margins = np.asarray(fit.draws_margin, dtype=float)
    n_post = int(margins.shape[0]) if margins.ndim == 2 else 0
    if n_sims is not None and int(n_sims) > n_post > 0:
        seed = int(sim_seed if sim_seed is not None else 0)
        margins = expand_joint_draws(margins, n_sims=int(n_sims), seed=seed)
    n_draws, n_races = margins.shape
    wins = (margins >= 0).astype(int)

    # Alaska's adapter retains the multi-candidate winner as the authoritative
    # draw.  Its signed column exists only to fit the common FitResult shape;
    # chamber accounting must therefore reconstruct caucus wins from candidate
    # identity before computing seats.
    alaska_diagnostics = (fit.diagnostics or {}).get("alaska_rcv_adapter") or {}
    if not alaska_diagnostics and (fit.diagnostics or {}).get("method") == "limited_validation_alaska_rcv_model-v1":
        alaska_diagnostics = fit.diagnostics or {}
    alaska_winners = np.asarray(alaska_diagnostics.get("winner_indices") or [], dtype=int)
    alaska_race_id = str(alaska_diagnostics.get("race_id") or "")
    if alaska_race_id and alaska_race_id in fit.race_ids:
        if alaska_winners.size != n_post:
            raise ValueError("Alaska RCV candidate-winner draws are not aligned with posterior draws")
        if n_draws != alaska_winners.size:
            if n_sims is None or int(n_sims) <= alaska_winners.size:
                alaska_winners = alaska_winners[:n_draws]
            else:
                resample_rng = np.random.default_rng(int(sim_seed if sim_seed is not None else 0))
                alaska_winners = alaska_winners[
                    resample_rng.integers(0, alaska_winners.size, size=n_draws)
                ]
        caucuses = list(alaska_diagnostics.get("caucuses") or [])
        if not caucuses or np.any((alaska_winners < 0) | (alaska_winners >= len(caucuses))):
            raise ValueError("Alaska RCV winner identity cannot be mapped to a caucus")
        winner_caucuses = np.asarray([caucuses[index] for index in alaska_winners], dtype=object)
        if not set(winner_caucuses.tolist()).issubset({"D", "R"}):
            raise ValueError("Alaska RCV winner has no explicit chamber-caucus mapping")
        alaska_index = fit.race_ids.index(alaska_race_id)
        wins[:, alaska_index] = (winner_caucuses == "D").astype(int)

    # Generic multiway plurality: candidate winners are authoritative; signed
    # margin is only a FitResult compatibility carrier. Unknown-caucus winners
    # fail closed (seat contributes to neither caucus for that draw).
    multiway_block = (fit.diagnostics or {}).get("multiway_plurality_adapter")
    multiway_rows = (
        multiway_block
        if isinstance(multiway_block, list)
        else ([multiway_block] if multiway_block else [])
    )
    unresolved_by_race = np.zeros((n_draws, n_races), dtype=int)
    for multiway_diagnostics in multiway_rows:
        if not isinstance(multiway_diagnostics, dict):
            continue
        multiway_race_id = str(multiway_diagnostics.get("race_id") or "")
        if not multiway_race_id or multiway_race_id not in fit.race_ids:
            continue
        winner_ids = list(multiway_diagnostics.get("winner_candidate_ids") or [])
        fail_closed = np.asarray(
            multiway_diagnostics.get("fail_closed_draws") or [], dtype=bool,
        )
        if len(winner_ids) != n_post and len(fail_closed) == n_post:
            winner_ids = [None] * n_post
        if len(winner_ids) != n_post:
            raise ValueError(
                f"multiway plurality winner draws are not aligned for {multiway_race_id}"
            )
        if n_draws != n_post:
            if n_sims is None or int(n_sims) <= n_post:
                winner_ids = winner_ids[:n_draws]
                fail_closed = fail_closed[:n_draws]
            else:
                resample_rng = np.random.default_rng(int(sim_seed if sim_seed is not None else 0))
                idx = resample_rng.integers(0, n_post, size=n_draws)
                winner_ids = [winner_ids[i] for i in idx]
                fail_closed = fail_closed[idx]
        caucus_map = {
            str(cid): cau
            for cid, cau in zip(
                multiway_diagnostics.get("candidate_ids") or [],
                multiway_diagnostics.get("caucuses") or [],
                strict=False,
            )
        }
        dem_col = np.zeros(n_draws, dtype=int)
        for draw, (winner, failed) in enumerate(zip(winner_ids, fail_closed)):
            if failed or winner is None:
                continue
            caucus = caucus_map.get(str(winner))
            if caucus == "D":
                dem_col[draw] = 1
            elif caucus != "R":
                # Unknown caucus: leave as 0 Dem and do not invent an R seat.
                continue
        multiway_index = fit.race_ids.index(multiway_race_id)
        wins[:, multiway_index] = dem_col
        # Track unknown-caucus / residual winners separately so they are not
        # silently mapped to Republican via (margins < 0).
        if fail_closed.size == n_draws:
            unresolved_by_race[:, multiway_index] = fail_closed.astype(int)

    # An active contest without predictive draws is an incomplete chamber
    # forecast.  Incumbent/held_by is historical state, not a deterministic
    # substitute for the missing election distribution.
    fit_ids = set(fit.race_ids)
    if len(contested):
        omitted = contested[~contested["race_id"].isin(fit_ids)]
        if len(omitted):
            raise ValueError(
                "complete chamber forecast requires predictive draws for every "
                "active contested seat; unsupported/withheld races: "
                + ", ".join(sorted(omitted["race_id"].astype(str).tolist()))
            )

    draw_unresolved = unresolved_by_race.sum(axis=1)
    dem_seats = held_dem + wins.sum(axis=1)
    # Permanent withheld seats (unsupported multiway not in FitResult) conserve
    # the 100-seat total as unresolved, but do not zero every draw's majority
    # probability — only draw-level unknown-caucus winners do that.
    unresolved_seats = draw_unresolved + withheld_contested_seats
    total = held_dem + held_rep + n_races + withheld_contested_seats
    if total != 100:
        raise ValueError(
            f"Senate seat accounting must total 100 (held_dem={held_dem}, "
            f"held_rep={held_rep}, contested_in_fit={n_races}, "
            f"withheld_contested={withheld_contested_seats}, total={total}). "
            "Check not_up / withdrawn / fit race_ids."
        )
    rep_seats = total - dem_seats - unresolved_seats
    if np.any(rep_seats < 0) or np.any(dem_seats + unresolved_seats + rep_seats != total):
        raise ValueError("multiway unknown-caucus accounting broke 100-seat conservation")

    hist: dict[str, int] = {}
    for s in dem_seats.astype(int):
        hist[str(int(s))] = hist.get(str(int(s)), 0) + 1

    draw_resolved = draw_unresolved == 0
    p_fifty = float(((dem_seats == 50) & draw_resolved).mean())
    p_unresolved = float((~draw_resolved).mean())
    # VP tiebreak among draws without fitted unknown-caucus winners.
    if vp_tiebreak_party == "R":
        p_dem_maj = float(((dem_seats >= majority_threshold) & draw_resolved).mean())
        p_rep_maj = float(((dem_seats < majority_threshold) & draw_resolved).mean())
    elif vp_tiebreak_party == "D":
        p_dem_maj = float(((dem_seats >= 50) & draw_resolved).mean())
        p_rep_maj = float(((dem_seats <= 49) & draw_resolved).mean())
    else:
        raise ValueError(f"vp_tiebreak_party must be 'R' or 'D', got {vp_tiebreak_party!r}")

    sim = ChamberSimulation(
        seat_draws=dem_seats,
        race_win=wins,
        race_ids=fit.race_ids,
        states=fit.states,
        held_dem=held_dem,
        held_rep=held_rep,
        majority_threshold=majority_threshold,
        p_dem_majority=p_dem_maj,
        p_rep_majority=p_rep_maj,
        p_fifty_fifty=p_fifty,
        expected_dem_seats=float(dem_seats.mean()),
        seat_histogram=hist,
        vp_tiebreak_party=vp_tiebreak_party,
        held_ind=held_ind,
        n_joint_sims=int(n_draws),
        n_posterior_margin_draws=int(n_post),
        unresolved_seat_draws=unresolved_seats,
        p_unresolved_control=p_unresolved,
        expected_unresolved_seats=float(unresolved_seats.mean()),
    )

    contested_ix = contested.set_index("race_id")
    adapter_diagnostics = (fit.diagnostics or {}).get("exceptional_adapter") or {}
    if not adapter_diagnostics and (fit.diagnostics or {}).get("target") == "modeled_candidate_margin":
        adapter_diagnostics = fit.diagnostics or {}
    adapter_records = {
        str(record.get("race_id")): record
        for record in adapter_diagnostics.get("records", [])
    }
    summaries = []
    for i, rid in enumerate(fit.race_ids):
        draw_i = margins[:, i]
        p_modeled = float(wins[:, i].mean())
        state = fit.states[i]
        row = contested_ix.loc[rid] if rid in contested_ix.index else None
        held_raw = None if row is None else row.get("held_by")
        held_by = None if held_raw is None or pd.isna(held_raw) else str(held_raw)
        prior_raw = None if row is None else row.get("prior_lean")
        prior_lean = (
            None if prior_raw is None or pd.isna(prior_raw) else float(prior_raw)
        )
        incumbent = None
        if row is not None and not pd.isna(row.get("incumbent_party")):
            incumbent = str(row.get("incumbent_party"))
        is_open = None if row is None or pd.isna(row.get("is_open")) else bool(row.get("is_open"))
        seat_class = None if row is None else str(row.get("seat_class", "II"))
        election_phase = None if row is None else str(row.get("election_phase") or "general")
        vacancy_reason = None
        if row is not None and "vacancy_reason" in contested_ix.columns:
            vr = row.get("vacancy_reason")
            vacancy_reason = None if vr is None or (isinstance(vr, float) and pd.isna(vr)) else str(vr)
        ticket = ticket_for_state(state)
        dem_name = str(ticket["dem_name"])
        rep_name = str(ticket["rep_name"])
        modeled_name = (
            dem_name
            if row is None or pd.isna(row.get("modeled_candidate_name"))
            else str(row.get("modeled_candidate_name"))
        )
        opposing_name = (
            rep_name
            if row is None or pd.isna(row.get("opposing_candidate_name"))
            else str(row.get("opposing_candidate_name"))
        )
        row_ballot_party = None if row is None else row.get("modeled_ballot_party")
        dem_party = str(
            row_ballot_party if pd.notna(row_ballot_party) else ticket.get("dem_party") or "D"
        )
        if dem_party not in {"D", "I"}:
            dem_party = "D"
        mean_m = float(fit.mean_margin[i])
        dem_share = 50.0 + mean_m / 2.0
        rep_share = 100.0 - dem_share
        opposing_party = (
            "R" if row is None or pd.isna(row.get("opposing_ballot_party"))
            else str(row.get("opposing_ballot_party"))
        )
        rating = rating_from_modeled_probability(
            p_modeled,
            modeled_ballot_party=dem_party,
            opposing_ballot_party=opposing_party,
        )
        modeled_caucus = None if row is None else row.get("modeled_caucus")
        opposing_caucus = None if row is None else row.get("opposing_caucus")
        favored_caucus = str(
            modeled_caucus if p_modeled >= 0.5 else opposing_caucus
        )
        if favored_caucus not in {"D", "R"}:
            favored_caucus = "D" if p_modeled >= 0.5 else "R"
        favored_party = dem_party if favored_caucus == "D" else "R"
        held_caucus = (
            str(row.get("held_caucus")) if held_by == "I" and row is not None
            and pd.notna(row.get("held_caucus")) else held_by if held_by in {"D", "R"} else None
        )
        is_flip = None if held_caucus is None else favored_caucus != held_caucus
        favored_candidate_id = None if row is None else row.get(
            "modeled_candidate_id" if p_modeled >= 0.5 else "opposing_candidate_id"
        )
        if rid == alaska_diagnostics.get("race_id"):
            candidate_ids = list(alaska_diagnostics["candidate_ids"])
            candidate_names = list(alaska_diagnostics["candidate_names"])
            parties = list(alaska_diagnostics["ballot_parties"])
            caucuses = list(alaska_diagnostics["caucuses"])
            probabilities = [float(np.mean(alaska_winners == index)) for index in range(len(candidate_ids))]
            favored_index = int(np.argmax(probabilities))
            p_dem_caucus = float(sum(probabilities[index] for index, caucus in enumerate(caucuses) if caucus == "D"))
            summary = {
                "race_id": rid, "state": state, "seat_class": seat_class,
                "contest_structure": "ranked_choice_multiway",
                "modeling_path": "alaska_rcv_adapter",
                "method": alaska_diagnostics.get("method"),
                "candidate_probabilities": [
                    {"candidate_id": candidate_ids[index], "candidate_name": candidate_names[index],
                     "ballot_party": parties[index], "caucus": caucuses[index], "p_win": probabilities[index],
                     "first_choice_estimate": alaska_diagnostics["first_choice_mean"][index],
                     "final_support_estimate": alaska_diagnostics["final_support_mean"][index]}
                    for index in range(len(candidate_ids))
                ],
                "favored_candidate_id": candidate_ids[favored_index],
                "favored_candidate": candidate_names[favored_index],
                "favored_party": parties[favored_index], "favored_caucus": caucuses[favored_index],
                "caucus": caucuses[favored_index], "p_dem_caucus": p_dem_caucus,
                "p_rep_caucus": 1.0 - p_dem_caucus, "p_dem": None, "p_rep": None,
                "p_modeled_candidate": None, "p_opposing_candidate": None,
                "mean_margin": None, "sd_margin": None, "ci05": None, "ci95": None,
                "prior_lean": prior_lean, "incumbent_party": incumbent, "is_open": is_open,
                "held_by": held_by, "election_phase": election_phase, "vacancy_reason": vacancy_reason,
                "rating": "RCV · limited validation", "exhausted_ballot_share": alaska_diagnostics["exhausted_mean"],
                "uncertainty_metadata": alaska_diagnostics.get("uncertainty"),
                "authoritative_binary_aliases": False,
            }
            summaries.append(summary)
            continue
        multiway_match = next(
            (
                block for block in multiway_rows
                if isinstance(block, dict) and str(block.get("race_id") or "") == rid
            ),
            None,
        )
        if multiway_match is not None:
            candidate_ids = list(multiway_match["candidate_ids"])
            candidate_names = list(multiway_match["candidate_names"])
            parties = list(multiway_match["ballot_parties"])
            caucuses = list(multiway_match["caucuses"])
            winner_ids = np.asarray(multiway_match.get("winner_candidate_ids") or [], dtype=object)
            probabilities = [
                float(np.mean(winner_ids == candidate_id)) for candidate_id in candidate_ids
            ]
            mass = float(sum(probabilities))
            if mass > 0 and abs(mass - 1.0) > 1e-6:
                # Fail-closed / tied draws leave residual probability mass.
                residual = max(0.0, 1.0 - mass)
                probabilities = [p + residual / len(probabilities) for p in probabilities]
            favored_index = int(np.argmax(probabilities))
            uncertainty = multiway_match.get("uncertainty") or {}
            p_unknown = float(multiway_match.get("p_unknown_caucus") or 0.0)
            p_dem_caucus = uncertainty.get("p_dem_caucus")
            p_rep_caucus = uncertainty.get("p_rep_caucus")
            if p_dem_caucus is None:
                p_dem_caucus = float(
                    sum(
                        probabilities[index]
                        for index, caucus in enumerate(caucuses)
                        if caucus == "D"
                    )
                )
            if p_rep_caucus is None:
                p_rep_caucus = float(
                    sum(
                        probabilities[index]
                        for index, caucus in enumerate(caucuses)
                        if caucus == "R"
                    )
                )
            summary = {
                "race_id": rid,
                "state": state,
                "seat_class": seat_class,
                "contest_structure": "multiway_plurality",
                "modeling_path": "multiway_plurality_adapter",
                "validation_status": uncertainty.get("support_status")
                or multiway_match.get("method"),
                "method": multiway_match.get("method"),
                "candidate_probabilities": [
                    {
                        "candidate_id": candidate_ids[index],
                        "candidate_name": candidate_names[index],
                        "ballot_party": parties[index],
                        "caucus": caucuses[index],
                        "p_win": probabilities[index],
                        "share_estimate": (multiway_match.get("share_mean") or [None] * len(candidate_ids))[index],
                    }
                    for index in range(len(candidate_ids))
                ],
                "favored_candidate_id": candidate_ids[favored_index],
                "favored_candidate": candidate_names[favored_index],
                "favored_party": parties[favored_index],
                "favored_ballot_party": parties[favored_index],
                "favored_caucus": caucuses[favored_index],
                "caucus": caucuses[favored_index],
                "p_dem_caucus": p_dem_caucus,
                "p_rep_caucus": p_rep_caucus,
                "p_unknown_caucus": p_unknown,
                "p_dem": None,
                "p_rep": None,
                "p_modeled_candidate": None,
                "p_opposing_candidate": None,
                "mean_margin": None,
                "sd_margin": None,
                "ci05": None,
                "ci95": None,
                "prior_lean": prior_lean,
                "incumbent_party": incumbent,
                "is_open": is_open,
                "held_by": held_by,
                "election_phase": election_phase,
                "vacancy_reason": vacancy_reason,
                "rating": "Multiway plurality · limited validation",
                "uncertainty_metadata": uncertainty,
                "authoritative_binary_aliases": False,
            }
            summaries.append(summary)
            continue
        summary = {
                "race_id": rid,
                "state": state,
                "seat_class": seat_class,
                "p_modeled_candidate": p_modeled,
                "p_opposing_candidate": float(1.0 - p_modeled),
                "modeled_candidate_margin": mean_m,
                "modeled_candidate_share": round(dem_share, 1),
                "opposing_candidate_share": round(rep_share, 1),
                "mean_margin": mean_m,
                "sd_margin": float(fit.sd_margin[i]),
                "ci05": float(np.quantile(draw_i, 0.05)),
                "ci95": float(np.quantile(draw_i, 0.95)),
                "prior_lean": prior_lean,
                "incumbent_party": incumbent,
                "is_open": is_open,
                "held_by": held_by,
                "election_phase": election_phase,
                "vacancy_reason": vacancy_reason,
                "modeled_candidate": modeled_name,
                "opposing_candidate": opposing_name,
                "modeled_candidate_id": None if row is None else row.get("modeled_candidate_id"),
                "opposing_candidate_id": None if row is None else row.get("opposing_candidate_id"),
                "modeled_ballot_party": dem_party,
                "opposing_ballot_party": opposing_party,
                "modeled_caucus": modeled_caucus,
                "opposing_caucus": opposing_caucus,
                "modeled_caucus_basis": None if row is None else row.get("modeled_caucus_basis"),
                "opposing_caucus_basis": None if row is None else row.get("opposing_caucus_basis"),
                "favored_candidate_id": favored_candidate_id,
                "caucus": favored_caucus,
                "rating": rating,
                "is_flip": is_flip,
                "favored_party": favored_party,
                "favored_caucus": favored_caucus,
                "modeling_path": (
                    "binary_non_major_adapter"
                    if row is not None
                    and str(row.get("contest_structure") or "")
                    == "non_major_party_vs_republican"
                    else "ordinary_stack"
                ),
            }
        if dem_party == "D":
            # Backward-compatible aliases are only semantically valid for an
            # actual Democratic-vs-Republican ballot contest.
            summary.update({
                "p_dem": p_modeled,
                "p_rep": float(1.0 - p_modeled),
                "dem_share": round(dem_share, 1),
                "rep_share": round(rep_share, 1),
                "dem_candidate": dem_name,
                "rep_candidate": rep_name,
                "dem_party": dem_party,
            })
        else:
            adapter_record = adapter_records.get(str(rid), {})
            summary.update({
                "method": adapter_diagnostics.get("method"),
                "uncertainty_metadata": {
                    key: adapter_record.get(key)
                    for key in (
                        "n_candidate_compatible_polls",
                        "enop",
                        "posterior_sd",
                        "predictive_sd",
                        "house_effect_treatment",
                    )
                },
            })
        summaries.append(summary)
    summaries.sort(key=lambda x: abs(float(
        x.get("p_modeled_candidate")
        if x.get("p_modeled_candidate") is not None else x.get("p_dem_caucus", 0.5)
    ) - 0.5))
    return sim, summaries


def independent_bernoulli_foil(
    summaries: list[dict], held_dem: int, n_draws: int = 5000, seed: int = 1
) -> np.ndarray:
    """Documented foil: independent Bernoulli from marginals (NOT used for production totals)."""
    rng = np.random.default_rng(seed)
    ps = np.array([
        s.get("p_modeled_candidate")
        if s.get("p_modeled_candidate") is not None else s.get("p_dem_caucus")
        for s in summaries
    ], dtype=float)
    wins = rng.random((n_draws, len(ps))) < ps
    return held_dem + wins.sum(axis=1)
