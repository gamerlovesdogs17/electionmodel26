"""Joint proper scores from already-frozen, draw-aligned OOF distributions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION
from midterms.validation.metrics import score_joint_draws

JOINT_OOF_SCORE_VERSION = "joint-oof-scores-v1"


def write_joint_oof_scores(
    *, nested_path: Path | None = None, out_path: Path | None = None,
) -> dict[str, Any]:
    nested_path = nested_path or (ARTIFACTS_DIR / "nested_component_loo.json")
    if not nested_path.exists():
        raise FileNotFoundError("nested OOF artifact is required")
    nested = json.loads(nested_path.read_text(encoding="utf-8"))
    if nested.get("model_version") != MODEL_VERSION:
        raise ValueError("joint OOF scores require a current-model-version nested artifact")
    draws_by_model = nested.get("oof_draws") or {}
    truths = nested.get("oof_truths") or {}
    rows: list[dict[str, Any]] = []
    for component, cases in sorted(draws_by_model.items()):
        groups: dict[tuple[str, str], list[str]] = {}
        for case_id in cases:
            parts = str(case_id).split(":", 2)
            if len(parts) != 3:
                raise ValueError(f"invalid frozen OOF case id: {case_id}")
            groups.setdefault((parts[0], parts[1]), []).append(case_id)
        for (year, lead), case_ids in sorted(groups.items()):
            ordered = sorted(case_ids, key=lambda value: value.split(":", 2)[2])
            if any(case_id not in truths for case_id in ordered):
                raise ValueError("joint OOF score truth set differs from frozen prediction set")
            lengths = {len(cases[case_id]) for case_id in ordered}
            if len(lengths) != 1 or not lengths or min(lengths) < 2:
                raise ValueError("joint OOF draws must be aligned and nonempty")
            matrix = np.column_stack([
                np.asarray(cases[case_id], dtype=float) for case_id in ordered
            ])
            observed = np.asarray([truths[case_id] for case_id in ordered], dtype=float)
            race_ids = [case_id.split(":", 2)[2] for case_id in ordered]
            score = score_joint_draws(
                matrix, observed, race_ids=race_ids, observed_race_ids=race_ids,
                seat_draws=np.sum(matrix > 0.0, axis=1),
                observed_seats=int(np.sum(observed > 0.0)),
                seed=20260922,
            )
            rows.append({
                "component": component, "holdout_year": int(year),
                "lead_days": int(lead), "n_races": len(race_ids),
                "race_order_sha256": hashlib.sha256(
                    json.dumps(race_ids, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "scores": score,
            })
    payload = {
        "schema_version": JOINT_OOF_SCORE_VERSION,
        "model_version": MODEL_VERSION,
        "source_nested_sha256": hashlib.sha256(nested_path.read_bytes()).hexdigest(),
        "source_frozen_draws_sha256": nested.get("frozen_draws_sha256"),
        "draw_alignment_preserved": True,
        "rows": rows,
        "ok": bool(rows),
    }
    out_path = out_path or (ARTIFACTS_DIR / "joint_oof_scores_latest.json")
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
