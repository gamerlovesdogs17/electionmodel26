"""Explanatory IA/KS/NE cross-race decomposition (no ordering gates / no tuning)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION


def _decomp_row(decomp: dict[str, Any], race_id: str) -> dict[str, Any] | None:
    for row in (
        decomp.get("rows")
        or decomp.get("races")
        or decomp.get("subjects")
        or []
    ):
        sid = str(row.get("subject_id") or row.get("race_id") or "")
        if sid == race_id:
            return row
    return None


def _forecast_race(forecast: dict[str, Any], race_id: str) -> dict[str, Any]:
    for row in forecast.get("races") or []:
        if str(row.get("race_id")) == race_id:
            return row
    raise KeyError(race_id)


def _ordinary_summary(row: dict[str, Any], decomp: dict[str, Any] | None) -> dict[str, Any]:
    terms = []
    if decomp:
        for term in decomp.get("terms") or []:
            terms.append(
                {
                    "name": term.get("name"),
                    "value": term.get("value"),
                    "kind": term.get("kind"),
                    "source": term.get("source"),
                }
            )
    return {
        "race_id": row["race_id"],
        "state": row["state"],
        "modeling_path": row.get("modeling_path") or "ordinary_stack",
        "p_dem": row.get("p_dem"),
        "mean_margin": row.get("mean_margin"),
        "sd_margin": row.get("sd_margin"),
        "rating": row.get("rating"),
        "prior_lean": row.get("prior_lean"),
        "decomposition_available": decomp is not None,
        "base_prior": (decomp or {}).get("base_prior"),
        "fundamentals_anchor": (decomp or {}).get("fundamentals_anchor"),
        "stacked_core_location": (decomp or {}).get("stacked_core_location"),
        "final_location": (decomp or {}).get("final_location"),
        "final_scale": (decomp or {}).get("final_scale"),
        "terms": terms,
        "outside_model_ordering_used": False,
    }


def _ne_from_adapter(row: dict[str, Any], coverage: dict[str, Any] | None) -> dict[str, Any]:
    meta = row.get("uncertainty_metadata") or {}
    cov_row = None
    if coverage:
        for item in coverage.get("races") or []:
            if str(item.get("race_id")) == row["race_id"]:
                cov_row = item
                break
    return {
        "race_id": row["race_id"],
        "state": row["state"],
        "modeling_path": row.get("modeling_path"),
        "p_modeled_candidate": row.get("p_modeled_candidate"),
        "p_opposing_candidate": row.get("p_opposing_candidate"),
        "modeled_candidate_margin": row.get("modeled_candidate_margin"),
        "sd_margin": row.get("sd_margin"),
        "rating": row.get("rating"),
        "prior_lean": row.get("prior_lean"),
        "decomposition_available": False,
        "decomposition_omission_reason": (
            "race_decomposition_latest covers ordinary binary races only; "
            "NE terms reconstructed from non-major adapter forecast fields"
        ),
        "adapter_terms": [
            {
                "name": "presidential_relative_state_anchor",
                "value": row.get("prior_lean"),
                "kind": "weak_geographic_location",
                "source": "binary_non_major_adapter",
            },
            {
                "name": "exceptional_candidate_deviation_prior_sd",
                "value": 30.0,
                "kind": "structural_prior_scale",
                "source": "binary_non_major_adapter",
            },
            {
                "name": "posterior_modeled_candidate_margin",
                "value": row.get("modeled_candidate_margin"),
                "kind": "posterior_location",
                "source": "binary_non_major_adapter",
            },
            {
                "name": "n_candidate_compatible_polls",
                "value": meta.get("n_candidate_compatible_polls"),
                "kind": "coverage",
                "source": "uncertainty_metadata",
            },
            {
                "name": "enop",
                "value": meta.get("enop"),
                "kind": "coverage",
                "source": "uncertainty_metadata",
            },
            {
                "name": "posterior_sd",
                "value": meta.get("posterior_sd"),
                "kind": "uncertainty_component",
                "source": "uncertainty_metadata",
            },
            {
                "name": "predictive_sd",
                "value": meta.get("predictive_sd"),
                "kind": "uncertainty_component",
                "source": "uncertainty_metadata",
            },
        ],
        "poll_coverage": cov_row,
        "outside_model_ordering_used": False,
        "cross_race_probability_ordering_gate": False,
    }


def build_ia_ks_ne_decomposition(
    *,
    forecast_path=None,
    decomp_path=None,
    coverage_path=None,
) -> dict[str, Any]:
    forecast_path = forecast_path or (ARTIFACTS_DIR / "forecast_latest.json")
    decomp_path = decomp_path or (ARTIFACTS_DIR / "race_decomposition_latest.json")
    coverage_path = coverage_path or (ARTIFACTS_DIR / "current_race_poll_coverage_v0923.json")
    forecast = json.loads(forecast_path.read_text(encoding="utf-8"))
    decomp = (
        json.loads(decomp_path.read_text(encoding="utf-8")) if decomp_path.is_file() else {}
    )
    coverage = (
        json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.is_file() else None
    )
    ia = _forecast_race(forecast, "senate-2026-IA")
    ks = _forecast_race(forecast, "senate-2026-KS")
    ne = _forecast_race(forecast, "senate-2026-NE")
    return {
        "schema_version": "ia-ks-ne-cross-race-decomposition-v0924",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "source_forecast_model_version": forecast.get("model_version"),
        "note": (
            "Explanatory only. No cross-race ordering gates and no tuning toward "
            "outside-model IA/KS/NE probability orderings."
        ),
        "outside_model_probabilities_used": False,
        "races": {
            "IA": _ordinary_summary(ia, _decomp_row(decomp, "senate-2026-IA")),
            "KS": _ordinary_summary(ks, _decomp_row(decomp, "senate-2026-KS")),
            "NE": _ne_from_adapter(ne, coverage),
        },
        "summary": {
            "IA_p_dem": ia.get("p_dem"),
            "KS_p_dem": ks.get("p_dem"),
            "NE_p_modeled_candidate": ne.get("p_modeled_candidate"),
            "NE_modeling_path": ne.get("modeling_path"),
            "comparable_on_same_estimand": False,
            "reason_not_comparable": (
                "IA/KS are ordinary D-v-R stack margins; NE is modeled_candidate_margin "
                "on a limited-validation binary non-major adapter."
            ),
        },
    }


def write_ia_ks_ne_decomposition() -> dict[str, Any]:
    payload = build_ia_ks_ne_decomposition()
    path = ARTIFACTS_DIR / "ia_ks_ne_cross_race_decomposition_v0924.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload
