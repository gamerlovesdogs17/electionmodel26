"""Publish validation report artifact (blueprint §12.1 / Appendix B)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import ARTIFACTS_DIR, MODEL_VERSION, PRIMARY_HOLDOUT


def build_validation_report(*, quick: bool = True) -> dict[str, Any]:
    """
    Assemble lead-time grid + component ablation + nested df/era into one report.
    `quick=True` uses fewer draws for CI friendliness.
    """
    from midterms.validation.ablations import run_component_ablations
    from midterms.validation.lead_time_grid import nested_df_era_search, replay_lead_time_grid

    draws = 150 if quick else 400
    lead = replay_lead_time_grid(year=PRIMARY_HOLDOUT, lead_days=(90, 60, 30, 7), draws=draws)
    abl = run_component_ablations(year=PRIMARY_HOLDOUT, lead_days=60, draws=draws)
    nested = nested_df_era_search(holdout_year=PRIMARY_HOLDOUT, draws=max(100, draws // 2))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "primary_holdout": PRIMARY_HOLDOUT,
        "lead_time_grid": lead,
        "component_ablations": abl,
        "nested_df_era": nested,
        "limitations": [
            "Historical polls remain partly synthetic fixtures sealed into a redistributable archive.",
            "Licensed expert-rating feeds are not redistributed; curated snapshots only.",
            "House / Electoral College intentionally out of scope.",
        ],
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "validation_report_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    # Markdown summary
    md = [
        f"# Validation report — {MODEL_VERSION}",
        "",
        f"Generated: {report['generated_at']}",
        f"Primary holdout: {PRIMARY_HOLDOUT}",
        "",
        "## Lead-time grid (CRPS)",
    ]
    for row in lead.get("by_lead") or []:
        crps = (row.get("scores") or {}).get("crps")
        md.append(f"- lead={row['lead_days']}: CRPS={crps}")
    md += ["", "## Nested Student-t / era selection", f"- selected: {nested.get('selected')}", f"- holdout CRPS: {nested.get('holdout_crps')}", ""]
    md_path = ARTIFACTS_DIR / "validation_report_latest.md"
    md_path.write_text("\n".join(md))
    report["paths"] = {"json": str(path), "markdown": str(md_path)}
    return report
