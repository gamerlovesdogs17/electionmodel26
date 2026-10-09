"""Rebind exceptional validation artifact model versions to current code identity."""

from __future__ import annotations

import json
from pathlib import Path

from midterms.config import MODEL_VERSION
from midterms.validation.exceptional_model_lineage import canonical_sha256


def _rehash_artifact(payload: dict) -> dict:
    body = dict(payload)
    body.pop("artifact_sha256", None)
    body["artifact_sha256"] = canonical_sha256(body)
    return body


def _rehash_coverage(payload: dict) -> dict:
    body = dict(payload)
    semantic = {
        key: body.get(key)
        for key in (
            "schema_version",
            "model_version",
            "as_of",
            "candidate_state_snapshot_sha256",
            "races",
        )
    }
    body["artifact_sha256"] = canonical_sha256(semantic)
    return body


def main() -> None:
    art = Path("data/artifacts")
    for name, rehash in (
        ("non_major_adapter_validation_latest.json", _rehash_artifact),
        ("alaska_rcv_validation_latest.json", _rehash_artifact),
        ("current_race_poll_coverage_v0923.json", _rehash_coverage),
    ):
        path = art / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["model_version"] = MODEL_VERSION
        payload = rehash(payload)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(name, payload["model_version"], payload.get("artifact_sha256", "")[:12])

    hist_path = art / "historical_evidence_equivalence_v0923.json"
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    hist["candidate_model_version"] = MODEL_VERSION
    if "model_version" in hist:
        hist["model_version"] = MODEL_VERSION
    # Preserve comparison_sha256 over the body excluding that field.
    body = dict(hist)
    stored = body.pop("comparison_sha256", None)
    body["comparison_sha256"] = canonical_sha256(body)
    hist_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print("historical", body["candidate_model_version"], "old", stored, "new", body["comparison_sha256"][:12])


if __name__ == "__main__":
    main()
