"""One-shot coverage artifact patch for multiway fail-closed races."""

from __future__ import annotations

import json
from pathlib import Path

from midterms.config import MODEL_VERSION
from midterms.evidence.current_candidates import load_current_candidate_registry
from midterms.validation.exceptional_model_lineage import canonical_sha256


def main() -> None:
    path = Path("data/artifacts/current_race_poll_coverage_v0923.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    registry = {r["race_id"]: r for r in load_current_candidate_registry()["races"]}
    for row in payload["races"]:
        rid = row["race_id"]
        reg = registry.get(rid)
        if not reg:
            continue
        structure = reg.get("contest_structure")
        if structure:
            row["contest_structure"] = structure
        support = reg.get("probability_model_support_status")
        if support:
            row["probability_model_support_status"] = support
        if structure == "multiway_plurality" or support == "unsupported":
            row["probability_model_supported"] = False
            row["exception_adapter_supported"] = False
            row["ordinary_binary_model_supported"] = False
            row["statistical_target_supported"] = False
            row["forecast_status"] = "fail"
            row["status"] = "fail"
            reasons = set(row.get("reasons") or [])
            reasons.add("unsupported_multiway_plurality_fail_closed")
            row["reasons"] = sorted(reasons)
        elif structure == "non_major_party_vs_republican":
            row["exception_adapter_supported"] = True
            row["probability_model_supported"] = True
    payload["model_version"] = MODEL_VERSION
    payload["summary"] = {
        "n_races": len(payload["races"]),
        "n_pass": sum(r.get("forecast_status") == "pass" for r in payload["races"]),
        "n_warning": sum(r.get("forecast_status") == "warning" for r in payload["races"]),
        "n_fail": sum(r.get("forecast_status") == "fail" for r in payload["races"]),
        "n_evidence_fail": sum(r.get("evidence_status") == "fail" for r in payload["races"]),
        "evidence_ready": not any(r.get("evidence_status") == "fail" for r in payload["races"]),
        "forecast_complete": not any(r.get("forecast_status") == "fail" for r in payload["races"]),
        "promotion_eligible": not any(r.get("forecast_status") == "fail" for r in payload["races"]),
        "source_and_model_coverage_separated": True,
    }
    semantic = {
        k: payload.get(k)
        for k in (
            "schema_version",
            "model_version",
            "as_of",
            "candidate_state_snapshot_sha256",
            "races",
        )
    }
    payload["artifact_sha256"] = canonical_sha256(semantic)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("summary", payload["summary"])
    for rid in [
        "senate-2026-NE",
        "senate-2026-ID",
        "senate-2026-MT",
        "senate-2026-SD",
    ]:
        row = next(x for x in payload["races"] if x["race_id"] == rid)
        print(
            rid,
            row["contest_structure"],
            row["probability_model_supported"],
            row["forecast_status"],
        )


if __name__ == "__main__":
    main()
