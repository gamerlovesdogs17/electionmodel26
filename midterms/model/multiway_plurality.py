"""Generic multi-candidate plurality forecasting adapter.

Produces joint candidate-share draws for K-candidate plurality races. Winner on
each draw is argmax(shares). Caucus for chamber accounting comes only from an
explicit candidate caucus mapping; unknown caucus fails closed for that draw.

Historical election-result analogs alone do not activate win probabilities.
Activation requires the scorable historical poll-validation artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import ARTIFACTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.model.pymc_model import FitResult

MULTIWAY_CONTEST_STRUCTURE = "multiway_plurality"
MULTIWAY_MODELING_PATH = "multiway_plurality_adapter"
MULTIWAY_METHOD = "limited_validation_multiway_plurality_model-v1"
MULTIWAY_SUPPORT_UNSUPPORTED = "unsupported"
MULTIWAY_SUPPORT_LIMITED = "limited_supported"
MULTIWAY_VALIDATION_CLASS = "limited_validation_multiway_plurality_model"
ADAPTER_SPEC_VERSION = "multiway-plurality-adapter-v1"
POLL_QUESTION_SCHEMA = "multiway-poll-question-class-v1"
CANDIDATE_SHARE_SCHEMA = "poll-candidate-shares-v1"
PRIOR_CONCENTRATION = 4.0
POLL_ERROR_FLOOR = 0.04
MIN_SHARE_FLOOR = 1e-6
COMMON_SHOCK_STRENGTH = 0.08
HEAVY_TAIL_DF = 5.0

ANALOG_SELECTION_RULE = {
    "id": "senate-plurality-multiway-analogs-v1",
    "predeclared_before_scoring": True,
    "include": [
        "US Senate general elections 1990–2024",
        "at least three ballot-qualified candidates",
        "plurality (not RCV, not majority-runoff) institutional rule",
    ],
    "exclude": [
        "Alaska RCV / IRV contests",
        "Louisiana jungle-primary / majority-threshold contests",
        "Georgia runoff-threshold contests",
        "same-party generals without multi-party field",
    ],
}

QUESTION_CLASS_FULL_FIELD = "full_field"
QUESTION_CLASS_PRINCIPAL_FIELD = "full_principal_field_minor_omitted"
QUESTION_CLASS_PARTIAL = "partial_field"
QUESTION_CLASS_BINARY_HYPOTHETICAL = "binary_hypothetical_pairwise"
QUESTION_CLASS_OBSOLETE = "obsolete_candidate_field"


@dataclass(frozen=True)
class MultiwayCandidate:
    candidate_id: str
    candidate_name: str
    ballot_party: str
    caucus: str | None


@dataclass
class MultiwayPluralityFit:
    race_id: str
    state: str
    candidate_ids: list[str]
    candidate_names: list[str]
    ballot_parties: list[str]
    caucuses: list[str | None]
    share_draws: np.ndarray
    winner_indices: np.ndarray
    winner_candidate_ids: list[str | None]
    chamber_dem_win: np.ndarray
    fail_closed_draws: np.ndarray
    p_unknown_caucus: float
    diagnostics: dict[str, Any]


def adapter_specification() -> dict[str, Any]:
    return {
        "adapter_spec_version": ADAPTER_SPEC_VERSION,
        "method": MULTIWAY_METHOD,
        "modeling_path": MULTIWAY_MODELING_PATH,
        "contest_structure": MULTIWAY_CONTEST_STRUCTURE,
        "target": "candidate_share_draws_and_plurality_winner",
        "poll_question_schema": POLL_QUESTION_SCHEMA,
        "candidate_share_schema": CANDIDATE_SHARE_SCHEMA,
        "prior_concentration": PRIOR_CONCENTRATION,
        "poll_error_floor": POLL_ERROR_FLOOR,
        "common_shock_strength": COMMON_SHOCK_STRENGTH,
        "heavy_tail_df": HEAVY_TAIL_DF,
        "binary_poll_treatment": "ignored_not_translated_to_shares",
        "unknown_caucus_policy": "fail_closed_draw_level_unknown_caucus",
        "activation_requires": "scorable_historical_multiway_poll_validation",
    }


def shares_sum_to_one(shares: np.ndarray, *, atol: float = 1e-9) -> bool:
    array = np.asarray(shares, dtype=float)
    if array.ndim != 2 or array.shape[1] < 2:
        return False
    if not np.isfinite(array).all() or (array < -atol).any():
        return False
    totals = array.sum(axis=1)
    return bool(np.all(np.abs(totals - 1.0) <= atol))


def plurality_winners(
    shares: np.ndarray,
    candidate_ids: list[str],
    *,
    residual_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return draw-wise plurality winners; residual/unknown caucus fails closed."""
    array = np.asarray(shares, dtype=float)
    if not shares_sum_to_one(array):
        raise ValueError("candidate shares must be finite, nonnegative, and sum to 1")
    if array.shape[1] != len(candidate_ids):
        raise ValueError("candidate_ids length must match share columns")
    residual_ids = residual_ids or set()
    order = np.argsort(-array, axis=1, kind="stable")
    top = order[:, 0]
    second = order[:, 1]
    tied = np.isclose(array[np.arange(len(array)), top], array[np.arange(len(array)), second])
    winners: list[str | None] = []
    fail_closed = np.zeros(len(array), dtype=bool)
    for draw, idx in enumerate(top):
        if tied[draw]:
            winners.append(None)
            fail_closed[draw] = True
            continue
        candidate_id = candidate_ids[int(idx)]
        if candidate_id in residual_ids:
            winners.append(None)
            fail_closed[draw] = True
            continue
        winners.append(candidate_id)
    return {
        "schema_version": "multiway-plurality-winners-v1",
        "candidate_ids": list(candidate_ids),
        "winner_candidate_ids": winners,
        "fail_closed_draws": fail_closed.tolist(),
        "n_fail_closed": int(fail_closed.sum()),
        "n_resolved": int((~fail_closed).sum()),
    }


