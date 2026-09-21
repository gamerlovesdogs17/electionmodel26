"""Build internal per-subject diagnostics from model stages without false additivity."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from midterms.model.fundamentals import COEF, feature_row
from midterms.model.poll_weights import attach_poll_weights
from midterms.validation.decomposition_schema import Decomposition, DiagnosticTerm


def build_decomposition_rows(
    *, races: pd.DataFrame, polls: pd.DataFrame, as_of,
    race_ids: list[str], core_means: np.ndarray, core_sds: np.ndarray,
    final_means: np.ndarray, final_sds: np.ndarray,
    prior_provenance_by_state: dict[str, dict[str, Any]],
    generic_ballot: float,
    overlay_shifts: dict[str, np.ndarray],
    error_budget: dict[str, Any] | None = None,
    exact_overlay_chain: bool = False,
) -> list[dict[str, Any]]:
    """Keep anchor terms, observed polling, and overlays in distinct roles."""
    n = len(race_ids)
    vectors = [core_means, core_sds, final_means, final_sds, *overlay_shifts.values()]
    if any(np.asarray(vector).shape != (n,) for vector in vectors):
        raise ValueError("decomposition vectors must align with race_ids")
    if len(set(race_ids)) != n:
        raise ValueError("duplicate decomposition subject")
    indexed = races.set_index("race_id")
    weighted = attach_poll_weights(polls, as_of=as_of) if len(polls) else pd.DataFrame()
    output: list[dict[str, Any]] = []
    for i, race_id in enumerate(race_ids):
        if race_id not in indexed.index:
            raise ValueError(f"race missing from decomposition input: {race_id}")
        row = indexed.loc[race_id]
        if isinstance(row, pd.DataFrame):
            raise ValueError(f"duplicate race row in decomposition input: {race_id}")
        state = str(row["state"])
        provenance = prior_provenance_by_state.get(state)
        if provenance is None:
            raise ValueError(f"prior provenance missing from decomposition: {state}")
        feats = feature_row(row, generic_ballot=generic_ballot)
        contributions = {name: float(COEF[name] * feats[name]) for name in COEF}
        anchor = sum(contributions.values())

        def term(name: str, value: float, kind: str, source: str, note: str = "") -> DiagnosticTerm:
            return DiagnosticTerm(name=name, value=float(value), kind=kind, source=source, note=note)

        terms = [
            term(name, value, "additive_location", "reference_fundamentals_anchor",
                 "Adds only within the reference anchor; do not add to stacked core")
            for name, value in contributions.items() if name != "prior_lean"
        ]
        if len(weighted):
            observed = weighted.loc[weighted["race_id"].eq(race_id)]
            poll_count = int(len(observed))
            if len(observed):
                values = pd.to_numeric(observed["two_party_margin"], errors="coerce")
                weights = pd.to_numeric(observed["influence_weight"], errors="coerce")
                valid = values.notna() & weights.notna() & weights.gt(0)
                if valid.any():
                    poll_location = float(np.average(values[valid], weights=weights[valid]))
                    terms.append(term(
                        "weighted_observed_poll_location", poll_location,
                        "observation_location", "available_poll_evidence",
                        "Descriptive weighted observation; not a standalone posterior effect",
                    ))
                    enop = float(observed["enop_race"].iloc[0])
                else:
                    enop = None
            else:
                enop = None
        else:
            enop = None
            poll_count = 0
        terms.append(term("core_scale", float(core_sds[i]), "uncertainty_component",
                          "stacked_core", "Marginal SD before optional overlays"))
        for name, vector in overlay_shifts.items():
            terms.append(term(name, float(vector[i]), "overlay_shift", name))
        for name in ("terminal_nat_sd", "terminal_race_sd", "similarity_terminal_sd"):
            value = (error_budget or {}).get(name)
            if value is not None:
                terms.append(term(name, float(value), "uncertainty_component", "error_budget",
                                  "Scale parameter; does not add linearly to final SD"))
        if not exact_overlay_chain:
            residual = float(final_means[i] - core_means[i] - sum(float(v[i]) for v in overlay_shifts.values()))
            if abs(residual) > 1e-10:
                terms.append(term("joint_or_calibration_location_difference", residual,
                                  "nonlinear_joint_effect", "post_overlay_draw_layer"))
        report = Decomposition(
            subject_id=race_id,
            base_prior=term("state_prior", float(row["prior_lean"]), "additive_location",
                            "point_in_time_presidential_prior"),
            prior_provenance=provenance,
            fundamentals_anchor=term("fundamentals_anchor", anchor, "posterior_location",
                                     "reference_fundamentals_anchor",
                                     "Deterministic prior location; not additive to stacked core"),
            stacked_core_location=term("stacked_core_location", float(core_means[i]),
                                       "posterior_location", "stacked_predictive_draws"),
            final_location=term("final_location", float(final_means[i]),
                                "posterior_location", "final_predictive_draws"),
            final_scale=term("final_scale", float(final_sds[i]),
                             "uncertainty_component", "final_predictive_draws"),
            terms=tuple(terms),
            effective_sample_size=enop,
            observation_count=poll_count,
            exact_overlay_chain=exact_overlay_chain,
        )
        output.append(report.as_dict())
    return output