def caucus_from_winners(
    winner_candidate_ids: list[str | None],
    caucus_by_candidate: dict[str, str | None],
) -> dict[str, Any]:
    """Map winners to caucus; missing/unknown caucus fails closed for that draw."""
    caucus_draws: list[str | None] = []
    fail_closed = []
    for winner in winner_candidate_ids:
        if winner is None:
            caucus_draws.append(None)
            fail_closed.append(True)
            continue
        caucus = caucus_by_candidate.get(winner)
        if caucus not in {"D", "R"}:
            caucus_draws.append(None)
            fail_closed.append(True)
        else:
            caucus_draws.append(caucus)
            fail_closed.append(False)
    resolved = [c for c, failed in zip(caucus_draws, fail_closed) if not failed]
    return {
        "schema_version": "multiway-plurality-caucus-v1",
        "caucus_draws": caucus_draws,
        "fail_closed_draws": fail_closed,
        "n_fail_closed": int(sum(fail_closed)),
        "p_unknown_caucus": float(np.mean(fail_closed)) if fail_closed else 0.0,
        "p_dem_caucus": (
            float(np.mean([c == "D" for c in resolved])) if resolved else None
        ),
        "p_rep_caucus": (
            float(np.mean([c == "R" for c in resolved])) if resolved else None
        ),
    }


def historical_analog_support_report(*, n_analogs: int, min_analogs: int = 8) -> dict[str, Any]:
    """Structural analog inventory only — does NOT activate forecast probabilities.

    Presence of certified election-result rows is evidence of contest structure,
    not validation of a probabilistic multiway forecast model.
    """
    return {
        "schema_version": "multiway-plurality-analog-support-v1",
        "analog_selection_rule": ANALOG_SELECTION_RULE,
        "n_analogs": int(n_analogs),
        "min_analogs_for_probability_model": int(min_analogs),
        "probability_model_support_status": MULTIWAY_SUPPORT_UNSUPPORTED,
        "win_probability_status": "fail_closed",
        "structural_inventory_only": True,
        "activates_forecast_probabilities": False,
        "note": (
            "Election-result analogs classify contest structure only; "
            "scorable historical poll validation is required to support win probabilities"
        ),
    }


def unsupported_multiway_race_payload(
    *,
    race_id: str,
    candidates: list[MultiwayCandidate],
    n_analogs: int = 0,
) -> dict[str, Any]:
    """Fail-closed forecast fields for a correctly classified multiway race."""
    support = historical_analog_support_report(n_analogs=n_analogs)
    return {
        "race_id": race_id,
        "contest_structure": MULTIWAY_CONTEST_STRUCTURE,
        "modeling_path": MULTIWAY_MODELING_PATH,
        "probability_model_support_status": support["probability_model_support_status"],
        "win_probability_status": support["win_probability_status"],
        "candidate_probabilities": [
            {
                "candidate_id": c.candidate_id,
                "candidate_name": c.candidate_name,
                "ballot_party": c.ballot_party,
                "caucus": c.caucus,
                "p_win": None,
            }
            for c in candidates
        ],
        "analog_support": support,
        "authoritative_binary_aliases": False,
    }


def classify_multiway_question(
    resolved_candidate_ids: list[str],
    *,
    ballot_candidate_ids: list[str],
    principal_candidate_ids: list[str] | None = None,
) -> str:
    """Classify a poll question relative to the current ballot field."""
    observed = {str(x) for x in resolved_candidate_ids if x}
    ballot = [str(x) for x in ballot_candidate_ids]
    principals = [str(x) for x in (principal_candidate_ids or ballot)]
    if len(observed) < 2:
        return QUESTION_CLASS_OBSOLETE
    if len(observed) == 2:
        return QUESTION_CLASS_BINARY_HYPOTHETICAL
    if set(ballot).issubset(observed) and observed.issubset(set(ballot)):
        return QUESTION_CLASS_FULL_FIELD
    if set(principals).issubset(observed) and observed.issubset(set(ballot)):
        return QUESTION_CLASS_PRINCIPAL_FIELD
    if observed & set(ballot):
        if observed - set(ballot):
            return QUESTION_CLASS_OBSOLETE
        return QUESTION_CLASS_PARTIAL
    return QUESTION_CLASS_OBSOLETE


def load_multiway_validation_status(
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Read the historical multiway poll-validation artifact if present."""
    artifact = Path(path or ARTIFACTS_DIR / "multiway_plurality_validation_latest.json")
    if not artifact.is_file():
        return {
            "probability_model_support_status": MULTIWAY_SUPPORT_UNSUPPORTED,
            "validation_class": None,
            "n_scorable_cases": 0,
            "activates_forecast_probabilities": False,
            "artifact_present": False,
        }
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    status = str(payload.get("probability_model_support_status") or MULTIWAY_SUPPORT_UNSUPPORTED)
    n_scorable = int((payload.get("summary") or {}).get("n_scorable_cases") or 0)
    activates = (
        status == MULTIWAY_SUPPORT_LIMITED
        and payload.get("validation_class") == MULTIWAY_VALIDATION_CLASS
        and n_scorable > 0
        and payload.get("activates_forecast_probabilities") is True
    )
    return {
        "probability_model_support_status": status if activates else MULTIWAY_SUPPORT_UNSUPPORTED,
        "validation_class": payload.get("validation_class"),
        "n_scorable_cases": n_scorable,
        "activates_forecast_probabilities": bool(activates),
        "artifact_present": True,
        "artifact_sha256": payload.get("artifact_sha256"),
        "summary": payload.get("summary") or {},
    }


def multiway_probability_supported() -> tuple[bool, str, str]:
    """Return (supported, status, reason) from the validation artifact gate."""
    status = load_multiway_validation_status()
    if status["activates_forecast_probabilities"]:
        return (
            True,
            MULTIWAY_SUPPORT_LIMITED,
            MULTIWAY_VALIDATION_CLASS,
        )
    return (
        False,
        MULTIWAY_SUPPORT_UNSUPPORTED,
        "multiway_plurality_probability_model_not_historically_supported",
    )


def _ballot_candidates_from_registry_race(race: dict[str, Any] | pd.Series) -> list[MultiwayCandidate]:
    rows = list(race.get("ballot_candidates") or [])
    out: list[MultiwayCandidate] = []
    for row in rows:
        if bool(row.get("withdrawn")):
            continue
        out.append(
            MultiwayCandidate(
                candidate_id=str(row["candidate_id"]),
                candidate_name=str(row.get("candidate_name") or ""),
                ballot_party=str(row.get("ballot_party") or ""),
                caucus=(
                    None
                    if row.get("caucus") in (None, "", "null")
                    else str(row.get("caucus"))
                ),
            )
        )
    return out


def _principal_ids_from_race(race: dict[str, Any] | pd.Series) -> list[str]:
    classification = race.get("contest_classification") or {}
    principals = classification.get("principal_pair") or []
    ids = [str(row.get("candidate_id")) for row in principals if row.get("candidate_id")]
    if ids:
        return ids
    return [c.candidate_id for c in _ballot_candidates_from_registry_race(race)]


def _load_candidate_share_rows(
    race_id: str,
    *,
    as_of: date,
    path: Path | None = None,
) -> pd.DataFrame:
    parquet = path or (NORMALIZED_DIR / "poll_candidate_shares_votehub.parquet")
    if parquet.is_file():
        df = pd.read_parquet(parquet)
        if "race_id" in df.columns:
            df = df[df["race_id"].astype(str).eq(race_id)].copy()
        if "available_at" in df.columns:
            available = pd.to_datetime(df["available_at"], errors="coerce").dt.date
            df = df[available.notna() & available.le(as_of)].copy()
        return df.reset_index(drop=True)
    return _extract_candidate_shares_from_votehub(race_id, as_of=as_of)


def _extract_candidate_shares_from_votehub(race_id: str, *, as_of: date) -> pd.DataFrame:
    """Fallback: rebuild candidate-level rows from the live VoteHub senator feed."""
    from midterms.evidence.current_candidates import load_current_candidate_registry
    from midterms.evidence.ingest import extract_candidate_level_answers
    from midterms.evidence.race_scoped_identity import build_race_identity_index

    raw_path = RAW_DIR / "external" / "votehub_us_senator.json"
    if not raw_path.is_file():
        return pd.DataFrame()
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    polls = payload.get("polls", payload) if isinstance(payload, dict) else payload
    state = race_id.rsplit("-", 1)[-1]
    registry = load_current_candidate_registry()
    identity_index = build_race_identity_index(list(registry.get("races") or []))
    rows: list[dict[str, Any]] = []
    for poll in polls or []:
        subject = str(poll.get("subject") or "")
        if state not in subject and state not in str(poll.get("seat_name") or ""):
            # Prefer explicit seat match when subject encoding is year-only.
            seat = str(poll.get("seat_name") or "")
            state_names = {
                "MT": "Montana", "NE": "Nebraska", "ID": "Idaho", "SD": "South Dakota",
            }
            if state_names.get(state, state) not in seat and state_names.get(state, state) not in subject:
                continue
        available_raw = poll.get("created_at") or poll.get("end_date") or poll.get("start_date")
        if not available_raw:
            continue
        available = pd.Timestamp(str(available_raw)[:10]).date()
        if available > as_of:
            continue
        extracted = extract_candidate_level_answers(
            poll.get("answers") or [],
            race_id=race_id,
            identity_index=identity_index,
            poll_id=f"vh-{poll.get('id')}",
            study_id=f"vh-study-{poll.get('id')}",
            question_id=f"vh-{poll.get('id')}:q0",
            field_start=poll.get("start_date"),
            field_end=poll.get("end_date"),
            available_at=str(available),
            source_lineage="votehub-live-fallback-v1",
        )
        for row in extracted["candidates"]:
            row = dict(row)
            row["sample_size"] = poll.get("sample_size")
            row["pollster"] = poll.get("pollster")
            row["population"] = poll.get("population")
            rows.append(row)
    return pd.DataFrame(rows)


def _usable_share_observations(
    share_rows: pd.DataFrame,
    *,
    ballot_ids: list[str],
    principal_ids: list[str],
) -> list[dict[str, Any]]:
    if share_rows.empty:
        return []
    observations: list[dict[str, Any]] = []
    group_cols = [c for c in ("poll_id", "question_id", "study_id") if c in share_rows.columns]
    if not group_cols:
        return []
    for _, group in share_rows.groupby(group_cols, dropna=False):
        resolved = group[group.get("candidate_id").notna()] if "candidate_id" in group.columns else group
        resolved = resolved[resolved["resolution_status"].astype(str).eq("race_scoped")] if "resolution_status" in resolved.columns else resolved
        ids = [str(x) for x in resolved["candidate_id"].tolist()]
        qclass = classify_multiway_question(
            ids, ballot_candidate_ids=ballot_ids, principal_candidate_ids=principal_ids,
        )
        if qclass not in {QUESTION_CLASS_FULL_FIELD, QUESTION_CLASS_PRINCIPAL_FIELD}:
            continue
        shares = {str(row.candidate_id): float(row.raw_share) for row in resolved.itertuples()}
        vector = np.array([max(shares.get(cid, 0.0), 0.0) for cid in ballot_ids], dtype=float)
        total = float(vector.sum())
        if total <= 0:
            continue
        vector = vector / total
        if "sample_size" in group.columns:
            sample_series = pd.to_numeric(group["sample_size"], errors="coerce").dropna()
            sample = float(sample_series.max()) if len(sample_series) else 400.0
        else:
            sample = 400.0
        observations.append({
            "question_class": qclass,
            "shares": vector,
            "sample_size": sample,
            "poll_id": str(group.iloc[0].get("poll_id") or ""),
        })
    return observations


def _dirichlet_posterior(
    observations: list[dict[str, Any]],
    *,
    n_candidates: int,
) -> np.ndarray:
    alpha = np.full(n_candidates, PRIOR_CONCENTRATION / max(n_candidates, 1), dtype=float)
    for obs in observations:
        weight = max(float(obs["sample_size"]), 1.0)
        # Soften with poll-error floor so tiny samples cannot dominate.
        effective = weight / (1.0 + weight * POLL_ERROR_FLOOR**2)
        alpha = alpha + effective * np.asarray(obs["shares"], dtype=float)
    return alpha


def _common_shock(base_fit: FitResult | None, n_draws: int, rng: np.random.Generator) -> np.ndarray:
    if base_fit is None or base_fit.draws_margin.size == 0:
        values = rng.standard_normal(n_draws)
    else:
        if base_fit.draws_margin.shape[0] != n_draws:
            raise ValueError("multiway and ordinary draw rows must align")
        scale = np.where(np.asarray(base_fit.sd_margin) > 1e-9, base_fit.sd_margin, 1.0)
        values = np.mean((base_fit.draws_margin - base_fit.mean_margin) / scale, axis=1)
    values = np.asarray(values, dtype=float) - float(np.mean(values))
    sd = float(np.std(values))
    return values / sd if sd > 1e-9 else rng.standard_normal(n_draws)


def fit_multiway_plurality_race(
    race: dict[str, Any] | pd.Series,
    *,
    as_of: str | date,
    n_draws: int,
    seed: int,
    base_fit: FitResult | None = None,
    share_rows: pd.DataFrame | None = None,
    require_supported: bool = True,
) -> MultiwayPluralityFit | None:
    """Fit one multiway plurality race; return None when unsupported/withheld."""
    race_id = str(race.get("race_id") or "")
    if str(race.get("contest_structure") or "") != MULTIWAY_CONTEST_STRUCTURE:
        return None
    supported, support_status, support_reason = multiway_probability_supported()
    candidates = _ballot_candidates_from_registry_race(race)
    if len(candidates) < 3:
        return None
    if require_supported and not supported:
        return None

    cutoff = pd.Timestamp(as_of).date()
    ballot_ids = [c.candidate_id for c in candidates]
    principal_ids = _principal_ids_from_race(race)
    rows = share_rows if share_rows is not None else _load_candidate_share_rows(race_id, as_of=cutoff)
    observations = _usable_share_observations(
        rows, ballot_ids=ballot_ids, principal_ids=principal_ids,
    )
    if not observations:
        return None

    alpha = _dirichlet_posterior(observations, n_candidates=len(candidates))
    rng = np.random.default_rng(int(seed))
    share_draws = rng.dirichlet(alpha, size=int(n_draws))
    # Mild shared national shock via logistic-normal perturbation.
    shock = _common_shock(base_fit, int(n_draws), rng)
    logits = np.log(np.clip(share_draws, MIN_SHARE_FLOOR, None))
    logits = logits + (COMMON_SHOCK_STRENGTH * shock)[:, None]
    # Heavy-tailed race-level residual on the logit scale.
    race_shock = rng.standard_t(HEAVY_TAIL_DF, size=(int(n_draws), len(candidates)))
    race_shock = race_shock - race_shock.mean(axis=1, keepdims=True)
    logits = logits + 0.15 * race_shock
    exp_logits = np.exp(logits - logits.max(axis=1, keepdims=True))
    share_draws = exp_logits / exp_logits.sum(axis=1, keepdims=True)
    if not shares_sum_to_one(share_draws):
        raise ValueError("multiway share draws failed mass conservation")

    winner_payload = plurality_winners(share_draws, ballot_ids)
    caucus_map = {c.candidate_id: c.caucus for c in candidates}
    caucus_payload = caucus_from_winners(winner_payload["winner_candidate_ids"], caucus_map)
    winner_indices = np.argmax(share_draws, axis=1)
    for draw, failed in enumerate(winner_payload["fail_closed_draws"]):
        if failed:
            winner_indices[draw] = -1
    chamber_dem = np.array(
        [1 if c == "D" else 0 for c in caucus_payload["caucus_draws"]],
        dtype=int,
    )
    # Fail-closed draws are neither Dem nor Rep caucus wins.
    for draw, failed in enumerate(caucus_payload["fail_closed_draws"]):
        if failed:
            chamber_dem[draw] = 0
            winner_indices[draw] = -1

    diagnostics = {
        "method": MULTIWAY_METHOD,
        "validation_class": MULTIWAY_VALIDATION_CLASS,
        "adapter_spec_version": ADAPTER_SPEC_VERSION,
        "adapter_specification": adapter_specification(),
        "race_id": race_id,
        "support_status": support_status,
        "support_reason": support_reason,
        "n_usable_multiway_questions": len(observations),
        "question_classes": sorted({obs["question_class"] for obs in observations}),
        "dirichlet_alpha": alpha.tolist(),
        "mean_shares": share_draws.mean(axis=0).tolist(),
        "p_win": [
            float(np.mean(np.asarray(winner_payload["winner_candidate_ids"], dtype=object) == cid))
            for cid in ballot_ids
        ],
        "p_unknown_caucus": caucus_payload["p_unknown_caucus"],
        "p_dem_caucus": caucus_payload["p_dem_caucus"],
        "p_rep_caucus": caucus_payload["p_rep_caucus"],
        "binary_polls_ignored": True,
        "authoritative_binary_aliases": False,
    }
    return MultiwayPluralityFit(
        race_id=race_id,
        state=str(race.get("state") or race_id.rsplit("-", 1)[-1]),
        candidate_ids=ballot_ids,
        candidate_names=[c.candidate_name for c in candidates],
        ballot_parties=[c.ballot_party for c in candidates],
        caucuses=[c.caucus for c in candidates],
        share_draws=share_draws,
        winner_indices=winner_indices,
        winner_candidate_ids=list(winner_payload["winner_candidate_ids"]),
        chamber_dem_win=chamber_dem.astype(bool),
        fail_closed_draws=np.asarray(caucus_payload["fail_closed_draws"], dtype=bool),
        p_unknown_caucus=float(caucus_payload["p_unknown_caucus"]),
        diagnostics=diagnostics,
    )


def fit_multiway_plurality_adapter(
    races: pd.DataFrame,
    *,
    as_of: str | date,
    n_draws: int,
    seed: int,
    base_fit: FitResult | None = None,
    registry_races: list[dict[str, Any]] | None = None,
    require_supported: bool = True,
) -> list[MultiwayPluralityFit]:
    """Fit all active multiway plurality races that are currently supported."""
    from midterms.evidence.current_candidates import load_current_candidate_registry

    registry = {str(r["race_id"]): r for r in (registry_races or load_current_candidate_registry().get("races") or [])}
    fits: list[MultiwayPluralityFit] = []
    active = races[~races.get("not_up", pd.Series(False, index=races.index)).fillna(False)]
    multiway = active[
        active.get("contest_structure", pd.Series("", index=active.index)).astype(str).eq(
            MULTIWAY_CONTEST_STRUCTURE
        )
    ]
    for i, (_, row) in enumerate(multiway.iterrows()):
        race_id = str(row.get("race_id") or "")
        registry_row = registry.get(race_id) or row.to_dict()
        # Prefer registry ballot field (authoritative candidate identities).
        fitted = fit_multiway_plurality_race(
            registry_row,
            as_of=as_of,
            n_draws=n_draws,
            seed=int(seed) + 17 * (i + 1),
            base_fit=base_fit,
            require_supported=require_supported,
        )
        if fitted is not None:
            fits.append(fitted)
    return fits


def as_fit_result(result: MultiwayPluralityFit) -> FitResult:
    """Encode chamber caucus by sign; keep candidate truth in diagnostics."""
    # Fail-closed / unknown-caucus draws encode as exact 0 margin so
    # (margins >= 0) would incorrectly count Dem; chamber overrides from winners.
    margin = np.where(
        result.fail_closed_draws,
        0.0,
        np.where(result.chamber_dem_win, 1.0, -1.0),
    )[:, None]
    return FitResult(
        race_ids=[result.race_id],
        states=[result.state],
        mean_margin=margin.mean(axis=0),
        sd_margin=margin.std(axis=0),
        draws_margin=margin,
        house_effects={},
        diagnostics={
            "method": MULTIWAY_METHOD,
            "race_id": result.race_id,
            "candidate_ids": result.candidate_ids,
            "candidate_names": result.candidate_names,
            "ballot_parties": result.ballot_parties,
            "caucuses": result.caucuses,
            "winner_indices": result.winner_indices.tolist(),
            "winner_candidate_ids": result.winner_candidate_ids,
            "share_mean": result.share_draws.mean(axis=0).tolist(),
            "fail_closed_draws": result.fail_closed_draws.tolist(),
            "p_unknown_caucus": result.p_unknown_caucus,
            "uncertainty": result.diagnostics,
            "authoritative_binary_aliases": False,
        },
        method=MULTIWAY_METHOD,
    )


def merge_multiway_fit(base: FitResult, multiway: FitResult) -> FitResult:
    if base.draws_margin.shape[0] != multiway.draws_margin.shape[0]:
        raise ValueError("multiway and base draws are not aligned")
    overlap = set(base.race_ids) & set(multiway.race_ids)
    if overlap:
        raise ValueError(f"multiway race already exists in base fit: {sorted(overlap)}")
    draws = np.column_stack([base.draws_margin, multiway.draws_margin])
    existing = (base.diagnostics or {}).get("multiway_plurality_adapter")
    if existing is None:
        multiway_block: Any = multiway.diagnostics
    elif isinstance(existing, list):
        multiway_block = [*existing, multiway.diagnostics]
    else:
        multiway_block = [existing, multiway.diagnostics]
    return FitResult(
        race_ids=[*base.race_ids, *multiway.race_ids],
        states=[*base.states, *multiway.states],
        mean_margin=draws.mean(axis=0),
        sd_margin=draws.std(axis=0),
        draws_margin=draws,
        house_effects=dict(base.house_effects),
        diagnostics={
            **(base.diagnostics or {}),
            "multiway_plurality_adapter": multiway_block,
            "ordinary_columns_preserved": True,
        },
        method=f"{base.method}+{MULTIWAY_METHOD}",
    )


def merge_multiway_fits(base: FitResult, fits: list[MultiwayPluralityFit]) -> FitResult:
    out = base
    for fitted in fits:
        out = merge_multiway_fit(out, as_fit_result(fitted))
    return out
